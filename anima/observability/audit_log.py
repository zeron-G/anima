"""Structured audit log for tool execution.

Writes JSONL entries to data/logs/tool_audit.jsonl for every tool call,
enabling compliance auditing and incident investigation.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from anima.config import data_dir
from anima.utils.logging import get_logger

log = get_logger("audit")


class ToolAuditLog:
    """Append-only JSONL audit log for tool executions."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (data_dir() / "logs" / "tool_audit.jsonl")
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        tool_name: str,
        args_summary: str,
        result_summary: str,
        duration_ms: float,
        success: bool,
        risk_level: int = 0,
        correlation_id: str = "",
    ) -> None:
        """Record a single tool execution event."""
        entry = {
            "ts": time.time(),
            "tool": tool_name,
            "args": args_summary[:200],
            "result": result_summary[:200],
            "duration_ms": round(duration_ms, 1),
            "success": success,
            "risk": risk_level,
            "cid": correlation_id,
        }
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            log.debug("Failed to write audit log entry")
