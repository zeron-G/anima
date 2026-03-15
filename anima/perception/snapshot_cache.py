"""Environment snapshot cache — script heartbeat writes, cognitive cycle reads."""

from __future__ import annotations

import time
import threading
from collections import deque


class SnapshotCache:
    """Thread-safe cache for environment snapshots.

    Written by script heartbeat (every 15s).
    Read by cognitive cycle's Perceive stage.
    """

    def __init__(self, history_size: int = 10) -> None:
        self._lock = threading.Lock()
        self._history: deque[dict] = deque(maxlen=history_size)
        self._latest: dict | None = None
        self._file_changes: list[dict] = []

    def update(self, system_state: dict, file_changes: list[dict] | None = None) -> None:
        """Update cache with new snapshot from script heartbeat."""
        snapshot = {
            "system_state": system_state,
            "file_changes": file_changes or [],
            "timestamp": time.time(),
        }
        with self._lock:
            self._latest = snapshot
            self._history.append(snapshot)
            if file_changes:
                self._file_changes.extend(file_changes)

    def get_latest(self) -> dict | None:
        """Get the most recent snapshot. Called by Perceive stage."""
        with self._lock:
            return self._latest

    def get_history(self, n: int = 5) -> list[dict]:
        """Get the most recent N snapshots."""
        with self._lock:
            items = list(self._history)
            return items[-n:]

    def consume_file_changes(self) -> list[dict]:
        """Get and clear accumulated file changes."""
        with self._lock:
            changes = self._file_changes
            self._file_changes = []
            return changes

    def clear(self) -> None:
        """Clear all cached data."""
        with self._lock:
            self._latest = None
            self._history.clear()
            self._file_changes.clear()
