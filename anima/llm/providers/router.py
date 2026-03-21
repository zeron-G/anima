"""Unified completion entry point -- routes to the correct provider."""
from __future__ import annotations

import os

from anima.llm.providers.constants import _LOCAL_LLM_BASE, _OPENAI_API_BASE
from anima.llm.providers.anthropic_sdk import _get_anthropic_client, _anthropic_sdk_completion
from anima.llm.providers.anthropic_http import _anthropic_completion
from anima.llm.providers.openai_compat import _openai_completion
from anima.llm.providers.codex import _codex_completion
from anima.llm.providers.local import _local_server


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

    # Default: Anthropic -- try SDK first, fall back to httpx
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
