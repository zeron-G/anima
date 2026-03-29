"""Config-driven tool selection — replaces hardcoded frozensets.

Loads tool subset definitions from config/tool_selection.yaml.
Falls back gracefully if the YAML file is not found.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from anima.config import project_root
from anima.utils.logging import get_logger

log = get_logger("tool_selector")

_DEFAULT_PATH = project_root() / "config" / "tool_selection.yaml"


class ToolSelector:
    """Select tool subsets based on event type and axis."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._path = config_path or _DEFAULT_PATH
        self._config: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            log.debug("Tool selection config not found: %s", self._path)
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
            log.info("Loaded tool selection config: %d event types, %d axes",
                     len(self._config.get("event_tools", {})),
                     len(self._config.get("axis_tools", {})))
        except Exception as e:
            log.warning("Failed to load tool selection config: %s", e)

    @property
    def available(self) -> bool:
        """True if config was loaded successfully."""
        return bool(self._config)

    def get_event_tools(self, event_type: str) -> frozenset[str] | None:
        """Get tool names for an event type, or None if not configured."""
        tools = self._config.get("event_tools", {}).get(event_type)
        return frozenset(tools) if tools else None

    def get_axis_tools(self, axis: str) -> frozenset[str] | None:
        """Get tool names for a thinking axis, or None if not configured."""
        tools = self._config.get("axis_tools", {}).get(axis)
        return frozenset(tools) if tools else None

    def get_user_only_tools(self) -> frozenset[str]:
        """Get tools that should only be available for user messages."""
        tools = self._config.get("user_only", [])
        return frozenset(tools) if tools else frozenset()
