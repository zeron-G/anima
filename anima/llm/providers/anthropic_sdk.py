"""Anthropic SDK client -- preferred path when SDK is installed + API key auth."""
from __future__ import annotations

import json
from typing import Any

from anima.llm.providers.constants import ANTHROPIC_API_BASE
from anima.llm.providers.auth import _get_anthropic_token, _is_oauth_token
from anima.llm.providers.message_convert import _fix_api_messages
from anima.utils.logging import get_logger

log = get_logger("llm_providers")

# ── SDK import (optional -- graceful fallback to httpx if not installed) ──
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

    # Custom base URL or non-standard token -> skip SDK, use httpx path
    if ANTHROPIC_API_BASE != "https://api.anthropic.com":
        return None

    token = _get_anthropic_token()
    if not token:
        return None

    _anthropic_client = AsyncAnthropic(
        api_key=token if not _is_oauth_token(token) else None,
        # OAuth tokens need manual header injection -- SDK doesn't support OAuth natively
        # Fall back to httpx for OAuth
        max_retries=2,
    )
    # Only use SDK for API key auth (not OAuth which needs special headers)
    if _is_oauth_token(token):
        _anthropic_client = None
        return None

    log.info("Using Anthropic SDK (auto-retry, connection pooling)")
    return _anthropic_client


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
