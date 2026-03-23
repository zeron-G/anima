"""Unified governance engine — controls Eva's autonomous behavior boundaries.

Consolidates scattered governance checks into one module:
- Personality update frequency limits
- Evolution proposal safety checks
- Self-thinking loop detection
- Style drift accumulation monitoring
- Activity level management
"""

from __future__ import annotations

import json
import time

from anima.config import get, data_dir
from anima.utils.logging import get_logger

log = get_logger("governance")


class GovernanceEngine:
    """Unified governance — controls Eva's autonomous behavior boundaries."""

    def __init__(self) -> None:
        self._last_personality_update: float = 0
        self._last_relationship_update: float = 0
        self._recent_self_thinking_actions: list[str] = []
        self._drift_scores: list[float] = []

    def check_personality_update(self, file: str) -> tuple[bool, str]:
        """Check if personality/relationship update is allowed.

        personality.md: max once per 4 hours
        relationship.md: max once per 24 hours
        """
        now = time.time()
        if file == "personality":
            if now - self._last_personality_update < 14400:  # 4 hours
                remaining = int((14400 - (now - self._last_personality_update)) / 60)
                return False, f"Personality update cooldown: {remaining}min remaining"
            self._last_personality_update = now
            return True, "OK"
        elif file == "relationship":
            if now - self._last_relationship_update < 86400:  # 24 hours
                remaining = int((86400 - (now - self._last_relationship_update)) / 3600)
                return False, f"Relationship update cooldown: {remaining}h remaining"
            self._last_relationship_update = now
            return True, "OK"
        return True, "OK"

    def check_evolution_proposal(self, proposal: dict, recent_failures: int) -> tuple[bool, str]:
        """Check if evolution proposal should proceed.

        Core modules require human confirmation.
        Consecutive failures trigger extended cooldown.
        """
        _CORE_MODULES = {
            "anima/core/cognitive.py", "anima/core/heartbeat.py",
            "anima/main.py", "anima/core/event_queue.py",
            "anima/__main__.py", "anima/core/pipeline.py",
            "anima/core/stages.py",
        }
        files = proposal.get("files", [])
        core_touched = [f for f in files if f in _CORE_MODULES]
        if core_touched and not proposal.get("human_confirmed"):
            return False, f"Core module change requires human confirmation: {core_touched}"
        if recent_failures >= 3:
            return False, f"Too many recent failures ({recent_failures}), extended cooldown"
        return True, "OK"

    def check_self_thinking_loop(self, action: str) -> bool:
        """Detect if self-thinking is in an action loop.

        If 3 consecutive self-thoughts all executed tool calls,
        reduce activity (return False to skip next one).
        """
        self._recent_self_thinking_actions.append(action)
        if len(self._recent_self_thinking_actions) > 5:
            self._recent_self_thinking_actions.pop(0)

        # If last 3 all had tool actions, signal to reduce
        if len(self._recent_self_thinking_actions) >= 3:
            recent = self._recent_self_thinking_actions[-3:]
            if all(a != "quiet" for a in recent):
                log.info("Self-thinking loop detected — 3 consecutive active thoughts")
                return False  # Should reduce activity
        return True

    def load_recent_drift_scores(self, max_entries: int = 20) -> None:
        """Load recent drift scores from drift.jsonl."""
        drift_path = data_dir() / "logs" / "drift.jsonl"
        if not drift_path.exists():
            return
        try:
            lines = drift_path.read_text(encoding="utf-8").strip().split("\n")
            recent = lines[-max_entries:] if len(lines) > max_entries else lines
            self._drift_scores = []
            for line in recent:
                if line.strip():
                    entry = json.loads(line)
                    self._drift_scores.append(entry.get("drift_score", 0))
        except Exception:
            pass

    def check_drift_accumulation(self, drift_score: float) -> bool:
        """Check if style drift is accumulating dangerously.

        If 5 consecutive responses have drift_score > 0.5,
        trigger style fallback (return False).
        """
        # Bootstrap from drift.jsonl if no scores loaded yet
        if not self._drift_scores:
            self.load_recent_drift_scores()

        self._drift_scores.append(drift_score)
        if len(self._drift_scores) > 10:
            self._drift_scores.pop(0)

        if len(self._drift_scores) >= 5:
            recent = self._drift_scores[-5:]
            if all(s > 0.5 for s in recent):
                log.warning("Drift accumulation detected — 5 consecutive high-drift responses")
                return False  # Should trigger style reset
        return True

    def get_activity_level(self) -> str:
        """Determine current activity level based on config."""
        return get("governance.default_mode", "active")


# Module-level singleton
_governance: GovernanceEngine | None = None


def get_governance() -> GovernanceEngine:
    global _governance
    if _governance is None:
        _governance = GovernanceEngine()
    return _governance
