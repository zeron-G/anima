"""Emotion → embodied expression (E3, DISTRIBUTED_DESIGN §5.6).

Maps Eva's mood to a self-contained PiDog expression so her BODY shows how she
feels — without waiting on an LLM decision. Uses only the non-locomotion emote
commands (be_happy/be_curious/be_alert/be_tired), which are never gated by the E1
safety clamp, so expression is always safe regardless of the robot's pose.
"""

from __future__ import annotations

from typing import Any

# perception kind → emotion adjustment. EmotionState.adjust clamps each to ±0.30.
# A body's sensations colour the per-locus emotion FAST (no LLM in the loop).
EMOTION_DELTAS: dict[str, dict[str, float]] = {
    "touch":            {"engagement": 0.15, "curiosity": 0.08, "concern": -0.05},
    "lifted":           {"concern": 0.15, "curiosity": 0.10, "engagement": 0.05},
    "set_down":         {"concern": -0.12, "engagement": 0.03},
    "obstacle_near":    {"concern": 0.10, "curiosity": 0.06},
    "battery_low":      {"concern": 0.10},
    "battery_critical": {"concern": 0.25, "engagement": -0.05},
    "emergency":        {"concern": 0.30, "engagement": -0.10},
}


def expression_for(emotion: Any) -> str | None:
    """Pick one PiDog emote for the current mood, or None for 'no notable change'.
    Ordered by urgency: alarm first, then bright, curious, weary. Non-locomotion."""
    concern = float(getattr(emotion, "concern", 0.2))
    curiosity = float(getattr(emotion, "curiosity", 0.7))
    engagement = float(getattr(emotion, "engagement", 0.5))
    if concern >= 0.55:
        return "be_alert"
    if engagement >= 0.60 and concern < 0.35:
        return "be_happy"
    if curiosity >= 0.68 and concern < 0.50:
        return "be_curious"
    if engagement < 0.35 and curiosity < 0.40:
        return "be_tired"
    return None
