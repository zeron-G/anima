"""Self-repair coordinator (phase 5) — the LAST resort in the recovery chain:

    safe Fixers → process restart → rollback known-good → SELF-REPAIR → DEFEATED

When a component would otherwise be declared DEFEATED (safe repairs + restart
budget exhausted) and self-repair is enabled, formulate ONE repair proposal and
submit it through the SAME gated evolution pipeline (governance frozen/allowlist
+ approval, real test gate, sandbox, deploy, known-good auto-revert). There is no
green channel: a repair that touches the frozen recovery core or code outside the
evolvable allowlist is blocked exactly like any other evolution — and the engine's
diff-scope gate enforces that on the ACTUAL changed files, since a repair proposal
declares no files. Budget is persisted in the Ledger so a repair-triggered reload
can't loop.

Default OFF (guardian.self_repair_enabled). Building it does not enable it.
"""
from __future__ import annotations

import asyncio
import time

from anima.utils.logging import get_logger

log = get_logger("guardian.self_repair")


class SelfRepairCoordinator:
    def __init__(self, *, engine, ledger, enabled: bool,
                 max_repairs: int = 1, window_s: int = 86400) -> None:
        self._engine = engine
        self._ledger = ledger
        self._enabled = bool(enabled)
        self._max = max_repairs
        self._window = window_s

    def should_attempt(self, component: str) -> tuple[bool, str]:
        """Pure, side-effect-free decision: may we attempt self-repair now?"""
        if not self._enabled:
            return False, "self-repair disabled"
        if self._engine is None:
            return False, "no evolution engine"
        if self._ledger is not None and not self._ledger.can_repair(
                component, time.time(), self._max, self._window):
            return False, "repair budget exhausted"
        return True, "ok"

    def attempt(self, component: str, reason: str, fault_summary: str = "") -> bool:
        """Last-resort hook, called by the Sentinel right before it would declare a
        component DEFEATED. Returns True if a repair was DISPATCHED (the caller then
        HOLDS instead of declaring DEFEATED); False → let it go DEFEATED."""
        ok, why = self.should_attempt(component)
        if not ok:
            log.info("self-repair skipped for %s: %s", component, why)
            return False
        try:
            if self._ledger is not None:
                self._ledger.record_repair(component, time.time(), self._window)
            asyncio.get_event_loop().create_task(
                self._submit(component, reason, fault_summary))
            log.warning("self-repair DISPATCHED for %s (%s) — via the gated evolution pipeline",
                        component, reason)
            return True
        except Exception as e:  # noqa: BLE001 — a failed dispatch must not crash the guardian loop
            log.error("self-repair dispatch failed for %s: %s", component, e)
            return False

    async def _submit(self, component: str, reason: str, fault_summary: str) -> None:
        from anima.evolution.proposal import create_proposal
        proposal = create_proposal(
            type="bugfix",
            title=f"Self-repair: {component} persistently failing",
            problem=(f"The '{component}' component is still unhealthy after safe repairs "
                     f"and a process restart. Reason: {reason}. "
                     f"Diagnostics: {fault_summary or 'see guardian audit log'}"),
            solution=("Find the root cause in the failing component's code + recent errors, "
                      "then make the SMALLEST fix that restores health. Edit ONLY files in the "
                      "evolvable allowlist; never the frozen recovery core."),
            files=[],            # agent diagnoses; the engine's diff-scope gate enforces bounds
            risk="high",
            complexity="small",
        )
        try:
            result = await self._engine.submit_proposal(proposal)
            log.warning("self-repair proposal %s submitted → %s", proposal.id, result)
        except Exception as e:  # noqa: BLE001
            log.error("self-repair submit failed: %s", e)
