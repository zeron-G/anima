"""Event model — external events flowing through the event queue."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum, auto

from anima.utils.ids import gen_id


class EventType(IntEnum):
    """Types of events flowing through the cognitive cycle."""
    USER_MESSAGE = auto()
    FILE_CHANGE = auto()
    SYSTEM_ALERT = auto()
    TIMER = auto()
    SHUTDOWN = auto()
    # Self-generated events — the agent drives itself
    STARTUP = auto()        # First boot — introduce, scan, set goals
    SELF_THINKING = auto()  # Periodic proactive thought (LLM heartbeat)
    FOLLOW_UP = auto()      # Agent wants to continue working on something
    SCHEDULED_TASK = auto()  # Cron scheduler fired a job


class EventPriority(IntEnum):
    """Event priority (higher = more urgent)."""
    LOW = 2
    NORMAL = 5
    HIGH = 8
    CRITICAL = 10


@dataclass
class Event:
    """An external event to be processed by the cognitive cycle."""
    type: EventType
    payload: dict = field(default_factory=dict)
    priority: EventPriority = EventPriority.NORMAL
    id: str = field(default_factory=lambda: gen_id("evt"))
    timestamp: float = field(default_factory=time.time)
    source: str = ""

    def __lt__(self, other: Event) -> bool:
        """Priority queue ordering: higher priority first, then earlier timestamp."""
        if self.priority != other.priority:
            return self.priority > other.priority  # higher priority = dequeued first
        return self.timestamp < other.timestamp
