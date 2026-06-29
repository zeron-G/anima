"""Tests for phase-5 self-repair: coordinator gating, Ledger repair budget,
and the shared governance gate (used by the engine's diff-scope check)."""
from __future__ import annotations

import asyncio

import pytest

from anima.guardian.self_repair import SelfRepairCoordinator
from anima.guardian.handoff import Ledger
from anima.core.governance import GovernanceEngine


class FakeLedger:
    def __init__(self, allow=True):
        self._allow = allow
        self.recorded = []

    def can_repair(self, c, now, mx, w):
        return self._allow

    def record_repair(self, c, now, w=86400):
        self.recorded.append(c)


class FakeEngine:
    def __init__(self):
        self.submitted = []

    async def submit_proposal(self, p):
        self.submitted.append(p)
        return "approved_executing"


# ── Coordinator gating ──
def test_should_attempt_disabled():
    c = SelfRepairCoordinator(engine=FakeEngine(), ledger=FakeLedger(), enabled=False)
    ok, why = c.should_attempt("task")
    assert ok is False and "disabled" in why


def test_should_attempt_no_engine():
    c = SelfRepairCoordinator(engine=None, ledger=FakeLedger(), enabled=True)
    assert c.should_attempt("task")[0] is False


def test_should_attempt_budget_exhausted():
    c = SelfRepairCoordinator(engine=FakeEngine(), ledger=FakeLedger(allow=False), enabled=True)
    ok, why = c.should_attempt("task")
    assert ok is False and "budget" in why


def test_should_attempt_ok():
    c = SelfRepairCoordinator(engine=FakeEngine(), ledger=FakeLedger(), enabled=True)
    assert c.should_attempt("task")[0] is True


def test_attempt_disabled_no_dispatch():
    eng = FakeEngine()
    c = SelfRepairCoordinator(engine=eng, ledger=FakeLedger(), enabled=False)
    assert c.attempt("task", "stuck") is False
    assert eng.submitted == []


def test_attempt_dispatches_gated_proposal():
    async def run():
        eng, led = FakeEngine(), FakeLedger()
        c = SelfRepairCoordinator(engine=eng, ledger=led, enabled=True)
        dispatched = c.attempt("task", "loop died")
        assert dispatched is True
        assert led.recorded == ["task"]      # budget consumed
        await asyncio.sleep(0.05)            # let the scheduled submit run
        assert len(eng.submitted) == 1
        p = eng.submitted[0]
        assert p.type.value == "bugfix" and p.files == []  # diff-scope gate enforces bounds
    asyncio.run(run())


# ── Ledger repair budget (persists across restarts) ──
def test_ledger_repair_budget(tmp_path):
    led = Ledger(path=tmp_path / "ledger.json")
    assert led.can_repair("task", 1000.0, 1, 86400) is True
    led.record_repair("task", 1000.0)
    assert led.repair_count("task", 1000.0, 86400) == 1
    assert led.can_repair("task", 1000.0, 1, 86400) is False     # max 1 reached
    assert led.can_repair("db", 1000.0, 1, 86400) is True         # per-component
    assert led.can_repair("task", 1000.0 + 90000, 1, 86400) is True  # window aged out


# ── Shared gate (engine diff-scope reuses this) ──
def test_gate_files_frozen_blocked():
    gov = GovernanceEngine()
    assert gov.gate_files(["anima/watchdog.py"], "x")[0] is False
    assert gov.gate_files(["anima/guardian/self_repair.py"], "x")[0] is False


def test_gate_files_allowlist_ok():
    gov = GovernanceEngine()
    assert gov.gate_files(["anima/tools/builtin/new_tool.py"], "x")[0] is True


def test_gate_files_outside_allowlist_needs_approval():
    gov = GovernanceEngine()
    ok, reason = gov.gate_files(["anima/api/chat.py"], "noapproval-id")
    assert ok is False and "approval" in reason
