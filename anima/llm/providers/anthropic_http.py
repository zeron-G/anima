"""Anthropic httpx fallback -- used for OAuth tokens or when SDK is not installed."""
from __future__ import annotations

import json
from typing import Any

import httpx

from anima.llm.providers.constants import (
    ANTHROPIC_API_BASE, ANTHROPIC_VERSION, CLAUDE_CODE_IDENTITY,
    CLAUDE_CODE_VERSION, OAUTH_BETA_HEADERS,
)
from anima.llm.providers.auth import _get_anthropic_token, _is_oauth_token
from anima.llm.providers.message_convert import _fix_api_messages


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
