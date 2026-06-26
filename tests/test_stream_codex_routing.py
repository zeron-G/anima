"""Regression: completion_stream() must route codex/ models to the Codex
provider, NOT to Anthropic.

The bug: codex/ had no branch in completion_stream, so it fell through to
Anthropic streaming → 404 ("model: codex/gpt-5.5"). The tool loop then failed and
fell back messily — tool calls leaked into chat and replies stopped mid-sentence.
"""

from __future__ import annotations

import pytest

from anima.llm.providers import stream as stream_mod
import anima.llm.providers.codex as codex_mod


@pytest.mark.asyncio
async def test_codex_stream_routes_to_codex_provider(monkeypatch):
    called = {}

    async def fake_codex(model, messages, max_tokens=4096, temperature=0.7, tools=None):
        called["model"] = model
        return {
            "content": "你好主人",
            "tool_calls": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            "model": model,
        }

    monkeypatch.setattr(codex_mod, "_codex_completion", fake_codex)

    events = []
    async for ev in stream_mod.completion_stream(
        model="codex/gpt-5.5", messages=[{"role": "user", "content": "hi"}],
    ):
        events.append(ev)

    # Routed to the Codex provider (not Anthropic, which would 404)
    assert called.get("model") == "codex/gpt-5.5"
    # Only message_complete — no text_delta preview (codex is one-shot; a preview
    # would double-display via stream_callback + output_callback).
    assert [e.type for e in events] == ["message_complete"]
    assert events[-1].content == "你好主人"
    assert events[-1].tool_calls == []


@pytest.mark.asyncio
async def test_codex_stream_propagates_tool_calls(monkeypatch):
    """Structured tool_calls from Codex must reach message_complete (so the tool
    loop executes them) instead of leaking into displayed text."""
    async def fake_codex(model, messages, max_tokens=4096, temperature=0.7, tools=None):
        return {
            "content": "",
            "tool_calls": [{"id": "c1", "name": "get_datetime", "arguments": "{}"}],
            "usage": {},
            "model": model,
        }

    monkeypatch.setattr(codex_mod, "_codex_completion", fake_codex)

    final = None
    async for ev in stream_mod.completion_stream(
        model="codex/gpt-5.4-mini", messages=[{"role": "user", "content": "几点了"}],
        tools=[{"name": "get_datetime"}],
    ):
        if ev.type == "message_complete":
            final = ev
    assert final is not None
    assert len(final.tool_calls) == 1 and final.tool_calls[0]["name"] == "get_datetime"
