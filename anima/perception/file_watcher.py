"""File change detection — polling-based (15s interval via heartbeat)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from dataclasses import dataclass, field

from anima.utils.logging import get_logger

log = get_logger("file_watcher")


@dataclass
class FileState:
    path: str
    mtime: float
    size: int


class FileWatcher:
    """Detects file changes by comparing modification times.

    Designed to be called every script heartbeat (15s).
    Uses polling, not filesystem events — simple and cross-platform.
    Optional watchdog enhancement can be added later.
    """

    def __init__(
        self,
        watch_paths: list[str],
        extensions: list[str] | None = None,
    ) -> None:
        self._watch_paths = [Path(p).resolve() for p in watch_paths]
        self._extensions = set(extensions) if extensions else None
        self._state: dict[str, FileState] = {}
        self._initialized = False

    def detect_changes(self) -> list[dict]:
        """Scan watched paths and return changes since last call.

        Returns list of {"path": str, "change": "created"|"modified"|"deleted"}
        """
        current: dict[str, FileState] = {}
        changes: list[dict] = []

        for base in self._watch_paths:
            if not base.exists():
                continue
            self._scan_dir(base, current)

        if not self._initialized:
            self._state = current
            self._initialized = True
            return []  # First scan — baseline, no changes

        # Detect created and modified
        for path, state in current.items():
            if path not in self._state:
                changes.append({"path": path, "change": "created"})
            elif state.mtime != self._state[path].mtime:
                changes.append({"path": path, "change": "modified"})

        # Detect deleted
        for path in self._state:
            if path not in current:
                changes.append({"path": path, "change": "deleted"})

        self._state = current

        if changes:
            log.info("Detected %d file change(s)", len(changes))
        return changes

    def _scan_dir(self, base: Path, result: dict[str, FileState]) -> None:
        """Recursively scan a directory."""
        try:
            for entry in os.scandir(str(base)):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    # Skip non-interesting dirs (including ANIMA's own data)
                    if entry.name in {
                        "__pycache__", "node_modules", ".git", "venv", ".venv",
                        ".pytest_cache", "anima.egg-info", "logs", "notes", "chroma",
                        "voice", "uploads", "data",
                    }:
                        continue
                    self._scan_dir(Path(entry.path), result)
                elif entry.is_file(follow_symlinks=False):
                    # Skip noisy runtime files
                    if entry.name in {"scheduler.json", "anima.lock", "node.json", "evolution_state.json"}:
                        continue
                    if self._extensions:
                        ext = Path(entry.name).suffix
                        if ext not in self._extensions:
                            continue
                    try:
                        stat = entry.stat()
                        result[entry.path] = FileState(
                            path=entry.path,
                            mtime=stat.st_mtime,
                            size=stat.st_size,
                        )
                    except OSError:
                        pass
        except PermissionError:
            pass
