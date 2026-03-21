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

import asyncio
import json
import os
import re
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from anima.utils.logging import get_logger

log = get_logger("llm_providers")


def _fix_api_messages(messages: list[dict]) -> list[dict]:
    """Merge consecutive same-role messages for Anthropic API compliance."""
    if not messages:
        return messages
    fixed = [messages[0]]
    for msg in messages[1:]:
        if msg["role"] == fixed[-1]["role"]:
            # Merge content
            prev_content = fixed[-1].get("content", "")
            new_content = msg.get("content", "")
            if isinstance(prev_content, str) and isinstance(new_content, str):
                fixed[-1]["content"] = prev_content + "\n\n" + new_content
            else:
                fixed.append(msg)  # Can't merge non-string content
        else:
            fixed.append(msg)
    return fixed


# ═══════════════════════════════════════════════════════════════════════════
#  Anthropic SDK (optional — graceful fallback to httpx if not installed)
# ═══════════════════════════════════════════════════════════════════════════

try:
    from anthropic import AsyncAnthropic
    HAS_ANTHROPIC_SDK = True
except ImportError:
    HAS_ANTHROPIC_SDK = False

_anthropic_client: Any = None


def _get_anthropic_client():
    """Get or create the Anthropic SDK client singleton.

    Benefits over raw httpx: auto-retry (429/500/502/503/529),
    connection pooling, proper error types, streaming support.
    Returns None if SDK not installed.
    """
    global _anthropic_client
    if not HAS_ANTHROPIC_SDK:
        return None
    if _anthropic_client is not None:
        return _anthropic_client

    # Custom base URL or non-standard token → skip SDK, use httpx path
    if ANTHROPIC_API_BASE != "https://api.anthropic.com":
        return None

    token = _get_anthropic_token()
    if not token:
        return None

    _anthropic_client = AsyncAnthropic(
        api_key=token if not _is_oauth_token(token) else None,
        # OAuth tokens need manual header injection — SDK doesn't support OAuth natively
        # Fall back to httpx for OAuth
        max_retries=2,
    )
    # Only use SDK for API key auth (not OAuth which needs special headers)
    if _is_oauth_token(token):
        _anthropic_client = None
        return None

    log.info("Using Anthropic SDK (auto-retry, connection pooling)")
    return _anthropic_client


# ═══════════════════════════════════════════════════════════════════════════
#  Anthropic constants (used by httpx fallback path)
# ═══════════════════════════════════════════════════════════════════════════

ANTHROPIC_API_BASE = (os.environ.get("ANTHROPIC_BASE_URL", "").strip() or "https://api.anthropic.com").rstrip("/")
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

        # Check if server is already running externally (e.g. started by evolution/user)
        base = os.environ.get("LOCAL_LLM_BASE_URL", _LOCAL_LLM_BASE)
        try:
            async with httpx.AsyncClient(timeout=2) as c:
                r = await c.get(f"{base}/health")
                if r.status_code == 200:
                    self._last_used = time.time()
                    self._externally_managed = True
                    log.info("Local LLM server already running externally")
                    return True
        except Exception:
            pass
        self._externally_managed = False

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
        """Stop server if idle for too long. Called periodically.

        Handles both self-started and externally-started servers.
        """
        if self._last_used == 0:
            return  # never used

        idle_timeout = int(os.environ.get("LOCAL_LLM_IDLE_TIMEOUT", "300"))
        if time.time() - self._last_used <= idle_timeout:
            return  # still within active window

        # Self-started: terminate process
        if self.is_running:
            log.info("Local LLM idle for %ds — shutting down", idle_timeout)
            self.stop()
            return

        # Externally-started: kill by port if still running
        if getattr(self, "_externally_managed", False):
            try:
                import subprocess
                base = os.environ.get("LOCAL_LLM_BASE_URL", _LOCAL_LLM_BASE)
                with httpx.Client(timeout=2) as client:
                    r = client.get(f"{base}/health")
                if r.status_code == 200:
                    # Find and kill the process on our port
                    result = subprocess.run(
                        ["netstat", "-ano"], capture_output=True, text=True, timeout=5
                    )
                    for line in result.stdout.split("\n"):
                        if f":{self._port}" in line and "LISTENING" in line:
                            pid = line.strip().split()[-1]
                            subprocess.run(["taskkill", "/F", "/PID", pid],
                                           capture_output=True, timeout=5)
                            log.info("Killed externally-managed llama-server PID %s (idle %ds)", pid, idle_timeout)
                            self._externally_managed = False
                            break
            except Exception as e:
                log.debug("External server idle check: %s", e)

    def mark_used(self):
        self._last_used = time.time()


# Singleton
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
    """Anthropic auth token. Priority: env AUTH_TOKEN > env OAUTH > credentials > env APIKEY."""
    return (
        os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
        or os.environ.get("ANTHROPIC_OAUTH_TOKEN", "").strip()
        or _load_token_from_credentials()
        or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )


# Expose for router usage tracking
_get_token = _get_anthropic_token


def _is_oauth_token(token: str) -> bool:
    return token.startswith("sk-ant-oat")


# ═══════════════════════════════════════════════════════════════════════════
#  Codex OAuth — ChatGPT subscription via Responses API
# ═══════════════════════════════════════════════════════════════════════════

_CODEX_AUTH_PATH = Path.home() / ".codex" / "auth.json"
_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_CODEX_REFRESH_URL = "https://auth.openai.com/oauth/token"
_codex_tokens: dict = {}
_codex_last_load: float = 0


def _load_codex_tokens() -> dict:
    """Load Codex OAuth tokens from ~/.codex/auth.json."""
    global _codex_tokens, _codex_last_load
    if time.time() - _codex_last_load < 60 and _codex_tokens:
        return _codex_tokens
    if not _CODEX_AUTH_PATH.exists():
        return {}
    try:
        data = json.loads(_CODEX_AUTH_PATH.read_text(encoding="utf-8"))
        if data.get("auth_mode") != "chatgpt":
            return {}
        _codex_tokens = data.get("tokens", {})
        _codex_last_load = time.time()
        return _codex_tokens
    except Exception as e:
        log.warning("Failed to read Codex auth: %s", e)
        return {}


def _codex_token_expired(tokens: dict) -> bool:
    """Check if the Codex access token is expired or near expiry (5min buffer)."""
    access = tokens.get("access_token", "")
    if not access:
        return True
    try:
        import base64
        payload = access.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return time.time() > (claims.get("exp", 0) - 300)
    except Exception:
        return False


async def _refresh_codex_token() -> dict:
    """Refresh the Codex OAuth access token via auth.openai.com."""
    global _codex_tokens
    tokens = _load_codex_tokens()
    refresh = tokens.get("refresh_token", "")
    if not refresh:
        log.warning("No Codex refresh token available")
        return {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _CODEX_REFRESH_URL,
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "client_id": _CODEX_CLIENT_ID,
                    "scope": "openid profile email offline_access",
                },
            )
        if resp.status_code != 200:
            log.warning("Codex token refresh failed: %d %s", resp.status_code, resp.text[:200])
            return {}
        new_data = resp.json()
        tokens["access_token"] = new_data["access_token"]
        tokens["id_token"] = new_data.get("id_token", tokens.get("id_token", ""))
        if new_data.get("refresh_token"):
            tokens["refresh_token"] = new_data["refresh_token"]
        # Write back to auth.json
        auth_data = json.loads(_CODEX_AUTH_PATH.read_text(encoding="utf-8"))
        auth_data["tokens"] = tokens
        auth_data["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        _CODEX_AUTH_PATH.write_text(json.dumps(auth_data, indent=2), encoding="utf-8")
        _codex_tokens = tokens
        log.info("Codex OAuth token refreshed successfully")
        return tokens
    except Exception as e:
        log.warning("Codex token refresh error: %s", e)
        return {}


async def _codex_completion(
    model: str,
    messages: list[dict],
    max_tokens: int = 4096,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> dict:
    """Call OpenAI via Codex OAuth (Responses API at chatgpt.com).

    Converts from Anthropic Messages format to OpenAI Responses API format,
    sends to chatgpt.com/backend-api/codex/responses, and converts back.
    """
    tokens = _load_codex_tokens()
    if not tokens:
        raise RuntimeError("No Codex OAuth tokens (check ~/.codex/auth.json)")

    if _codex_token_expired(tokens):
        tokens = await _refresh_codex_token()
        if not tokens:
            raise RuntimeError("Codex token refresh failed")

    access_token = tokens.get("access_token", "")
    account_id = tokens.get("account_id", "")

    if model.startswith("codex/"):
        model = model[len("codex/"):]

    # Convert messages → Responses API format
    instructions = ""
    input_items: list[dict] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            instructions += (content if isinstance(content, str) else str(content)) + "\n"
            continue

        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    if block:
                        input_items.append({"role": role, "content": str(block)})
                    continue
                btype = block.get("type", "")
                if btype == "tool_use":
                    input_items.append({
                        "type": "function_call",
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                        "call_id": block.get("id", ""),
                    })
                elif btype == "tool_result":
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": block.get("tool_use_id", ""),
                        "output": str(block.get("content", "")),
                    })
                elif btype == "text":
                    text = block.get("text", "")
                    if text:
                        input_items.append({"role": role, "content": text})
                else:
                    text = block.get("text", block.get("content", ""))
                    if text:
                        input_items.append({"role": role, "content": str(text)})
        elif content:
            input_items.append({"role": role, "content": str(content)})

    # Build payload
    payload: dict[str, Any] = {
        "model": model,
        "instructions": instructions.strip(),
        "input": input_items,
        # Note: Codex endpoint doesn't support max_output_tokens or temperature
        "stream": False,
        "store": False,
    }

    if tools:
        payload["tools"] = [
            {
                "type": "function",
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", t.get("parameters", {})),
            }
            for t in tools
        ]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "ChatGPT-Account-ID": account_id,
        "Content-Type": "application/json",
    }

    # Codex requires streaming — collect SSE events into final response
    payload["stream"] = True
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    _timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)

    async def _stream_request(hdrs: dict) -> dict:
        content_parts: list[str] = []
        tool_calls_out: list[dict] = []
        usage_out: dict = {}

        async with httpx.AsyncClient(timeout=_timeout) as client:
            async with client.stream(
                "POST", f"{_CODEX_BASE_URL}/responses",
                headers=hdrs, content=body,
            ) as resp:
                if resp.status_code != 200:
                    error_body = ""
                    async for chunk in resp.aiter_text():
                        error_body += chunk
                        if len(error_body) > 500:
                            break
                    raise RuntimeError(
                        f"Codex API error {resp.status_code}: {error_body[:500]}"
                    )

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if not data_str:
                        continue
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type", "")

                    # Text output
                    if etype == "response.output_text.delta":
                        content_parts.append(event.get("delta", ""))
                    # Function call completed (collect from response.completed instead
                    # to avoid duplicates — intermediate events lack the name field)
                    # Response completed — extract usage
                    elif etype == "response.completed":
                        r = event.get("response", {})
                        u = r.get("usage", {})
                        usage_out.update({
                            "prompt_tokens": u.get("input_tokens", 0),
                            "completion_tokens": u.get("output_tokens", 0),
                        })
                        # Also extract any output we might have missed
                        for item in r.get("output", []):
                            if item.get("type") == "message":
                                for block in item.get("content", []):
                                    if block.get("type") == "output_text":
                                        t = block.get("text", "")
                                        if t and t not in "".join(content_parts):
                                            content_parts.append(t)
                            elif item.get("type") == "function_call":
                                tc = {
                                    "id": item.get("call_id", item.get("id", "")),
                                    "name": item.get("name", ""),
                                    "arguments": item.get("arguments", "{}"),
                                }
                                if tc not in tool_calls_out:
                                    tool_calls_out.append(tc)

        return {
            "content": "".join(content_parts),
            "tool_calls": tool_calls_out,
            "usage": usage_out,
            "model": model if not model.startswith("codex/") else model[6:],
        }

    try:
        return await _stream_request(headers)
    except RuntimeError as e:
        if "401" in str(e):
            tokens = await _refresh_codex_token()
            if tokens:
                headers["Authorization"] = f"Bearer {tokens['access_token']}"
                return await _stream_request(headers)
        raise


def _parse_codex_response(data: dict, model: str) -> dict:
    """Parse Responses API response into standard format."""
    content = ""
    tool_calls = []
    for item in data.get("output", []):
        itype = item.get("type", "")
        if itype == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    content += block.get("text", "")
        elif itype == "function_call":
            tool_calls.append({
                "id": item.get("call_id", item.get("id", "")),
                "name": item.get("name", ""),
                "arguments": item.get("arguments", "{}"),
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
    if model.startswith("codex/"):
        return await _codex_completion(
            model=model, messages=messages, max_tokens=max_tokens,
            temperature=temperature, tools=tools,
        )

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

    # Default: Anthropic — try SDK first, fall back to httpx
    client = _get_anthropic_client()
    if client is not None:
        return await _anthropic_sdk_completion(
            client=client, model=model, messages=messages,
            max_tokens=max_tokens, temperature=temperature, tools=tools,
        )
    return await _anthropic_completion(
        model=model, messages=messages, max_tokens=max_tokens,
        temperature=temperature, tools=tools,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Format 1a: Anthropic SDK (preferred when installed + API key auth)
# ═══════════════════════════════════════════════════════════════════════════

async def _anthropic_sdk_completion(
    client: Any,
    model: str,
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> dict:
    """Call Anthropic via the official SDK. Auto-retry, connection pooling."""
    if model.startswith("anthropic/"):
        model = model[len("anthropic/"):]

    # Separate system from conversation
    system_prompt = None
    api_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_prompt = msg["content"]
        else:
            content = msg.get("content", "")
            if isinstance(content, str) and not content.strip():
                continue
            if isinstance(content, list) and not content:
                continue
            api_messages.append(msg)

    api_messages = _fix_api_messages(api_messages)

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": api_messages,
    }
    if system_prompt:
        kwargs["system"] = system_prompt
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = {"type": "auto"}

    response = await client.messages.create(**kwargs)

    # Parse SDK response to standard format
    content_text = ""
    tool_calls = []
    for block in response.content:
        if block.type == "text":
            content_text += block.text
        elif block.type == "tool_use":
            tool_calls.append({
                "id": block.id,
                "name": block.name,
                "arguments": json.dumps(block.input),
            })

    return {
        "content": content_text,
        "tool_calls": tool_calls,
        "usage": {
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
        },
        "model": model,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Format 1b: Anthropic httpx fallback (OAuth tokens, SDK not installed)
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

    # Separate system from conversation messages, filter empty content
    system_prompt = None
    api_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_prompt = msg["content"]
        else:
            content = msg.get("content", "")
            if isinstance(content, str) and not content.strip():
                continue  # Anthropic rejects empty user/assistant messages
            if isinstance(content, list) and not content:
                continue
            api_messages.append(msg)

    # H-21 fix: merge consecutive same-role messages before sending
    api_messages = _fix_api_messages(api_messages)

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

    # Granular timeouts: connect=10s, read=90s (kills half-open hung connections),
    # write=30s, pool=10s. Total budget ~90s, not 120s of silent hang.
    _anthropic_timeout = httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=_anthropic_timeout) as client:
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

    # Local models can be slow (large prompt processing) but should never hang silently.
    # read=180s is generous for local generation; connect should be instant.
    _openai_timeout = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=_openai_timeout) as client:
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


# ═══════════════════════════════════════════════════════════════════════════
#  Streaming API — H-03 fix
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class StreamEvent:
    """A single event from a streaming LLM response.

    Event types:
      - text_delta:      Incremental text content (chunk in .text)
      - tool_use_start:  A tool call has started (tool name/id in .tool_call)
      - tool_input_delta: Incremental JSON for tool input
      - tool_use_done:   Tool input is complete
      - message_complete: Full response is done (.content, .tool_calls, .usage populated)
      - error:           An error occurred (.error populated)
    """
    type: str
    text: str = ""
    tool_call: dict = field(default_factory=dict)
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""
    error: str = ""


async def completion_stream(
    model: str,
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Streaming completion — yields StreamEvent objects as they arrive.

    Same routing logic as completion(), but returns an async generator
    of StreamEvent instead of a single dict.

    Falls back to non-streaming (yields a single message_complete event)
    if the provider doesn't support streaming.
    """
    if model.startswith("local/"):
        base = os.environ.get("LOCAL_LLM_BASE_URL", _LOCAL_LLM_BASE)
        model_id = model.removeprefix("local/").strip() or None
        if not await _local_server.ensure_running(timeout=60):
            yield StreamEvent(type="error", error="Local LLM server failed to start")
            return
        _local_server.mark_used()
        async for event in _openai_completion_stream(
            base_url=base, model_id=model_id, api_key=None,
            messages=messages, max_tokens=max_tokens,
            temperature=temperature, tools=tools,
        ):
            yield event
        return

    if model.startswith("openai/"):
        base = os.environ.get("OPENAI_API_BASE", _OPENAI_API_BASE)
        model_id = model.removeprefix("openai/").strip()
        api_key = os.environ.get("OPENAI_API_KEY", "")
        async for event in _openai_completion_stream(
            base_url=base, model_id=model_id, api_key=api_key,
            messages=messages, max_tokens=max_tokens,
            temperature=temperature, tools=tools,
        ):
            yield event
        return

    # Default: Anthropic streaming
    async for event in _anthropic_completion_stream(
        model=model, messages=messages, max_tokens=max_tokens,
        temperature=temperature, tools=tools,
    ):
        yield event


# ── Anthropic streaming ──

async def _anthropic_completion_stream(
    model: str,
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Stream from Anthropic Messages API via SSE."""
    token = _get_anthropic_token()
    if not token:
        yield StreamEvent(type="error", error="No Anthropic auth token")
        return

    if model.startswith("anthropic/"):
        model = model[len("anthropic/"):]

    is_oauth = _is_oauth_token(token)

    # Build headers (same as non-streaming)
    if is_oauth:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "anthropic-version": ANTHROPIC_VERSION,
            "anthropic-beta": OAUTH_BETA_HEADERS,
            "user-agent": f"claude-cli/{CLAUDE_CODE_VERSION}",
            "x-app": "cli",
            "anthropic-dangerous-direct-browser-access": "true",
            "accept": "text/event-stream",
        }
    else:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": token,
            "anthropic-version": ANTHROPIC_VERSION,
            "accept": "text/event-stream",
        }

    # Build messages (same as non-streaming)
    system_prompt = None
    api_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_prompt = msg["content"]
        else:
            content = msg.get("content", "")
            if isinstance(content, str) and not content.strip():
                continue
            if isinstance(content, list) and not content:
                continue
            api_messages.append(msg)

    api_messages = _fix_api_messages(api_messages)

    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": api_messages,
        "stream": True,
    }

    if is_oauth:
        blocks = [{"type": "text", "text": CLAUDE_CODE_IDENTITY}]
        if system_prompt:
            blocks.append({"type": "text", "text": system_prompt})
        payload["system"] = blocks
    elif system_prompt:
        payload["system"] = system_prompt

    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = {"type": "auto"}

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    # Streaming needs longer read timeout (response comes in chunks over minutes)
    _stream_timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

    # Accumulate full response for the final message_complete event
    full_content = ""
    tool_calls: list[dict] = []
    current_tool: dict = {}
    current_tool_input = ""
    usage_data: dict = {}

    try:
        async with httpx.AsyncClient(timeout=_stream_timeout) as client:
            async with client.stream(
                "POST",
                f"{ANTHROPIC_API_BASE}/v1/messages",
                headers=headers,
                content=body,
            ) as response:
                if response.status_code != 200:
                    error_body = ""
                    async for chunk in response.aiter_text():
                        error_body += chunk
                        if len(error_body) > 500:
                            break
                    yield StreamEvent(
                        type="error",
                        error=f"Anthropic API error {response.status_code}: {error_body[:500]}",
                    )
                    return

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = data.get("type", "")

                    if event_type == "content_block_start":
                        block = data.get("content_block", {})
                        if block.get("type") == "tool_use":
                            current_tool = {
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                            }
                            current_tool_input = ""
                            yield StreamEvent(
                                type="tool_use_start",
                                tool_call={"id": current_tool["id"], "name": current_tool["name"]},
                            )

                    elif event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        delta_type = delta.get("type", "")

                        if delta_type == "text_delta":
                            text = delta.get("text", "")
                            full_content += text
                            yield StreamEvent(type="text_delta", text=text)

                        elif delta_type == "input_json_delta":
                            partial = delta.get("partial_json", "")
                            current_tool_input += partial
                            yield StreamEvent(type="tool_input_delta", text=partial)

                    elif event_type == "content_block_stop":
                        if current_tool:
                            try:
                                args = json.loads(current_tool_input) if current_tool_input else {}
                            except json.JSONDecodeError:
                                args = {}
                            tool_calls.append({
                                "id": current_tool["id"],
                                "name": current_tool["name"],
                                "arguments": json.dumps(args),
                            })
                            yield StreamEvent(
                                type="tool_use_done",
                                tool_call={
                                    "id": current_tool["id"],
                                    "name": current_tool["name"],
                                    "arguments": json.dumps(args),
                                },
                            )
                            current_tool = {}
                            current_tool_input = ""

                    elif event_type == "message_delta":
                        delta_usage = data.get("usage", {})
                        if delta_usage:
                            usage_data["completion_tokens"] = delta_usage.get("output_tokens", 0)

                    elif event_type == "message_start":
                        msg_usage = data.get("message", {}).get("usage", {})
                        if msg_usage:
                            usage_data["prompt_tokens"] = msg_usage.get("input_tokens", 0)

                    elif event_type == "error":
                        yield StreamEvent(
                            type="error",
                            error=data.get("error", {}).get("message", "Unknown streaming error"),
                        )
                        return

        # Final message_complete event
        yield StreamEvent(
            type="message_complete",
            content=full_content,
            tool_calls=tool_calls,
            usage={
                "prompt_tokens": usage_data.get("prompt_tokens", 0),
                "completion_tokens": usage_data.get("completion_tokens", 0),
            },
            model=model,
        )

    except httpx.ReadTimeout:
        yield StreamEvent(type="error", error=f"Anthropic streaming read timeout ({model})")
    except httpx.ConnectTimeout:
        yield StreamEvent(type="error", error=f"Anthropic connection timeout ({model})")
    except Exception as e:
        yield StreamEvent(type="error", error=f"Anthropic streaming error: {str(e)[:300]}")


# ── OpenAI-compatible streaming ──

async def _openai_completion_stream(
    base_url: str,
    model_id: str | None,
    api_key: str | None,
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Stream from any OpenAI-compatible endpoint via SSE."""
    api_messages = _anthropic_to_openai_messages(messages)

    payload: dict[str, Any] = {
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    if model_id:
        payload["model"] = model_id

    openai_tools = _convert_tools_to_openai(tools)
    if openai_tools:
        payload["tools"] = openai_tools
        payload["tool_choice"] = "auto"

    h: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    _stream_timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

    full_content = ""
    tool_calls: list[dict] = []
    # OpenAI streams tool calls across multiple chunks — track by index
    tool_call_accum: dict[int, dict] = {}

    try:
        async with httpx.AsyncClient(timeout=_stream_timeout) as client:
            async with client.stream(
                "POST",
                f"{base_url}/v1/chat/completions",
                headers=h,
                content=body,
            ) as response:
                if response.status_code != 200:
                    error_body = ""
                    async for chunk in response.aiter_text():
                        error_body += chunk
                        if len(error_body) > 500:
                            break
                    yield StreamEvent(
                        type="error",
                        error=f"OpenAI API error {response.status_code}: {error_body[:500]}",
                    )
                    return

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = data.get("choices", [])
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {})

                    # Text content
                    if "content" in delta and delta["content"]:
                        text = delta["content"]
                        # Strip <think> blocks from local models
                        if "<think>" not in full_content and "<think>" in text:
                            pass  # will strip at end
                        full_content += text
                        yield StreamEvent(type="text_delta", text=text)

                    # Tool calls (accumulated across chunks)
                    if "tool_calls" in delta:
                        for tc_delta in delta["tool_calls"]:
                            idx = tc_delta.get("index", 0)
                            if idx not in tool_call_accum:
                                tool_call_accum[idx] = {
                                    "id": tc_delta.get("id", ""),
                                    "name": tc_delta.get("function", {}).get("name", ""),
                                    "arguments": "",
                                }
                                yield StreamEvent(
                                    type="tool_use_start",
                                    tool_call={"id": tool_call_accum[idx]["id"],
                                               "name": tool_call_accum[idx]["name"]},
                                )
                            fn_delta = tc_delta.get("function", {})
                            if "arguments" in fn_delta:
                                tool_call_accum[idx]["arguments"] += fn_delta["arguments"]

        # Finalize tool calls
        for idx in sorted(tool_call_accum.keys()):
            tc = tool_call_accum[idx]
            tool_calls.append({
                "id": tc["id"],
                "name": tc["name"],
                "arguments": tc["arguments"],
            })
            yield StreamEvent(
                type="tool_use_done",
                tool_call=tc,
            )

        # Strip <think> blocks from final content
        if "<think>" in full_content:
            full_content = re.sub(
                r"<think>.*?</think>\s*", "", full_content, flags=re.DOTALL
            ).strip()

        yield StreamEvent(
            type="message_complete",
            content=full_content,
            tool_calls=tool_calls,
            usage={},  # OpenAI streaming doesn't always include usage
            model=model_id or "local",
        )

    except httpx.ReadTimeout:
        yield StreamEvent(type="error", error="OpenAI streaming read timeout")
    except Exception as e:
        yield StreamEvent(type="error", error=f"OpenAI streaming error: {str(e)[:300]}")
