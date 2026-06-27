"""Sentinel core types — the closed vocabulary the self-healing system speaks.

P0 (passive observation) uses the subset here: Severity, Component, Health,
HealthReport, AuditRecord. The repair-side contracts (Fault / RepairAction /
RepairResult / FixStrategy) arrive with the Fixer layer in a later phase — see
docs/SENTINEL_DESIGN.md.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Mapping


class Severity(IntEnum):
    """Ordered so max() picks the worst."""
    OK = 0
    INFO = 1
    WARN = 2
    ERROR = 3
    CRITICAL = 4


class Component(str, Enum):
    """The closed set of monitorable subsystems."""
    LLM = "llm"
    DB = "db"
    TASK = "task"
    MESH = "mesh"
    CHANNELS = "channels"
    RESOURCE = "resource"
    PROCESS = "process"
    SENTINEL = "sentinel"


class Health(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"
    RECOVERING = "recovering"
    UNKNOWN = "unknown"


# Rollup rule (docs §2.1 G1): UNKNOWN counts as DEGRADED for alerting — never as
# OK. "stale-as-OK" is the most dangerous false negative. Ordering worst→best:
_HEALTH_RANK = {
    Health.DOWN: 4,
    Health.DEGRADED: 3,
    Health.UNKNOWN: 2,
    Health.RECOVERING: 1,
    Health.OK: 0,
}


def worst_health(items) -> Health:
    """Roll a set of component healths up to a single overall health."""
    worst = Health.OK
    for h in items:
        if _HEALTH_RANK.get(h, 2) > _HEALTH_RANK[worst]:
            worst = h
    return worst


@dataclass(frozen=True, slots=True)
class HealthReport:
    """A probe's current read of one component. Cheap, side-effect-free."""
    component: Component
    health: Health
    severity: Severity = Severity.OK
    detail: str = ""
    pressure: float = 0.0                 # 0..1 advisory load/closeness-to-failure
    ts: float = field(default_factory=time.time)
    self_healed: bool = False             # True: subsystem auto-switched to its own backup
    raw: Mapping[str, object] = field(default_factory=dict)
    source: str = "probe"

    def faulted(self) -> bool:
        return self.health in (Health.DOWN, Health.DEGRADED)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One append-only line in guardian_actions.jsonl. Everything is auditable."""
    phase: str                            # "observe" | "transition" | "warn" | (later) "repair"
    component: Component
    severity: Severity = Severity.INFO
    message: str = ""
    health: Health = Health.UNKNOWN
    node_id: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)
    data: Mapping[str, object] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "id": self.id, "ts": self.ts, "node_id": self.node_id,
            "phase": self.phase, "component": self.component.value,
            "severity": self.severity.name, "health": self.health.value,
            "message": self.message, "data": dict(self.data),
        }
