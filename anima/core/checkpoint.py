"""Pipeline checkpoint — persist stage progress for crash recovery.

Writes a JSON snapshot after each pipeline stage completes.
On clean completion, the checkpoint is cleared. On restart,
an incomplete checkpoint is logged as a warning and discarded.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from anima.config import data_dir
from anima.utils.logging import get_logger

log = get_logger("checkpoint")


class PipelineCheckpointer:
    """JSON-based pipeline checkpoint manager."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (data_dir() / "pipeline_checkpoint.json")
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, pctx: Any, stage_name: str) -> None:
        """Save a checkpoint after a pipeline stage completes."""
        data = {
            "event_id": pctx.event.id if hasattr(pctx, "event") else "",
            "event_type": pctx.event.type.name if hasattr(pctx, "event") else "",
            "completed_stage": stage_name,
            "user_message": (pctx.user_message[:500] if pctx.user_message else "")
                if hasattr(pctx, "user_message") else "",
            "correlation_id": getattr(pctx, "correlation_id", ""),
            "timestamp": time.time(),
            "content": (pctx.content[:500] if pctx.content else "")
                if hasattr(pctx, "content") else "",
            "tool_calls_made": getattr(pctx, "tool_calls_made", 0),
        }
        try:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except Exception:
            log.debug("Checkpoint save failed for stage %s", stage_name)

    def load(self) -> dict | None:
        """Load the last checkpoint, or None if no checkpoint exists."""
        if not self._path.exists():
            return None
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def clear(self) -> None:
        """Remove checkpoint after successful pipeline completion."""
        try:
            if self._path.exists():
                self._path.unlink(missing_ok=True)
        except Exception:
            pass

    def check_incomplete(self) -> None:
        """Check for and log any incomplete pipeline from a previous run."""
        checkpoint = self.load()
        if checkpoint:
            log.warning(
                "Found incomplete pipeline from previous run: "
                "event_id=%s, last_stage=%s, event_type=%s. Discarding.",
                checkpoint.get("event_id", "?"),
                checkpoint.get("completed_stage", "?"),
                checkpoint.get("event_type", "?"),
            )
            self.clear()
