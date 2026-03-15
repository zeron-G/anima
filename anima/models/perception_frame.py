"""Perception models — PerceptionFrame, StateDiff, DiffRule."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from anima.utils.ids import gen_id


@dataclass
class DiffRule:
    """Threshold rule for a single field."""
    field: str
    threshold: float  # absolute change needed to count as significant


@dataclass
class FieldDiff:
    """Diff result for a single field."""
    field: str
    old_value: Any
    new_value: Any
    delta: float  # absolute change magnitude
    significant: bool  # exceeded threshold?


@dataclass
class StateDiff:
    """Result of comparing two environment snapshots."""
    field_diffs: dict[str, FieldDiff] = field(default_factory=dict)
    significance_score: float = 0.0  # 0.0–1.0
    has_alerts: bool = False
    timestamp: float = field(default_factory=time.time)

    @property
    def significant_fields(self) -> list[str]:
        return [k for k, v in self.field_diffs.items() if v.significant]


@dataclass
class PerceptionFrame:
    """Assembled perception context for the cognitive cycle."""
    id: str = field(default_factory=lambda: gen_id("pf"))
    timestamp: float = field(default_factory=time.time)
    # Current environment snapshot (from cache)
    system_state: dict = field(default_factory=dict)
    # File changes detected
    file_changes: list[dict] = field(default_factory=list)
    # The triggering event
    event_type: str = ""
    event_payload: dict = field(default_factory=dict)
    # Diff from last snapshot
    state_diff: StateDiff | None = None
    # Recent snapshot history
    recent_snapshots: list[dict] = field(default_factory=list)
