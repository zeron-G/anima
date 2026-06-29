"""Tests for the standalone P0 fixes (track A) from docs/CODE_REVIEW_2026-06.md:
P0-5 auth bind, P0-6 import LFI, P0-7 circuit breaker, P0-8 rule fast-path,
P0-9 pg_sync. Async cases use asyncio.run to stay independent of pytest-asyncio."""
from __future__ import annotations

import asyncio

import pytest


# ── P0-7: circuit breaker counts ONE failure per call(), not per cascade member ──
def test_circuit_breaker_single_failure_per_call(monkeypatch):
    import anima.llm.router as r

    async def boom(**kw):
        raise Exception("boom")  # not transient → no retry sleep

    monkeypatch.setattr(r, "completion", boom)
    router = r.LLMRouter(
        tier1_model="anthropic/a", tier2_model="anthropic/b",
        deepseek_fallback="deepseek/c", codex_fallback="codex/d",
        local_model="local/e",
    )
    msgs = [{"role": "user", "content": "hi"}]
    asyncio.run(router._try_call(msgs, 2, 0.7))
    assert router._consecutive_failures == 1, "a multi-provider cascade must add 1, not N"
    asyncio.run(router._try_call(msgs, 2, 0.7))
    assert router._consecutive_failures == 2


# ── P0-8: rule-engine fast path actually delivers + persists the response ──
def test_rule_decision_executed():
    from anima.core.stages import EventRoutingStage
    from anima.core.event_routing import EventRouter, RoutingDecision
    from anima.models.decision import Decision, ActionType
    from anima.models.event import Event, EventType

    emitted = []
    saved = []

    class FakeStore:
        async def save_memory_async(self, **kw):
            saved.append(kw)

    class FakeCtx:
        def __init__(self):
            self.conversation = []
            self.memory_store = FakeStore()

        def emit_output(self, text, source=""):
            emitted.append((text, source))

        def trim_conversation(self):
            pass

    ctx = FakeCtx()
    event = Event(type=EventType.USER_MESSAGE, payload={"text": "hi"}, source="sess1")
    decision = RoutingDecision(
        handled=True, source="sess1",
        rule_decision=Decision(action=ActionType.RESPOND, content="你好！"),
    )
    stage = EventRoutingStage(EventRouter())
    asyncio.run(stage._execute_rule_decision(None, event, ctx, decision))

    assert emitted == [("你好！", "sess1")], "greeting must be delivered to the user"
    assert ctx.conversation[-1] == {"role": "assistant", "content": "你好！"}
    # both user + assistant turns persisted
    roles = [s["metadata"]["role"] for s in saved]
    assert roles == ["user", "assistant"]


def test_rule_decision_noop_emits_nothing():
    from anima.core.stages import EventRoutingStage
    from anima.core.event_routing import EventRouter, RoutingDecision
    from anima.models.decision import Decision, ActionType
    from anima.models.event import Event, EventType

    emitted = []

    class FakeCtx:
        conversation = []
        def emit_output(self, text, source=""):
            emitted.append(text)
        def trim_conversation(self):
            pass

    event = Event(type=EventType.SYSTEM_ALERT, payload={}, source="")
    decision = RoutingDecision(
        handled=True, rule_decision=Decision(action=ActionType.NOOP))
    stage = EventRoutingStage(EventRouter())
    asyncio.run(stage._execute_rule_decision(None, event, FakeCtx(), decision))
    assert emitted == []


# ── P0-9: pg_sync has json imported (conflict journal) + clock-skew margin ──
def test_pg_sync_has_json_and_margin():
    from anima.memory import pg_sync
    assert pg_sync.json is not None          # journal would NameError without this
    assert pg_sync._RECONCILE_SAFETY_MARGIN_S >= 3600


# ── P0-6: import path confinement blocks traversal / escape ──
def test_import_path_confinement(tmp_path):
    from anima.utils.path_safety import validate_path_within
    from anima.utils.errors import PathTraversalBlocked

    (tmp_path / "doc.txt").write_text("x", encoding="utf-8")
    assert validate_path_within(tmp_path / "doc.txt", tmp_path)
    with pytest.raises(PathTraversalBlocked):
        validate_path_within(tmp_path / ".." / ".." / "secret.env", tmp_path)


# ── P0-5: auth_enabled reflects whether a password is configured ──
def test_auth_enabled(monkeypatch):
    import anima.api.auth as a
    monkeypatch.setattr(a, "get", lambda k, d="": "")
    assert a.auth_enabled() is False
    monkeypatch.setattr(a, "get", lambda k, d="": "${PW}")
    monkeypatch.setattr(a, "_resolve_secret", lambda v: "realpw")
    assert a.auth_enabled() is True
