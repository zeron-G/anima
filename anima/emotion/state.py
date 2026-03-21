"""Emotion state machine — four dimensions + mood label with user perception.

Dimensions:
  engagement  — interest/attention level
  confidence  — certainty in decisions
  curiosity   — desire to explore/learn
  concern     — worry about anomalies

Added:
  user_state  — last detected user emotional state (from perception.py)
  intensity   — current emotional intensity 0.0–1.0 (decays over time)
  mood_label  — derived descriptive label (tender/playful/worried/etc.)

Changes from v1:
  - decay rate slowed from 0.05 → 0.02 (emotion persists longer)
  - adjust() clamp expanded to ±0.30 per delta (was 1.0 total clamp only)
  - mood_label property derived from dimensions + user_state override
  - user_state / intensity added as first-class fields
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── Mood derivation rules ─────────────────────────────────────────────────────
# Evaluated in order; first match wins.
# Condition keys: "{dimension}_{min|max}" — threshold float.
_MOOD_RULES: list[tuple[str, dict[str, float]]] = [
    ("worried",  {"concern_min":    0.65}),
    ("deflated", {"engagement_max": 0.30}),
    ("tender",   {"concern_min":    0.40, "confidence_max": 0.55}),
    ("excited",  {"engagement_min": 0.75, "curiosity_min":  0.70}),
    ("joyful",   {"engagement_min": 0.65, "confidence_min": 0.65}),
    ("playful",  {"curiosity_min":  0.70, "engagement_min": 0.55}),
    ("proud",    {"confidence_min": 0.80}),
    ("focused",  {"engagement_min": 0.55, "confidence_min": 0.55}),
]

# user_state detected from user message can override mood_label directly.
_USER_STATE_OVERRIDES: dict[str, str] = {
    "praising":   "joyful",
    "happy":      "playful",
    "frustrated": "worried",
    "sad":        "tender",
    "tired":      "tender",
}


@dataclass
class EmotionState:
    """Four-dimensional emotion state with mood label and user perception."""

    engagement: float = 0.5
    confidence: float = 0.6
    curiosity:  float = 0.7
    concern:    float = 0.2
    user_state: str   = "neutral"
    intensity:  float = 0.5
    _baseline: dict = field(default_factory=dict, repr=False)

    def __init__(self, baseline: dict | None = None) -> None:
        bl = baseline or {}
        self.engagement = bl.get("engagement", 0.5)
        self.confidence = bl.get("confidence", 0.6)
        self.curiosity  = bl.get("curiosity",  0.7)
        self.concern    = bl.get("concern",    0.2)
        self.user_state = bl.get("user_state", "neutral")
        self.intensity  = bl.get("intensity",  0.5)
        self._baseline = {
            "engagement": self.engagement,
            "confidence": self.confidence,
            "curiosity":  self.curiosity,
            "concern":    self.concern,
        }

    # ── Mood label ────────────────────────────────────────────────────────────

    @property
    def mood_label(self) -> str:
        """Derive a descriptive mood label from dimensions and user_state.

        user_state can force an override (e.g. frustrated → worried).
        Falls back to dimension-based rules, then 'focused' if nothing matches.
        """
        override = _USER_STATE_OVERRIDES.get(self.user_state)
        if override:
            return override

        d = {
            "engagement": self.engagement,
            "confidence": self.confidence,
            "curiosity":  self.curiosity,
            "concern":    self.concern,
        }
        for label, conds in _MOOD_RULES:
            match = True
            for key, threshold in conds.items():
                dim, bound = key.rsplit("_", 1)
                val = d.get(dim, 0.5)
                if bound == "min" and val < threshold:
                    match = False
                    break
                if bound == "max" and val > threshold:
                    match = False
                    break
            if match:
                return label

        return "focused"

    # ── Mutation ──────────────────────────────────────────────────────────────

    def adjust(self, **deltas: float) -> None:
        """Adjust dimensions by deltas. Each delta clamped to ±0.30."""
        _DIMS = {"engagement", "confidence", "curiosity", "concern"}
        for dim, delta in deltas.items():
            if dim in _DIMS:
                clamped_delta = max(-0.30, min(0.30, delta))
                current = getattr(self, dim)
                setattr(self, dim, max(0.0, min(1.0, current + clamped_delta)))

    def set_user_state(self, state: str, intensity: float = 0.5) -> None:
        """Set the perceived user emotional state and current intensity."""
        self.user_state = state
        self.intensity = max(0.0, min(1.0, intensity))

    def decay(self, rate: float = 0.02) -> None:
        """Decay all dimensions toward baseline.

        Rate slowed to 0.02 (was 0.05) so emotion persists across turns.
        """
        for dim, base in self._baseline.items():
            current = getattr(self, dim)
            if abs(current - base) < rate:
                setattr(self, dim, base)
            elif current > base:
                setattr(self, dim, current - rate)
            else:
                setattr(self, dim, current + rate)

        # user_state decays toward neutral via intensity
        if self.user_state != "neutral":
            self.intensity = max(0.0, self.intensity - 0.05)
            if self.intensity <= 0.0:
                self.user_state = "neutral"

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "engagement": round(self.engagement, 3),
            "confidence": round(self.confidence, 3),
            "curiosity":  round(self.curiosity,  3),
            "concern":    round(self.concern,    3),
            "user_state": self.user_state,
            "intensity":  round(self.intensity,  3),
            "mood_label": self.mood_label,
        }

    def dominant(self) -> str:
        """Return the most active dimension."""
        d = {
            "engagement": self.engagement,
            "confidence": self.confidence,
            "curiosity":  self.curiosity,
            "concern":    self.concern,
        }
        return max(d, key=d.get)  # type: ignore[arg-type]
