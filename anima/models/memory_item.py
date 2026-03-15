"""MemoryItem model — items stored in working and episodic memory."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from anima.utils.ids import gen_id


class MemoryType(Enum):
    CHAT = "chat"
    INTERACTION = "interaction"
    DECISION = "decision"
    ACTION_RESULT = "action_result"
    OBSERVATION = "observation"
    SYSTEM_EVENT = "system_event"


# Default importance by type
IMPORTANCE_DEFAULTS: dict[MemoryType, float] = {
    MemoryType.CHAT: 0.9,
    MemoryType.INTERACTION: 0.8,
    MemoryType.DECISION: 0.7,
    MemoryType.ACTION_RESULT: 0.7,
    MemoryType.OBSERVATION: 0.3,
    MemoryType.SYSTEM_EVENT: 0.1,
}


@dataclass
class MemoryItem:
    """A single memory item with importance scoring."""
    content: str
    type: MemoryType
    importance: float = 0.5
    id: str = field(default_factory=lambda: gen_id("mem"))
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    metadata: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.importance == 0.5 and self.type in IMPORTANCE_DEFAULTS:
            self.importance = IMPORTANCE_DEFAULTS[self.type]

    def touch(self) -> None:
        """Mark as accessed."""
        self.last_accessed = time.time()
        self.access_count += 1
