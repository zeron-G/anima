"""DeepSeek upstream: routes via the OpenAI-compatible provider on both the
non-streaming and streaming paths, and sits in the cascade after Codex (Codex
OAuth gpt-5.5 stays primary)."""

from __future__ import annotations

import pytest

from anima.llm.providers import router as router_mod
from anima.llm.providers import stream as stream_mod
import anima.llm.providers.openai_compat as oc
from anima.llm.router import LLMRouter


@pytest.mark.asyncio
async def test_deepseek_completion_routes_to_openai_compat(monkeypatch):
    captured = {}

    async def fake_oc(base_url, model_id, api_key, messages, max_tokens=2048, temperature=0.7, tools=None):
        captured.update(base_url=base_url, model_id=model_id, api_key=api_key)
        return {"content": "hi", "tool_calls": [], "usage": {}, "model": model_id}

    monkeypatch.setattr(router_mod, "_openai_completion", fake_oc)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    r = await router_mod.completion(
        model="deepseek/deepseek-v4-flash", messages=[{"role": "user", "content": "hi"}],
    )
    assert captured["model_id"] == "deepseek-v4-flash"   # prefix stripped
    assert "deepseek.com" in captured["base_url"]
    assert captured["api_key"] == "sk-test"
    assert r["content"] == "hi"


@pytest.mark.asyncio
async def test_deepseek_stream_emits_single_message_complete(monkeypatch):
    async def fake_oc(base_url, model_id, api_key, messages, max_tokens=2048, temperature=0.7, tools=None):
        return {"content": "streamed", "tool_calls": [], "usage": {}, "model": model_id}

    # stream.py imports _openai_completion locally from openai_compat
    monkeypatch.setattr(oc, "_openai_completion", fake_oc)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    events = [e async for e in stream_mod.completion_stream(
        model="deepseek/deepseek-v4-flash", messages=[{"role": "user", "content": "hi"}],
    )]
    # Single message_complete (no text_delta preview → no double-display)
    assert [e.type for e in events] == ["message_complete"]
    assert events[0].content == "streamed"


def test_deepseek_sits_after_codex_in_cascade(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    r = LLMRouter(
        tier1_model="codex/gpt-5.5", tier2_model="codex/gpt-5.4-mini",
        codex_fallback="codex/gpt-5.4-mini",
        deepseek_fallback="deepseek/deepseek-v4-flash",
        local_model="local/",
    )
    chain = [m for m, _t, _to in r._build_cascade(1)]
    # Codex primary + backup → DeepSeek (live fallback) → local. Claude absent (no key).
    assert chain == [
        "codex/gpt-5.5", "codex/gpt-5.4-mini",
        "deepseek/deepseek-v4-flash", "local/",
    ]
