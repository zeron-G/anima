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
    """Call LLM — auto-detects OAuth token vs API key and routes accordingly.

    No litellm, no proxy — direct HTTP to Anthropic API.
    """
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
