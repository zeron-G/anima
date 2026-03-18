"""LLM provider wrapper — supports both API Key and OAuth Token auth.

Auth modes:
- API Key (sk-ant-api03-...): Uses litellm with x-api-key header
- OAuth Token (sk-ant-oat01-...): Direct HTTP with Bearer auth + beta headers
  - Requires Claude Code identity in system prompt
  - Uses subscription quota (not API billing)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from anima.utils.logging import get_logger

log = get_logger("llm_providers")

# ── OAuth constants (from openclaw-auth-analysis) ──

ANTHROPIC_API_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
CLAUDE_CODE_VERSION = "2.1.76"
OAUTH_BETA_HEADERS = "claude-code-20250219,oauth-2025-04-20,fine-grained-tool-streaming-2025-05-14"

# Required system prompt prefix for OAuth token validation
CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."

# Claude Code credentials file (auto-discovery)
_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


def _load_token_from_credentials() -> str:
    """Try to read OAuth token from Claude Code's local credentials file."""
    try:
        if _CREDENTIALS_PATH.exists():
            data = json.loads(_CREDENTIALS_PATH.read_text(encoding="utf-8"))
            oauth = data.get("claudeAiOauth", {})
            token = oauth.get("accessToken", "")
            if token:
                log.debug("Loaded OAuth token from Claude Code credentials")
                return token
    except Exception as e:
        log.debug("Could not read Claude Code credentials: %s", e)
    return ""


def _get_token() -> str:
    """Get auth token. Priority: env OAUTH > credentials OAuth > env APIKEY."""
    return (
        os.environ.get("ANTHROPIC_OAUTH_TOKEN", "").strip()
        or _load_token_from_credentials()
        or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )


def _is_oauth_token(token: str) -> bool:
    return "sk-ant-oat" in token


def _build_oauth_headers(token: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "anthropic-version": ANTHROPIC_VERSION,
        "anthropic-beta": OAUTH_BETA_HEADERS,
        "user-agent": f"claude-cli/{CLAUDE_CODE_VERSION}",
        "x-app": "cli",
        "anthropic-dangerous-direct-browser-access": "true",
        "accept": "application/json",
    }


def _build_apikey_headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "accept": "application/json",
    }


def _inject_system_identity(messages: list[dict], system_prompt: str | None = None) -> list[Any]:
    """Build system blocks with Claude Code identity prefix (required for OAuth).

    Returns system as a list of blocks (Anthropic format).
    """
    blocks = [
        {"type": "text", "text": CLAUDE_CODE_IDENTITY},
    ]
    # Extract existing system from messages if present
    if system_prompt:
        blocks.append({"type": "text", "text": system_prompt})
    return blocks


def _strip_provider_prefix(model: str) -> str:
    """Remove 'anthropic/' prefix from model name for direct API call."""
    if model.startswith("anthropic/"):
        return model[len("anthropic/"):]
    return model


async def _oauth_completion(
    token: str,
    model: str,
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> dict:
    """Call Anthropic Messages API directly with OAuth Bearer token."""
    model = _strip_provider_prefix(model)
    headers = _build_oauth_headers(token)

    # Separate system message from user/assistant messages
    system_prompt = None
    api_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_prompt = msg["content"]
        else:
            api_messages.append(msg)

    # Build payload with injected identity
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": _inject_system_identity(api_messages, system_prompt),
        "messages": api_messages,
    }

    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = {"type": "auto"}

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{ANTHROPIC_API_BASE}/v1/messages",
            headers=headers,
            json=payload,
        )

    if resp.status_code != 200:
        error_body = resp.text[:500]
        raise RuntimeError(f"Anthropic API error {resp.status_code}: {error_body}")

    data = resp.json()

    # Parse response
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


async def _apikey_completion(
    api_key: str,
    model: str,
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> dict:
    """Call Anthropic Messages API with standard API key."""
    model = _strip_provider_prefix(model)
    headers = _build_apikey_headers(api_key)

    system_prompt = None
    api_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_prompt = msg["content"]
        else:
            api_messages.append(msg)

    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": api_messages,
    }
    if system_prompt:
        payload["system"] = system_prompt
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = {"type": "auto"}

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{ANTHROPIC_API_BASE}/v1/messages",
            headers=headers,
            json=payload,
        )

    if resp.status_code != 200:
        error_body = resp.text[:500]
        raise RuntimeError(f"Anthropic API error {resp.status_code}: {error_body}")

    data = resp.json()

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


async def completion(
    model: str,
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> dict:
    """Call LLM — routes to local (llama.cpp/ollama) or Anthropic API.

    Routing logic:
    1. If model starts with "local/" → local OpenAI-compatible server
    2. Else → Anthropic (OAuth or API key)
    """
    if model.startswith("local/"):
        return await _local_completion(model, messages, max_tokens, temperature, tools)

    token = _get_token()
    if not token:
        raise RuntimeError(
            "No auth token found. Set ANTHROPIC_OAUTH_TOKEN or ANTHROPIC_API_KEY in .env"
        )

    if _is_oauth_token(token):
        log.debug("Using OAuth token auth (Bearer)")
        return await _oauth_completion(token, model, messages, max_tokens, temperature, tools)
    else:
        log.debug("Using API key auth (x-api-key)")
        return await _apikey_completion(token, model, messages, max_tokens, temperature, tools)


# ── Local LLM (llama.cpp / ollama / any OpenAI-compatible server) ──

# Default local server URL — override via LOCAL_LLM_BASE_URL env var
_LOCAL_LLM_BASE = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:8080")


def _get_local_base_url() -> str:
    """Get local LLM server base URL (supports runtime env override)."""
    return os.environ.get("LOCAL_LLM_BASE_URL", _LOCAL_LLM_BASE)


def _convert_tools_to_openai(tools: list[dict] | None) -> list[dict] | None:
    """Convert Anthropic tool format to OpenAI function-calling format.

    Anthropic: {"name": ..., "description": ..., "input_schema": {...}}
    OpenAI:    {"type": "function", "function": {"name": ..., "description": ..., "parameters": {...}}}
    """
    if not tools:
        return None
    converted = []
    for t in tools:
        converted.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", t.get("parameters", {})),
            },
        })
    return converted


def _parse_openai_response(data: dict, model_name: str) -> dict:
    """Parse OpenAI-format response into our standard format."""
    choices = data.get("choices", [])
    if not choices:
        return {"content": "", "tool_calls": [], "usage": {"prompt_tokens": 0, "completion_tokens": 0}, "model": model_name}

    msg = choices[0].get("message", {})
    content = msg.get("content", "") or ""

    # Strip <think>...</think> blocks (Qwen thinking mode)
    if "<think>" in content:
        import re
        content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()

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


def _flatten_content(content) -> str:
    """Normalize message content to plain string.

    Anthropic uses list-of-blocks: [{"type":"text","text":"..."}, {"type":"tool_result",...}]
    OpenAI expects plain strings. Convert any format to a flat string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    parts.append(f"[Tool call: {block.get('name', '?')}({json.dumps(block.get('input', {}))})]")
                elif block.get("type") == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, list):
                        inner = " ".join(b.get("text", "") for b in inner if isinstance(b, dict))
                    parts.append(f"[Tool result: {inner}]")
                else:
                    parts.append(str(block))
        return "\n".join(parts)
    return str(content) if content else ""


async def _local_completion(
    model: str,
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> dict:
    """Call local LLM via OpenAI-compatible API (llama.cpp / ollama / vLLM / etc).

    Model format: "local/<model_name>" or just "local/" (use server's loaded model).
    """
    base_url = _get_local_base_url()
    model_name = model.removeprefix("local/").strip() or None

    # Normalize all message content to plain strings (Anthropic → OpenAI format)
    # Also skip tool_use/tool_result role messages that local models can't handle
    api_messages = []
    system_parts = []
    for msg in messages:
        role = msg.get("role", "user")
        content = _flatten_content(msg.get("content", ""))
        if not content.strip():
            continue
        if role == "system":
            system_parts.append(content)
        elif role == "tool":
            # Convert tool results to assistant context
            api_messages.append({"role": "user", "content": f"[Tool Result] {content}"})
        else:
            api_messages.append({"role": role, "content": content})

    # Ensure message alternation (user/assistant/user/assistant)
    # Merge consecutive same-role messages
    merged = []
    for msg in api_messages:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] += "\n\n" + msg["content"]
        else:
            merged.append(msg)
    api_messages = merged

    if system_parts and api_messages:
        system_text = "\n\n".join(system_parts)
        if api_messages[0]["role"] == "user":
            api_messages[0]["content"] = f"[System Instructions]\n{system_text}\n\n[User Message]\n{api_messages[0]['content']}"
        else:
            api_messages.insert(0, {"role": "user", "content": f"[System Instructions]\n{system_text}"})
            if len(api_messages) > 1 and api_messages[1]["role"] == "user":
                api_messages[1]["content"] = api_messages[0]["content"] + "\n\n" + api_messages[1]["content"]
                api_messages.pop(0)
    elif system_parts:
        api_messages = [{"role": "user", "content": "\n\n".join(system_parts)}]

    payload: dict[str, Any] = {
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    if model_name:
        payload["model"] = model_name

    openai_tools = _convert_tools_to_openai(tools)
    if openai_tools:
        payload["tools"] = openai_tools
        payload["tool_choice"] = "auto"

    log.debug("Local LLM call: %s (model=%s, tokens=%d)", base_url, model_name or "default", max_tokens)

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.post(f"{base_url}/v1/chat/completions", json=payload)

    if resp.status_code != 200:
        error_body = resp.text[:500]
        raise RuntimeError(f"Local LLM error {resp.status_code}: {error_body}")

    return _parse_openai_response(resp.json(), model_name or "local")
