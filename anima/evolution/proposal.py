"""Evolution Proposal — structured proposal generation and management.

Generates proposals from:
  - Major heartbeat self-reflection
  - Error pattern detection
  - User feedback
  - Goal system targets
  - Remote node suggestions
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from anima.utils.ids import gen_id
from anima.utils.logging import get_logger

log = get_logger("evolution.proposal")


class ProposalType(str, Enum):
    BUGFIX = "bugfix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    OPTIMIZATION = "optimization"
    ARCHITECTURE = "architecture"


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    VOTING = "voting"
    APPROVED = "approved"
    REJECTED = "rejected"
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    REVIEWING = "reviewing"
    DEPLOYED = "deployed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    ABANDONED = "abandoned"


@dataclass
class Proposal:
    id: str
    type: ProposalType
    priority: int  # 1-5, 5=urgent
    title: str
    problem: str
    solution: str
    files: list[str] = field(default_factory=list)
    risk: str = "low"  # low, medium, high
    breaking_change: bool = False
    complexity: str = "small"  # trivial, small, medium, large
    proposer_node: str = "local"
    status: ProposalStatus = ProposalStatus.DRAFT
    timestamp: float = field(default_factory=time.time)
    votes: dict[str, str] = field(default_factory=dict)  # node_id → approve/reject/abstain
    vote_reasons: dict[str, str] = field(default_factory=dict)
    implementation_branch: str = ""
    retry_count: int = 0
    max_retries: int = 3

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "priority": self.priority,
            "title": self.title,
            "problem": self.problem,
            "solution": self.solution,
            "files": self.files,
            "risk": self.risk,
            "breaking_change": self.breaking_change,
            "complexity": self.complexity,
            "proposer_node": self.proposer_node,
            "status": self.status.value,
            "timestamp": self.timestamp,
            "votes": self.votes,
            "retry_count": self.retry_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Proposal:
        return cls(
            id=d["id"],
            type=ProposalType(d["type"]),
            priority=d.get("priority", 3),
            title=d["title"],
            problem=d.get("problem", ""),
            solution=d.get("solution", ""),
            files=d.get("files", []),
            risk=d.get("risk", "low"),
            breaking_change=d.get("breaking_change", False),
            complexity=d.get("complexity", "small"),
            proposer_node=d.get("proposer_node", "local"),
            status=ProposalStatus(d.get("status", "draft")),
            timestamp=d.get("timestamp", time.time()),
            votes=d.get("votes", {}),
            retry_count=d.get("retry_count", 0),
        )


class ProposalQueue:
    """Priority queue of approved proposals waiting for implementation."""

    def __init__(self) -> None:
        self._queue: list[Proposal] = []
        self._history: list[Proposal] = []  # completed/failed proposals
        self._max_history = 50

    def add(self, proposal: Proposal) -> None:
        proposal.status = ProposalStatus.APPROVED
        self._queue.append(proposal)
        self._queue.sort(key=lambda p: -p.priority)
        log.info("Proposal queued: %s (priority %d)", proposal.title, proposal.priority)

    def next(self) -> Proposal | None:
        if not self._queue:
            return None
        return self._queue[0]

    def pop(self) -> Proposal | None:
        if not self._queue:
            return None
        return self._queue.pop(0)

    def archive(self, proposal: Proposal) -> None:
        self._history.append(proposal)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    @property
    def size(self) -> int:
        return len(self._queue)

    @property
    def history(self) -> list[Proposal]:
        return self._history


def create_proposal(
    type: str,
    title: str,
    problem: str,
    solution: str,
    files: list[str] | None = None,
    risk: str = "low",
    priority: int = 3,
    complexity: str = "small",
    node_id: str = "local",
) -> Proposal:
    """Create a new evolution proposal."""
    ts = time.strftime("%Y%m%d")
    proposal_id = f"EVO-{ts}-{gen_id()[:6]}"

    return Proposal(
        id=proposal_id,
        type=ProposalType(type),
        priority=priority,
        title=title,
        problem=problem,
        solution=solution,
        files=files or [],
        risk=risk,
        complexity=complexity,
        proposer_node=node_id,
    )
