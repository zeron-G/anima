"""Fixer layer — the actuators + the escalation policy that gates them.

A FixStrategy is a closed contract: ``can_handle`` decides if it applies,
``repair`` performs an idempotent fix, ``rollback`` undoes it (or refuses). The
Registry picks the lightest applicable strategy and dedups in-flight repairs.
ComponentPolicy carries the per-component knobs — chiefly ``mode`` (auto vs
manual/human-authorized) and the anti-flapping budget.

P2 ships the safe, reversible rungs only (LLM backstop, DB recover). Process
restart / code self-repair (the irreversible rungs) arrive in later phases; this
layer is built so they slot in by harshness without changing the FSM.
"""
from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from anima.guardian.signal import Component, Fault, Health
from anima.utils.logging import get_logger

log = get_logger("guardian.fixer")


class FsmState(str, Enum):
    """Per-component escalation state."""
    OK = "ok"
    WARNED = "warned"          # anomaly seen, below repair threshold — alert only
    DEGRADED = "degraded"      # subsystem self-healed to its backup — observe
    REPAIRING = "repairing"    # a fix is in flight
    RECOVERING = "recovering"  # fix returned; confirming health
    HELD = "held"              # safe repairs exhausted — hold degraded, keep alerting
    ESCALATED = "escalated"    # P4: safe rungs failed → requested a process restart
    DEFEATED = "defeated"      # restart budget exhausted — stop trying, page a human (persisted)


class RepairKind(str, Enum):
    LLM_BACKSTOP = "llm_backstop"
    DB_RECOVER = "db_recover"
    RESTART_TASK = "restart_task"          # P3+
    RESTART_PROC = "restart_proc"          # P4
    CODE_REPAIR = "code_repair"            # P5
    ALERT_ONLY = "alert_only"              # explicit terminal: nothing safe to do


class RepairOutcome(str, Enum):
    REPAIRED = "repaired"        # fix applied; component should recover
    NOOP = "noop"               # nothing to do (already healthy)
    INEFFECTIVE = "ineffective"  # tried, didn't help → retry/escalate
    FAILED = "failed"           # the repair itself errored
    REFUSED = "refused"         # strategy declined (e.g. unsafe right now)
    ALERT_ONLY = "alert_only"    # no safe automatic action — hold + alert


@dataclass(frozen=True, slots=True)
class RepairAction:
    fault_id: str
    component: Component
    kind: RepairKind
    reason: str = ""
    attempt: int = 1
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class RepairResult:
    action_id: str
    kind: RepairKind
    outcome: RepairOutcome
    detail: str = ""
    new_health: Health = Health.UNKNOWN   # post-repair: never claims OK, at most RECOVERING
    reversible: bool = False
    ts: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return self.outcome in (RepairOutcome.REPAIRED, RepairOutcome.NOOP)


class FixStrategy(ABC):
    """One repair tactic for one component. Closed: can_handle / repair / rollback."""

    kind: RepairKind
    component: Component
    harshness: int = 50          # 1 = lightest … 100 = code repair; registry sorts ascending

    @abstractmethod
    async def can_handle(self, fault: Fault) -> bool:
        """Whether this strategy applies AND is safe to run right now."""

    @abstractmethod
    async def repair(self, action: RepairAction) -> RepairResult:
        """Perform the fix. MUST be idempotent and MUST NOT raise (wrap → FAILED)."""

    async def rollback(self, result: RepairResult) -> RepairResult:
        # Default: irreversible. Reversibility is declared on the RepairResult,
        # not faked with a no-op rollback.
        from dataclasses import replace
        return replace(result, outcome=RepairOutcome.REFUSED, detail="irreversible")


class Registry:
    """Holds strategies; selects the lightest applicable; dedups in-flight repairs."""

    def __init__(self, strategies: list[FixStrategy] | None = None) -> None:
        self._strategies = sorted(strategies or [], key=lambda s: s.harshness)
        self._inflight: set[Component] = set()

    async def strategy_for(self, fault: Fault) -> FixStrategy | None:
        for s in self._strategies:
            if s.component != fault.component:
                continue
            try:
                if await s.can_handle(fault):
                    return s
            except Exception as e:  # noqa: BLE001 — a strategy's gate must never crash the loop
                log.warning("guardian: %s.can_handle raised: %s", s.kind.value, e)
        return None

    def claim(self, component: Component) -> bool:
        if component in self._inflight:
            return False
        self._inflight.add(component)
        return True

    def release(self, component: Component) -> None:
        self._inflight.discard(component)


@dataclass
class ComponentPolicy:
    """Per-component escalation knobs. Default AUTO (the user's 'tend to full
    auto'); flip ``mode='manual'`` for the human-authorized variant."""
    enabled: bool = True
    mode: str = "auto"            # "auto" → execute; "manual" → propose + await human
    warn_threshold: int = 3       # consecutive faulted observations before repairing
    cooldown_s: float = 30        # min seconds between repair attempts
    max_attempts: int = 3         # attempts at the safe rung before holding degraded
    recover_confirm: int = 2      # consecutive healthy reads to clear RECOVERING → OK
    escalate_to_restart: bool = False  # P4: when safe rungs are exhausted/absent, escalate to a process restart

    @classmethod
    def from_config(cls, cfg: dict | None) -> "ComponentPolicy":
        cfg = cfg or {}
        return cls(
            enabled=cfg.get("enabled", True),
            mode=str(cfg.get("mode", "auto")).lower(),
            warn_threshold=int(cfg.get("warn_threshold", 3)),
            cooldown_s=float(cfg.get("cooldown_s", 30)),
            max_attempts=int(cfg.get("max_attempts", 3)),
            recover_confirm=int(cfg.get("recover_confirm", 2)),
            escalate_to_restart=bool(cfg.get("escalate_to_restart", False)),
        )
