"""Concrete repair strategies — P2 ships the SAFE, REVERSIBLE rungs only.

Both deliberately do LESS than a naive "self-healer": they lean on the
subsystem's OWN recovery (the LLM router's circuit breaker + cascade, PgSync's
replay-before-failback) rather than reaching in and mutating its state. The
Guardian's job here is to remove a blocker (ensure a local backstop exists) or
to ACCELERATE an existing safe path (failback), never to override it.
"""
from __future__ import annotations

import asyncio

from anima.guardian.fixer import (
    FixStrategy, RepairAction, RepairKind, RepairOutcome, RepairResult,
)
from anima.guardian.signal import Component, Fault, Health
from anima.utils.logging import get_logger

log = get_logger("guardian.fixers")


class LlmBackstopFixer(FixStrategy):
    """When the LLM circuit is stuck open (all cloud providers failing), make
    sure the LOCAL backstop is running so the router's cascade has a floor to
    fall to. NEVER force-demote / reset the circuit — a probe call would feed the
    same failure counter and could trip it; the router's own 30s half-open probe
    recovers better than we could."""

    kind = RepairKind.LLM_BACKSTOP
    component = Component.LLM
    harshness = 10

    def __init__(self, router, local_server_manager) -> None:
        self._router = router
        self._local = local_server_manager

    async def can_handle(self, fault: Fault) -> bool:
        try:
            st = self._router.get_status() if self._router else {}
        except Exception:  # noqa: BLE001
            return False
        # circuit_open alone is the router working correctly; only act when it's
        # STUCK open and the local floor isn't up to catch the cascade.
        stuck = st.get("circuit_open") and st.get("seconds_in_silent_mode", 0) >= 60
        return bool(stuck and self._local is not None and not self._local.is_running)

    async def repair(self, action: RepairAction) -> RepairResult:
        try:
            ok = await self._local.ensure_running(timeout=30)
        except Exception as e:  # noqa: BLE001 — must not raise
            return RepairResult(action.id, self.kind, RepairOutcome.FAILED,
                                f"ensure_running raised: {e}", new_health=Health.DOWN)
        if not ok:
            return RepairResult(action.id, self.kind, RepairOutcome.INEFFECTIVE,
                                "local backstop unavailable", new_health=Health.DOWN)
        return RepairResult(action.id, self.kind, RepairOutcome.REPAIRED,
                            "ensured local LLM backstop; deferring to router auto-reset",
                            new_health=Health.RECOVERING, reversible=True)


class DbRecoverFixer(FixStrategy):
    """When the DB has failed over to local and the primary is reachable again,
    ACCELERATE failback — but ONLY via PgSync.recover_now() (replay-then-switch,
    same lock as the 300s loop). NEVER call switch_to_primary() directly (it
    bypasses replay → loses/dupes local-only writes), and never during a node
    split-brain (could replay a forked write)."""

    kind = RepairKind.DB_RECOVER
    component = Component.DB
    harshness = 20

    def __init__(self, db, pg_sync, split_brain=None) -> None:
        self._db = db
        self._sync = pg_sync
        self._split = split_brain

    async def can_handle(self, fault: Fault) -> bool:
        if not (self._db and self._sync):
            return False
        try:
            st = self._db.status()
        except Exception:  # noqa: BLE001
            return False
        if not st.get("using_local"):
            return False                       # on primary already = not a fault
        if getattr(self._split, "is_readonly", False):
            return False                       # split-brain: do not failback
        # read-only reachability probe (off the loop thread; ~5s connect)
        try:
            reachable = await asyncio.to_thread(self._db.primary_reachable)
        except Exception:  # noqa: BLE001
            reachable = False
        return bool(reachable)

    async def repair(self, action: RepairAction) -> RepairResult:
        try:
            res = await self._sync.recover_now()
        except Exception as e:  # noqa: BLE001
            return RepairResult(action.id, self.kind, RepairOutcome.FAILED,
                                f"recover_now raised: {e}", new_health=Health.DEGRADED)
        if res.get("recovered"):
            return RepairResult(action.id, self.kind, RepairOutcome.REPAIRED,
                                f"replayed local-only writes + failed back ({res.get('detail','')})",
                                new_health=Health.RECOVERING, reversible=False)
        if res.get("reason") == "in_progress":
            return RepairResult(action.id, self.kind, RepairOutcome.NOOP,
                                "sync loop already owns failback")
        return RepairResult(action.id, self.kind, RepairOutcome.INEFFECTIVE,
                            f"stayed local (writes safe): {res.get('reason', '?')}",
                            new_health=Health.DEGRADED)
