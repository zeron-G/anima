"""LLM provider — dual-format completion API.

Two completion formats, three auth modes:

  Format 1: Anthropic Messages API
    - OAuth Token (sk-ant-oat...): Bearer auth + Claude Code identity injection
    - API Key (sk-ant-api...): x-api-key header
    Auth auto-detected. Used for Claude models.

  Format 2: OpenAI Chat Completions API
    - Any OpenAI-compatible server: llama.cpp, ollama, vLLM, OpenAI, etc.
    - Optional API key via OPENAI_API_KEY env var
    Used for local models and OpenAI cloud.

Model prefix routing:
    "local/..."     → OpenAI format, local server (LOCAL_LLM_BASE_URL)
    "openai/..."    → OpenAI format, OpenAI cloud (OPENAI_API_BASE)
    everything else → Anthropic format (auto-detect OAuth vs API key)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

from anima.utils.logging import get_logger

log = get_logger("llm_providers")


# ═══════════════════════════════════════════════════════════════════════════
#  Anthropic constants
# ═══════════════════════════════════════════════════════════════════════════

ANTHROPIC_API_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
CLAUDE_CODE_VERSION = "2.1.76"
OAUTH_BETA_HEADERS = (
    "claude-code-20250219,oauth-2025-04-20,"
    "fine-grained-tool-streaming-2025-05-14"
)
CLAUDE_CODE_IDENTITY = (
    "You are Claude Code, Anthropic's official CLI for Claude."
)
_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


# ═══════════════════════════════════════════════════════════════════════════
#  OpenAI constants
# ═══════════════════════════════════════════════════════════════════════════

_OPENAI_API_BASE = "https://api.openai.com"
_LOCAL_LLM_BASE = "http://localhost:8080"


# ═══════════════════════════════════════════════════════════════════════════
#  Local LLM server lifecycle (on-demand start/stop)
# ═══════════════════════════════════════════════════════════════════════════

class LocalServerManager:
    """Manages llama-server lifecycle — starts on demand, stops after idle.

    Config via environment variables:
        LOCAL_LLM_SERVER_CMD: full command (default: auto-detect from LOCAL_LLM_SERVER_PATH)
        LOCAL_LLM_SERVER_PATH: path to llama-server executable
        LOCAL_LLM_MODEL_PATH: path to .gguf model file
        LOCAL_LLM_BASE_URL: server URL (default: http://localhost:8080)
        LOCAL_LLM_GPU_LAYERS: number of GPU layers (default: 99 = all)
        LOCAL_LLM_CTX_SIZE: context size (default: 65536)
        LOCAL_LLM_IDLE_TIMEOUT: seconds before auto-shutdown (default: 300)
    """

    def __init__(self):
        self._process: Any = None
        self._last_used: float = 0
        self._starting: bool = False
        self._port: int = 8080

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _get_server_cmd(self) -> list[str] | None:
        """Build llama-server command from env vars."""
        # Option 1: explicit full command
        full_cmd = os.environ.get("LOCAL_LLM_SERVER_CMD", "")
        if full_cmd:
            return full_cmd.split()

        # Option 2: build from path + model
        server = os.environ.get("LOCAL_LLM_SERVER_PATH", "")
        model = os.environ.get("LOCAL_LLM_MODEL_PATH", "")
        if not server or not model:
            return None

        ngl = os.environ.get("LOCAL_LLM_GPU_LAYERS", "99")
        ctx = os.environ.get("LOCAL_LLM_CTX_SIZE", "65536")
        base = os.environ.get("LOCAL_LLM_BASE_URL", _LOCAL_LLM_BASE)
        # Extract port from base URL
        try:
            from urllib.parse import urlparse
            self._port = urlparse(base).port or 8080
        except Exception:
            self._port = 8080

        return [
            server, "-m", model,
            "-ngl", ngl,
            "--port", str(self._port),
            "-c", ctx,
        ]

    async def ensure_running(self, timeout: float = 30) -> bool:
        """Start server if not running. Returns True if server is ready."""
        if self.is_running:
            self._last_used = time.time()
            return True

        if self._starting:
            # Another call is starting it — wait
            for _ in range(int(timeout * 2)):
                await asyncio.sleep(0.5)
                if self.is_running:
                    self._last_used = time.time()
                    return True
            return False

        cmd = self._get_server_cmd()
        if not cmd:
            return False

        self._starting = True
        try:
            import subprocess
            log.info("Starting local LLM server: %s", " ".join(cmd[:3]) + "...")
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            # Wait for server to be ready
            base = os.environ.get("LOCAL_LLM_BASE_URL", _LOCAL_LLM_BASE)
            start = time.time()
            while time.time() - start < timeout:
                try:
                    async with httpx.AsyncClient(timeout=2) as c:
                        r = await c.get(f"{base}/health")
                        if r.status_code == 200:
                            self._last_used = time.time()
                            log.info("Local LLM server ready (%.1fs)", time.time() - start)
                            return True
                except Exception:
                    pass
                await asyncio.sleep(1)

            log.warning("Local LLM server failed to start within %ds", timeout)
            self.stop()
            return False
        finally:
            self._starting = False

    def stop(self):
        """Stop the local server."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            log.info("Local LLM server stopped")
            self._process = None

    def check_idle_shutdown(self):
        """Stop server if idle for too long. Called periodically."""
        if not self.is_running:
            return
        idle_timeout = int(os.environ.get("LOCAL_LLM_IDLE_TIMEOUT", "300"))
        if time.time() - self._last_used > idle_timeout:
            log.info("Local LLM idle for %ds — shutting down", idle_timeout)
            self.stop()

    def mark_used(self):
        self._last_used = time.time()


# Singleton
import asyncio as _asyncio
import time as _time

_local_server = LocalServerManager()


# ═══════════════════════════════════════════════════════════════════════════
#  Auth helpers (Anthropic)
# ═══════════════════════════════════════════════════════════════════════════

def _load_token_from_credentials() -> str:
    """Try to read OAuth token from Claude Code's credentials."""
    try:
        if _CREDENTIALS_PATH.exists():
            data = json.loads(_CREDENTIALS_PATH.read_text(encoding="utf-8"))
            token = data.get("claudeAiOauth", {}).get("accessToken", "")
            if token:
                log.debug("Loaded OAuth token from credentials")
                return token
    except Exception as e:
        log.debug("Could not read credentials: %s", e)
    return ""


def _get_anthropic_token() -> str:
    """Anthropic auth token. Priority: env OAUTH > credentials > env APIKEY."""
    return (
        os.environ.get("ANTHROPIC_OAUTH_TOKEN", "").strip()
        or _load_token_from_credentials()
        or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )


# Expose for router usage tracking
_get_token = _get_anthropic_token


def _is_oauth_token(token: str) -> bool:
    return "sk-ant-oat" in token


# ═══════════════════════════════════════════════════════════════════════════
#  Unified completion entry point
# ═══════════════════════════════════════════════════════════════════════════

async def completion(
    model: str,
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> dict:
    """Route to the correct provider based on model prefix.

    Returns standardized response:
        {"content": str, "tool_calls": list, "usage": dict, "model": str}
    """
    if model.startswith("local/"):
        base = os.environ.get("LOCAL_LLM_BASE_URL", _LOCAL_LLM_BASE)
        model_id = model.removeprefix("local/").strip() or None
        # On-demand: start server if not running
        if not await _local_server.ensure_running(timeout=60):
            raise RuntimeError("Local LLM server failed to start")
        _local_server.mark_used()
        return await _openai_completion(
            base_url=base, model_id=model_id, api_key=None,
            messages=messages, max_tokens=max_tokens,
            temperature=temperature, tools=tools,
        )

    if model.startswith("openai/"):
        base = os.environ.get("OPENAI_API_BASE", _OPENAI_API_BASE)
        model_id = model.removeprefix("openai/").strip()
        api_key = os.environ.get("OPENAI_API_KEY", "")
        return await _openai_completion(
            base_url=base, model_id=model_id, api_key=api_key,
            messages=messages, max_tokens=max_tokens,
            temperature=temperature, tools=tools,
        )

    # Default: Anthropic
    return await _anthropic_completion(
        model=model, messages=messages, max_tokens=max_tokens,
        temperature=temperature, tools=tools,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Format 1: Anthropic Messages API
# ═══════════════════════════════════════════════════════════════════════════

async def _anthropic_completion(
    model: str,
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> dict:
    """Call Anthropic Messages API. Auto-detects OAuth vs API key."""
    token = _get_anthropic_token()
    if not token:
        raise RuntimeError(
            "No Anthropic auth. Set ANTHROPIC_OAUTH_TOKEN or ANTHROPIC_API_KEY"
        )

    # Strip provider prefix
    if model.startswith("anthropic/"):
        model = model[len("anthropic/"):]

    is_oauth = _is_oauth_token(token)

    # Build headers
    if is_oauth:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "anthropic-version": ANTHROPIC_VERSION,
            "anthropic-beta": OAUTH_BETA_HEADERS,
            "user-agent": f"claude-cli/{CLAUDE_CODE_VERSION}",
            "x-app": "cli",
            "anthropic-dangerous-direct-browser-access": "true",
            "accept": "application/json",
        }
    else:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": token,
            "anthropic-version": ANTHROPIC_VERSION,
            "accept": "application/json",
        }

    # Separate system from conversation messages
    system_prompt = None
    api_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_prompt = msg["content"]
        else:
            api_messages.append(msg)

    # Build payload
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": api_messages,
    }

    if is_oauth:
        # OAuth requires Claude Code identity prefix
        blocks = [{"type": "text", "text": CLAUDE_CODE_IDENTITY}]
        if system_prompt:
            blocks.append({"type": "text", "text": system_prompt})
        payload["system"] = blocks
    elif system_prompt:
        payload["system"] = system_prompt

    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = {"type": "auto"}

    # Serialize JSON as UTF-8 bytes explicitly (Windows GBK locale safety)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if "Content-Type" not in headers:
        headers["Content-Type"] = "application/json; charset=utf-8"

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{ANTHROPIC_API_BASE}/v1/messages",
            headers=headers,
            content=body,
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Anthropic API error {resp.status_code}: {resp.text[:500]}"
        )

    return _parse_anthropic_response(resp.json(), model)


def get_local_server_manager() -> LocalServerManager:
    """Get the singleton local server manager (for idle shutdown checks)."""
    return _local_server


def _parse_anthropic_response(data: dict, model: str) -> dict:
    """Parse Anthropic Messages API response into standard format."""
    content = ""
    tool_calls = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            content += block.get("text", "")
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "name": block["name"],
                "arguments": json.dumps(block.get("input", {})),
            })
    usage = data.get("usage", {})
    return {
        "content": content,
        "tool_calls": tool_calls,
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
        },
        "model": model,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Format 2: OpenAI Chat Completions API
# ═══════════════════════════════════════════════════════════════════════════

async def _openai_completion(
    base_url: str,
    model_id: str | None,
    api_key: str | None,
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> dict:
    """Call any OpenAI-compatible endpoint.

    Used for: local llama.cpp, ollama, vLLM, OpenAI cloud, etc.
    Handles Anthropic→OpenAI message format conversion transparently.
    """
    # Convert messages from Anthropic format to OpenAI format
    api_messages = _anthropic_to_openai_messages(messages)

    payload: dict[str, Any] = {
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    if model_id:
        payload["model"] = model_id

    # Convert tool format
    openai_tools = _convert_tools_to_openai(tools)
    if openai_tools:
        payload["tools"] = openai_tools
        payload["tool_choice"] = "auto"

    headers: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    log.debug("OpenAI-compat call: %s (model=%s)", base_url, model_id or "default")

    # Serialize JSON as UTF-8 bytes explicitly (Windows GBK locale safety)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(
            f"{base_url}/v1/chat/completions",
            headers=headers,
            content=body,
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"OpenAI API error {resp.status_code}: {resp.text[:500]}"
        )

    return _parse_openai_response(resp.json(), model_id or "local")


# ── Message format conversion ──

def _flatten_content(content: Any) -> str:
    """Normalize Anthropic content blocks to plain string.

    Anthropic: [{"type":"text","text":"..."}, {"type":"tool_result",...}]
    OpenAI: plain string
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    parts.append(
                        f"[Tool call: {block.get('name', '?')}"
                        f"({json.dumps(block.get('input', {}))})]"
                    )
                elif btype == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, list):
                        inner = " ".join(
                            b.get("text", "")
                            for b in inner
                            if isinstance(b, dict)
                        )
                    parts.append(f"[Tool result: {inner}]")
                else:
                    parts.append(str(block))
        return "\n".join(p for p in parts if p)
    return str(content) if content else ""


def _anthropic_to_openai_messages(messages: list[dict]) -> list[dict]:
    """Convert Anthropic-format messages to OpenAI-format.

    Handles: content blocks → strings, tool role → user, system merging,
    consecutive same-role merging, empty message removal.
    """
    converted = []
    system_parts: list[str] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = _flatten_content(msg.get("content", ""))
        if not content.strip():
            continue

        if role == "system":
            system_parts.append(content)
        elif role == "tool":
            converted.append({
                "role": "user",
                "content": f"[Tool Result] {content}",
            })
        else:
            converted.append({"role": role, "content": content})

    # Merge consecutive same-role messages
    merged: list[dict] = []
    for msg in converted:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] += "\n\n" + msg["content"]
        else:
            merged.append(msg)

    # Inject system as first user message (most local models handle
    # system role poorly; prepending to first user msg is more reliable)
    if system_parts:
        system_text = "\n\n".join(system_parts)
        if merged and merged[0]["role"] == "user":
            merged[0]["content"] = (
                f"[System Instructions]\n{system_text}\n\n"
                f"[User Message]\n{merged[0]['content']}"
            )
        else:
            merged.insert(0, {"role": "user", "content": system_text})
            # Fix alternation if next is also user
            if len(merged) > 1 and merged[1]["role"] == "user":
                merged[0]["content"] += "\n\n" + merged[1]["content"]
                merged.pop(1)

    if not merged:
        merged = [{"role": "user", "content": "(empty)"}]

    return merged


# ── Tool format conversion ──

def _convert_tools_to_openai(
    tools: list[dict] | None,
) -> list[dict] | None:
    """Convert Anthropic tool schema to OpenAI function-calling format.

    Anthropic: {"name", "description", "input_schema": {...}}
    OpenAI:    {"type":"function", "function": {"name", "description", "parameters"}}
    """
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get(
                    "input_schema", t.get("parameters", {})
                ),
            },
        }
        for t in tools
    ]


# ── Response parsing ──

def _parse_openai_response(data: dict, model_name: str) -> dict:
    """Parse OpenAI Chat Completions response into standard format."""
    choices = data.get("choices", [])
    if not choices:
        return {
            "content": "",
            "tool_calls": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "model": model_name,
        }

    msg = choices[0].get("message", {})
    content = msg.get("content", "") or ""

    # Strip <think>...</think> blocks (Qwen/DeepSeek thinking mode)
    if "<think>" in content:
        content = re.sub(
            r"<think>.*?</think>\s*", "", content, flags=re.DOTALL
        ).strip()

    tool_calls = []
    for tc in msg.get("tool_calls", []):
        fn = tc.get("function", {})
        tool_calls.append({
            "id": tc.get("id", ""),
            "name": fn.get("name", ""),
            "arguments": fn.get("arguments", "{}"),
        })

    usage = data.get("usage", {})
    return {
        "content": content,
        "tool_calls": tool_calls,
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
        },
        "model": model_name,
    }
