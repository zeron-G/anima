"""Working memory — importance-based eviction, not FIFO.

NOTE (v3): This module is used by heartbeat (tick logging) and dashboard
(display/clear) but is NOT part of the cognitive decision path.
The cognitive loop uses MemoryRetriever (v3) for memory injection.
WorkingMemory serves as a real-time monitoring buffer only.
"""

from __future__ import annotations

from anima.models.memory_item import MemoryItem
from anima.utils.logging import get_logger

log = get_logger("working_memory")


class WorkingMemory:
    """Short-term memory with importance-based eviction.

    When full, evicts the item with lowest importance (not oldest).
    """

    def __init__(self, capacity: int = 20) -> None:
        self._capacity = capacity
        self._items: list[MemoryItem] = []

    def add(self, item: MemoryItem) -> MemoryItem | None:
        """Add an item. Returns the evicted item if memory was full, else None."""
        evicted = None
        if len(self._items) >= self._capacity:
            # Find and remove the least important item
            min_idx = 0
            for i, m in enumerate(self._items):
                if m.importance < self._items[min_idx].importance:
                    min_idx = i
            evicted = self._items.pop(min_idx)
            log.debug("Evicted memory: %s (importance=%.2f)", evicted.id, evicted.importance)

        self._items.append(item)
        return evicted

    def get_by_importance(self, n: int = 10) -> list[MemoryItem]:
        """Get top N items sorted by importance (highest first)."""
        return sorted(self._items, key=lambda m: m.importance, reverse=True)[:n]

    def get_recent(self, n: int = 10) -> list[MemoryItem]:
        """Get most recent N items."""
        return sorted(self._items, key=lambda m: m.created_at, reverse=True)[:n]

    def get_all(self) -> list[MemoryItem]:
        return list(self._items)

    def get_summary(self) -> str:
        """Get a text summary of working memory for prompt building."""
        if not self._items:
            return "(working memory is empty)"
        top = self.get_by_importance(10)
        lines = []
        for m in top:
            lines.append(f"- [{m.type.value}] {m.content[:100]}")
        return "\n".join(lines)

    @property
    def size(self) -> int:
        return len(self._items)

    @property
    def capacity(self) -> int:
        return self._capacity

    def clear(self) -> None:
        self._items.clear()
