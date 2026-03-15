"""Emotion state machine — four dimensions with rule-based adjustment and decay."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EmotionState:
    """Four-dimensional emotion state.

    Dimensions:
    - engagement: interest/attention level
    - confidence: certainty in decisions
    - curiosity: desire to explore/learn
    - concern: worry about anomalies
    """

    engagement: float = 0.5
    confidence: float = 0.6
    curiosity: float = 0.7
    concern: float = 0.2
    _baseline: dict = field(default_factory=dict, repr=False)

    def __init__(self, baseline: dict | None = None) -> None:
        bl = baseline or {}
        self.engagement = bl.get("engagement", 0.5)
        self.confidence = bl.get("confidence", 0.6)
        self.curiosity = bl.get("curiosity", 0.7)
        self.concern = bl.get("concern", 0.2)
        self._baseline = {
            "engagement": self.engagement,
            "confidence": self.confidence,
            "curiosity": self.curiosity,
            "concern": self.concern,
        }

    def adjust(self, **deltas: float) -> None:
        """Adjust dimensions by deltas. Clamps to [0, 1]."""
        for dim, delta in deltas.items():
            if hasattr(self, dim) and dim != "_baseline":
                current = getattr(self, dim)
                setattr(self, dim, max(0.0, min(1.0, current + delta)))

    def decay(self, rate: float = 0.05) -> None:
        """Decay all dimensions toward baseline."""
        for dim, base in self._baseline.items():
            current = getattr(self, dim)
            if abs(current - base) < rate:
                setattr(self, dim, base)
            elif current > base:
                setattr(self, dim, current - rate)
            else:
                setattr(self, dim, current + rate)

    def to_dict(self) -> dict:
        return {
            "engagement": round(self.engagement, 3),
            "confidence": round(self.confidence, 3),
            "curiosity": round(self.curiosity, 3),
            "concern": round(self.concern, 3),
        }

    def dominant(self) -> str:
        """Return the most active dimension."""
        d = self.to_dict()
        return max(d, key=d.get)  # type: ignore[arg-type]
