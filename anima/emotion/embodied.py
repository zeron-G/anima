"""Embodied emotion coupling (E3, DISTRIBUTED_DESIGN §5.6).

Two-way body↔emotion link driven off the robot's perception stream:
  1. A sensation nudges Eva's per-locus emotion FAST and deterministically (no LLM),
     so being touched feels engaging and being lifted feels a little alarming even
     before cognition catches up.
  2. Her resulting mood is reflected back as a PiDog expression (a be_* emote),
     throttled to actual mood changes — the body shows the feeling.

Wired as the RoboticsManager perception hook (fire-and-forget, off the poll's
critical path). Expression sends are non-locomotion, so the E1 clamp never blocks
them and there's no perceive→move feedback loop (emotes don't change the sensors
the perception source keys on).
"""

from __future__ import annotations

from typing import Any

from anima.robotics.expression import EMOTION_DELTAS, expression_for
from anima.utils.logging import get_logger

log = get_logger("emotion.embodied")


class EmbodiedEmotionCoupler:
    def __init__(self, emotion_state: Any, robotics_manager: Any, node_id: str) -> None:
        self._emotion = emotion_state
        self._robot = robotics_manager
        self._node_id = node_id
        self._last_expr: str | None = None

    async def on_perception(self, kind: str, perception: dict) -> None:
        """Called (fire-and-forget) for each significant EMBODIED_PERCEPTION."""
        # 1) sensation → emotion (deterministic, immediate)
        delta = EMOTION_DELTAS.get(kind)
        if delta:
            self._emotion.adjust(**delta)

        # 2) mood → expression, only when the chosen emote changes (avoid spamming
        #    the robot with the same emote every poll while a mood persists)
        expr = expression_for(self._emotion)
        if not expr or expr == self._last_expr:
            return
        self._last_expr = expr
        try:
            await self._robot.execute_command(self._node_id, expr)
            log.info("Embodied expression: %s (mood=%s) after %s", expr,
                     getattr(self._emotion, "mood_label", "?"), kind)
        except Exception as e:  # noqa: BLE001 — expression must never break perception
            log.debug("expression send skipped: %s", e)
