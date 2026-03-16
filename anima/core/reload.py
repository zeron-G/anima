"""Hot-reload manager — checkpoint state before evolution restart.

Flow:
  1. Evolution completes (tests pass, git committed)
  2. ReloadManager.request_reload() → saves checkpoint → signals shutdown
  3. main_entry() restart loop detects restart flag → re-runs run()
  4. New run() invocation loads checkpoint → restores state → continues

Checkpoint preserves:
  - Conversation buffer (so Eva doesn't lose context)
  - Emotion state (so mood doesn't reset)
  - Heartbeat tick count (so Eva knows uptime)
  - Discord response targets (so session routing continues)
  - Evolution context (what just completed)

Discord/Gossip reconnect in ~3-5 seconds. The user sees at most a brief
"Eva went offline" blip, then she's back with full context.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from anima.config import data_dir
from anima.utils.logging import get_logger

log = get_logger("reload")

CHECKPOINT_PATH = data_dir() / "restart_checkpoint.json"


class ReloadManager:
    """Manages checkpoint-based hot-reload for evolution deployments."""

    def __init__(self):
        self._restart_requested = False
        self._restart_reason = ""

    @property
    def restart_requested(self) -> bool:
        return self._restart_requested

    @property
    def restart_reason(self) -> str:
        return self._restart_reason

    def request_reload(
        self,
        reason: str,
        conversation: list[dict] | None = None,
        emotion_state: dict | None = None,
        tick_count: int = 0,
        discord_targets: dict | None = None,
        evolution_title: str = "",
    ) -> None:
        """Save checkpoint and signal restart.

        Called after evolution completes successfully.
        The cognitive loop checks restart_requested after each event
        and triggers graceful shutdown.
        """
        checkpoint = {
            "timestamp": time.time(),
            "reason": reason,
            "conversation": (conversation or [])[-50:],  # Keep last 50 turns
            "emotion": emotion_state or {},
            "tick_count": tick_count,
            "discord_targets": discord_targets or {},
            "evolution_title": evolution_title,
        }

        CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
        CHECKPOINT_PATH.write_text(
            json.dumps(checkpoint, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        self._restart_requested = True
        self._restart_reason = reason
        log.info("Reload requested: %s (checkpoint saved)", reason)

    @staticmethod
    def has_checkpoint() -> bool:
        """Check if a restart checkpoint exists."""
        return CHECKPOINT_PATH.exists()

    @staticmethod
    def load_checkpoint() -> dict | None:
        """Load and consume the checkpoint (deletes file after reading)."""
        if not CHECKPOINT_PATH.exists():
            return None
        try:
            data = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
            CHECKPOINT_PATH.unlink()
            age = time.time() - data.get("timestamp", 0)
            if age > 300:  # Stale checkpoint (> 5 min) — discard
                log.warning("Discarding stale checkpoint (%.0fs old)", age)
                return None
            log.info("Loaded restart checkpoint (%.1fs old): %s", age, data.get("reason", "?"))
            return data
        except Exception as e:
            log.error("Failed to load checkpoint: %s", e)
            try:
                CHECKPOINT_PATH.unlink()
            except Exception:
                pass
            return None

    @staticmethod
    def clear_checkpoint() -> None:
        """Remove checkpoint file if it exists."""
        try:
            if CHECKPOINT_PATH.exists():
                CHECKPOINT_PATH.unlink()
        except Exception:
            pass
