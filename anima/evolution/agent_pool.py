"""Agent Pool — depth-limited SubAgent spawn control.

Rules:
  - depth 0 (Eva): max 5 children
  - depth 1: max 3 children
  - depth 2: cannot spawn (leaf)
  - Spawn auto-approved by quota rules, no LLM call needed
  - Parent tracks children + grandchildren count
  - Timeout: depth 0=10min, 1=5min, 2=2min
  - Cascade terminate on parent death
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from anima.utils.ids import gen_id
from anima.utils.logging import get_logger

log = get_logger("evolution.agent_pool")

# Spawn limits per depth
MAX_CHILDREN = {0: 5, 1: 3, 2: 0}
TIMEOUT_S = {0: 600, 1: 300, 2: 120}  # 10min, 5min, 2min
HARVEST_TIMEOUT_S = 30


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class AgentEntry:
    id: str
    task: str
    depth: int
    parent_id: str | None
    status: AgentStatus = AgentStatus.PENDING
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    result: Any = None
    children_ids: list[str] = field(default_factory=list)

    @property
    def age_s(self) -> float:
        return time.time() - self.created_at

    @property
    def timed_out(self) -> bool:
        if self.status not in (AgentStatus.PENDING, AgentStatus.RUNNING):
            return False
        return self.age_s > TIMEOUT_S.get(self.depth, 120)

    def to_summary(self) -> dict:
        return {
            "id": self.id,
            "task": self.task[:80],
            "depth": self.depth,
            "status": self.status.value,
            "age_s": round(self.age_s),
            "children_count": len(self.children_ids),
        }


class AgentPool:
    """Manages the hierarchical agent tree with depth-limited spawning."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentEntry] = {}
        self._on_terminate: Callable[[str], None] | None = None

    def set_terminate_callback(self, fn: Callable[[str], None]) -> None:
        """Set callback to actually terminate a running agent."""
        self._on_terminate = fn

    def request_spawn(self, task: str, parent_id: str | None = None) -> str | None:
        """Request to spawn a new agent. Returns agent_id or None if denied.

        Spawn is auto-approved by quota rules — no LLM call.
        """
        # Determine depth
        if parent_id is None:
            depth = 0
        else:
            parent = self._agents.get(parent_id)
            if not parent:
                log.warning("Spawn denied: parent %s not found", parent_id)
                return None
            depth = parent.depth + 1

        # Check depth limit
        max_c = MAX_CHILDREN.get(depth, 0)
        if depth > 2 or max_c == 0:
            log.warning("Spawn denied: depth %d cannot spawn children", depth)
            return None

        # Check parent's children quota
        if parent_id:
            parent = self._agents[parent_id]
            active_children = sum(
                1 for cid in parent.children_ids
                if self._agents.get(cid) and
                self._agents[cid].status in (AgentStatus.PENDING, AgentStatus.RUNNING)
            )
            parent_max = MAX_CHILDREN.get(parent.depth, 0)
            if active_children >= parent_max:
                log.warning("Spawn denied: parent %s at capacity (%d/%d)",
                            parent_id[:8], active_children, parent_max)
                return None
        else:
            # Root level — check total depth-0 agents
            root_active = sum(
                1 for a in self._agents.values()
                if a.depth == 0 and a.status in (AgentStatus.PENDING, AgentStatus.RUNNING)
            )
            if root_active >= MAX_CHILDREN[0]:
                log.warning("Spawn denied: root at capacity (%d/%d)", root_active, MAX_CHILDREN[0])
                return None

        # Approved — create agent entry
        agent_id = f"agent-{gen_id()[:8]}"
        entry = AgentEntry(
            id=agent_id,
            task=task,
            depth=depth,
            parent_id=parent_id,
        )
        self._agents[agent_id] = entry

        if parent_id and parent_id in self._agents:
            self._agents[parent_id].children_ids.append(agent_id)

        log.info("Agent spawned: %s (depth=%d, task=%s)", agent_id, depth, task[:60])
        return agent_id

    def mark_running(self, agent_id: str) -> None:
        if agent_id in self._agents:
            self._agents[agent_id].status = AgentStatus.RUNNING

    def mark_completed(self, agent_id: str, result: Any = None) -> None:
        if agent_id in self._agents:
            self._agents[agent_id].status = AgentStatus.COMPLETED
            self._agents[agent_id].completed_at = time.time()
            self._agents[agent_id].result = result
            log.info("Agent completed: %s", agent_id)

    def mark_failed(self, agent_id: str, reason: str = "") -> None:
        if agent_id in self._agents:
            self._agents[agent_id].status = AgentStatus.FAILED
            self._agents[agent_id].completed_at = time.time()
            self._agents[agent_id].result = reason
            log.warning("Agent failed: %s — %s", agent_id, reason)

    def harvest(self, agent_id: str) -> Any:
        """Get result from a completed agent."""
        entry = self._agents.get(agent_id)
        if not entry:
            return None
        return entry.result

    def terminate(self, agent_id: str) -> None:
        """Terminate an agent and all its descendants."""
        entry = self._agents.get(agent_id)
        if not entry:
            return

        # Cascade to children first
        for cid in list(entry.children_ids):
            self.terminate(cid)

        if entry.status in (AgentStatus.PENDING, AgentStatus.RUNNING):
            entry.status = AgentStatus.CANCELLED
            entry.completed_at = time.time()
            if self._on_terminate:
                self._on_terminate(agent_id)
            log.info("Agent terminated: %s (cascade)", agent_id)

    def cleanup_expired(self) -> list[str]:
        """Terminate timed-out agents. Returns list of terminated IDs."""
        terminated = []
        for agent_id, entry in list(self._agents.items()):
            if entry.timed_out:
                self.terminate(agent_id)
                terminated.append(agent_id)
        return terminated

    def get_tree_summary(self) -> dict:
        """Get global agent tree summary (for root agent / dashboard)."""
        active = [a for a in self._agents.values()
                  if a.status in (AgentStatus.PENDING, AgentStatus.RUNNING)]
        return {
            "total_agents": len(self._agents),
            "active": len(active),
            "by_depth": {
                d: sum(1 for a in active if a.depth == d)
                for d in range(3)
            },
            "agents": [a.to_summary() for a in active],
        }

    def get_children_view(self, parent_id: str) -> dict:
        """Get the two-level view for a specific agent."""
        parent = self._agents.get(parent_id)
        if not parent:
            return {"children": [], "grandchildren_count": 0}

        children = []
        grandchildren_count = 0
        for cid in parent.children_ids:
            child = self._agents.get(cid)
            if child:
                children.append(child.to_summary())
                grandchildren_count += len(child.children_ids)

        return {
            "children": children,
            "grandchildren_count": grandchildren_count,
        }
