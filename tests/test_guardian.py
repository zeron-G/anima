"""Sentinel P0 (passive self-healing supervisor) — offline unit tests.

Uses fakes for the LLM router / DB / mesh, so these run without Postgres. They
lock down the P0 guarantees: silent task death is surfaced, probe failures are
isolated (never crash the loop), health rolls up worst-first, and the monitor
takes NO action (dry-run).
"""
import asyncio

import pytest

from anima.guardian import Sentinel, TaskProbe, LlmProbe, DbProbe, MeshProbe
from anima.guardian.signal import Component, Health


# ── fakes ──
class _FakeRouter:
    def __init__(self, status):
        self._status = status
    def get_status(self):
        return self._status


class _FakeDb:
    def __init__(self, status):
        self._status = status
    def status(self):
        return self._status


class _BadProbe(MeshProbe):
    async def check(self):
        raise RuntimeError("boom")


# ── TaskProbe: the blind-spot fix ──
@pytest.mark.asyncio
async def test_task_probe_surfaces_silent_death():
    tp = TaskProbe()

    async def _dies():
        raise ValueError("loop crashed")

    t = asyncio.create_task(_dies(), name="cognitive")
    tp.watch(t, kind="cognitive")
    await asyncio.sleep(0.05)  # let it die + callback fire

    hr = await tp.check()
    assert hr.health is Health.DOWN
    assert "cognitive" in hr.detail


@pytest.mark.asyncio
async def test_task_probe_ignores_shutdown_deaths():
    tp = TaskProbe()

    async def _dies():
        raise ValueError("x")

    t = asyncio.create_task(_dies(), name="terminal")
    tp.watch(t, kind="terminal")
    tp.begin_shutdown()           # teardown — deaths must not register
    await asyncio.sleep(0.05)

    hr = await tp.check()
    assert hr.health is Health.OK


@pytest.mark.asyncio
async def test_task_probe_clean_exit_is_not_down():
    """A task that returns cleanly (e.g. heartbeat.start spawns sub-loops and
    returns) must NOT be flagged down — only exception-deaths are."""
    tp = TaskProbe()

    async def _spawns_and_returns():
        return  # finishes cleanly, by design

    t = asyncio.create_task(_spawns_and_returns(), name="heartbeat")
    tp.watch(t, kind="heartbeat")
    await asyncio.sleep(0.05)

    hr = await tp.check()
    assert hr.health is Health.OK


@pytest.mark.asyncio
async def test_task_probe_alive():
    tp = TaskProbe()
    fut = asyncio.get_event_loop().create_future()
    t = asyncio.create_task(_hold(fut), name="heartbeat")
    tp.watch(t, kind="heartbeat")
    hr = await tp.check()
    assert hr.health is Health.OK
    fut.set_result(None)
    await t


async def _hold(fut):
    await fut


# ── LlmProbe ──
@pytest.mark.asyncio
async def test_llm_probe_states():
    assert (await LlmProbe(_FakeRouter({"circuit_open": True})).check()).health is Health.DOWN
    deg = await LlmProbe(_FakeRouter({"degraded": True, "active_model": "x"})).check()
    assert deg.health is Health.DEGRADED and deg.self_healed
    assert (await LlmProbe(_FakeRouter({"active_model": "ok"})).check()).health is Health.OK


# ── DbProbe (folds in the failover state) ──
@pytest.mark.asyncio
async def test_db_probe_states():
    assert (await DbProbe(_FakeDb({"is_open": False})).check()).health is Health.DOWN
    deg = await DbProbe(_FakeDb({"is_open": True, "using_local": True})).check()
    assert deg.health is Health.DEGRADED and deg.self_healed
    assert (await DbProbe(_FakeDb({"is_open": True, "using_local": False})).check()).health is Health.OK


# ── MeshProbe disabled = OK (not an error) ──
@pytest.mark.asyncio
async def test_mesh_probe_disabled_is_ok():
    assert (await MeshProbe(None).check()).health is Health.OK


# ── Sentinel: rollup, isolation, dry-run ──
@pytest.mark.asyncio
async def test_sentinel_rollup_and_isolation():
    sentinel = Sentinel(
        probes=[
            LlmProbe(_FakeRouter({"circuit_open": True})),       # DOWN
            DbProbe(_FakeDb({"is_open": True, "using_local": True})),  # DEGRADED
            _BadProbe(None),                                      # raises → UNKNOWN, must not crash
        ],
        config={"interval_s": 0.01, "probe_timeout_s": 1},
    )
    task = asyncio.create_task(sentinel.run())
    await asyncio.sleep(0.1)
    sentinel.begin_shutdown()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    snap = sentinel.snapshot()
    assert snap["sentinel_tick"] > 0
    assert snap["overall"] == "down"                # worst-of (DOWN beats DEGRADED/UNKNOWN)
    assert snap["components"]["llm"]["health"] == "down"
    assert snap["components"]["db"]["health"] == "degraded"
    assert snap["components"]["mesh"]["health"] == "unknown"  # bad probe isolated, not crashed


# ── P1: external limb decision logic (pure) ──
def test_classify_exit():
    from anima.watchdog import classify_exit
    assert classify_exit(0, None) == "stop"            # clean exit, nobody asked
    assert classify_exit(1, None) == "crash"
    assert classify_exit(137, None) == "crash"
    assert classify_exit(0, {"reason": "x"}) == "requested_restart"  # marker wins
    assert classify_exit(1, {"reason": "x"}) == "requested_restart"


def test_liveness_verdict():
    from anima.watchdog import liveness_verdict
    # draining (graceful restart in progress) → never killed
    assert liveness_verdict(hb_age=9999, healthz_ok=False, tick_frozen=True, draining=True) == "alive"
    # body answers HTTP but Sentinel tick stuck → brain frozen
    assert liveness_verdict(hb_age=1, healthz_ok=True, tick_frozen=True, draining=False) == "brain_frozen"
    # heartbeat stale AND healthz unreachable (two signals) → hung
    assert liveness_verdict(hb_age=999, healthz_ok=False, tick_frozen=False, draining=False) == "hung"
    # stale heartbeat but healthz OK → NOT hung (single signal insufficient)
    assert liveness_verdict(hb_age=999, healthz_ok=True, tick_frozen=False, draining=False) == "alive"
    # all good
    assert liveness_verdict(hb_age=1, healthz_ok=True, tick_frozen=False, draining=False) == "alive"


# ── P1: cross-process ledger ──
def test_ledger_restart_budget(tmp_path):
    from anima.guardian.handoff import Ledger
    led = Ledger(path=tmp_path / "ledger.json")
    now = 1000.0
    assert led.can_restart(now, max_n=3, window_s=600)
    for i in range(3):
        led.record_restart("crash", now + i, window_s=600)
    assert led.restart_count(now + 3, window_s=600) == 3
    assert not led.can_restart(now + 3, max_n=3, window_s=600)   # budget exhausted
    # old restarts fall out of the window
    assert led.restart_count(now + 1000, window_s=600) == 0
    assert led.can_restart(now + 1000, max_n=3, window_s=600)


def test_ledger_defeated(tmp_path):
    from anima.guardian.handoff import Ledger
    led = Ledger(path=tmp_path / "ledger.json")
    assert not led.is_defeated("db")
    led.mark_defeated("db")
    assert led.is_defeated("db")
    assert Ledger(path=tmp_path / "ledger.json").is_defeated("db")  # persisted across instances
    led.clear_defeated("db")
    assert not led.is_defeated("db")


# ── P2: escalation FSM + Fixer layer ──
class _StubFixer:
    """A controllable fixer for FSM tests."""
    from anima.guardian.fixer import RepairKind as _RK
    kind = _RK.LLM_BACKSTOP
    component = Component.LLM
    harshness = 10

    def __init__(self, outcome):
        self._outcome = outcome
        self.calls = 0

    async def can_handle(self, fault):
        return True

    async def repair(self, action):
        from anima.guardian.fixer import RepairResult
        self.calls += 1
        return RepairResult(action.id, self.kind, self._outcome,
                            new_health=Health.RECOVERING)


def _faulted_llm():
    from anima.guardian.signal import HealthReport, Severity
    return HealthReport(Component.LLM, Health.DOWN, Severity.ERROR, detail="circuit open")


def _ok_llm():
    from anima.guardian.signal import HealthReport
    return HealthReport(Component.LLM, Health.OK, detail="ok")


def _build_sentinel(fixer, policy):
    from anima.guardian.fixer import Registry
    return Sentinel(probes=[], registry=Registry([fixer]),
                    policies={Component.LLM: policy})


@pytest.mark.asyncio
async def test_fsm_warns_before_repairing():
    from anima.guardian.fixer import ComponentPolicy, FsmState, RepairOutcome
    fx = _StubFixer(RepairOutcome.REPAIRED)
    s = _build_sentinel(fx, ComponentPolicy(warn_threshold=3, cooldown_s=0))
    s._observe(_faulted_llm())                       # 1st fault → WARNED, no repair
    assert s._domains[Component.LLM].state is FsmState.WARNED
    assert fx.calls == 0
    s._observe(_faulted_llm())                       # 2nd → still WARNED
    assert fx.calls == 0


@pytest.mark.asyncio
async def test_fsm_repairs_after_threshold_then_recovers():
    from anima.guardian.fixer import ComponentPolicy, FsmState, RepairOutcome
    fx = _StubFixer(RepairOutcome.REPAIRED)
    s = _build_sentinel(fx, ComponentPolicy(warn_threshold=2, cooldown_s=0, recover_confirm=1))
    s._observe(_faulted_llm())
    s._observe(_faulted_llm())                       # threshold → engage
    await asyncio.sleep(0.05)                         # let the repair task run
    assert fx.calls == 1
    assert s._domains[Component.LLM].state is FsmState.RECOVERING
    s._observe(_ok_llm())                            # confirm → OK
    assert s._domains[Component.LLM].state is FsmState.OK


@pytest.mark.asyncio
async def test_fsm_manual_mode_proposes_not_executes():
    from anima.guardian.fixer import ComponentPolicy, RepairOutcome
    fx = _StubFixer(RepairOutcome.REPAIRED)
    s = _build_sentinel(fx, ComponentPolicy(warn_threshold=1, cooldown_s=0, mode="manual"))
    s._observe(_faulted_llm())                       # confirmed, but manual → propose only
    await asyncio.sleep(0.05)
    assert fx.calls == 0                             # NOT executed


@pytest.mark.asyncio
async def test_fsm_holds_after_max_attempts():
    from anima.guardian.fixer import ComponentPolicy, FsmState, RepairOutcome
    fx = _StubFixer(RepairOutcome.INEFFECTIVE)
    s = _build_sentinel(fx, ComponentPolicy(warn_threshold=1, cooldown_s=0, max_attempts=2))
    for _ in range(6):
        s._observe(_faulted_llm())
        await asyncio.sleep(0.02)
    assert s._domains[Component.LLM].state is FsmState.HELD
    assert fx.calls == 2                             # capped at max_attempts, no storm


@pytest.mark.asyncio
async def test_fsm_self_healed_is_observed_not_repaired():
    from anima.guardian.fixer import ComponentPolicy, FsmState, RepairOutcome
    from anima.guardian.signal import HealthReport, Severity
    fx = _StubFixer(RepairOutcome.REPAIRED)
    s = _build_sentinel(fx, ComponentPolicy(warn_threshold=1, cooldown_s=0))
    s._observe(HealthReport(Component.LLM, Health.DEGRADED, Severity.WARN,
                            self_healed=True, detail="on fallback"))
    await asyncio.sleep(0.03)
    assert s._domains[Component.LLM].state is FsmState.DEGRADED
    assert fx.calls == 0                             # self-healed = report, not repair


def test_handoff_tick_and_marker(tmp_path, monkeypatch):
    from anima.guardian import handoff
    monkeypatch.setattr(handoff, "guardian_dir", lambda: tmp_path)
    handoff.write_sentinel_tick(42)
    assert handoff.read_sentinel_tick()["tick"] == 42
    assert handoff.write_restart_marker("evolution") is True
    assert handoff.read_restart_marker()["reason"] == "evolution"
    consumed = handoff.consume_restart_marker()
    assert consumed["reason"] == "evolution"
    assert handoff.read_restart_marker() is None    # archived, not re-read
