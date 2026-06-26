"""S2 regression: emotion has consequences (salience, behavior, tone signals)
and survives restart — no longer a decorative vector.
"""

from __future__ import annotations

import pytest

from anima.emotion.state import EmotionState
from anima.memory.store import MemoryStore


def test_arousal_low_when_calm():
    e = EmotionState()  # at baseline, neutral user_state
    assert e.arousal() < 0.1
    assert e.salience_multiplier() == pytest.approx(1.0, abs=0.06)


def test_arousal_high_when_user_emotion_intense():
    e = EmotionState()
    e.set_user_state("frustrated", intensity=0.8)
    assert e.arousal() >= 0.7
    assert e.salience_multiplier() > 1.3


def test_arousal_high_when_dimensions_swing():
    e = EmotionState()
    e.adjust(concern=0.3)  # concern 0.2 → 0.5, big deviation from baseline
    assert e.arousal() > 0.3
    assert e.salience_multiplier() > 1.0


def test_salience_multiplier_boosts_importance_monotonically():
    calm = EmotionState()
    charged = EmotionState()
    charged.set_user_state("sad", intensity=0.9)
    base = 0.5
    assert base * calm.salience_multiplier() < base * charged.salience_multiplier()
    # never pushes a mid importance past the clamp on its own
    assert charged.salience_multiplier() <= 1.6


def test_valence_sign():
    pos = EmotionState()
    pos.engagement, pos.confidence, pos.concern = 0.85, 0.85, 0.1
    neg = EmotionState()
    neg.engagement, neg.confidence, neg.concern = 0.4, 0.4, 0.85
    assert pos.valence() > 0
    assert neg.valence() < 0


def test_thinking_interval_factor_tracks_drive():
    low = EmotionState()
    low.curiosity, low.engagement = 0.2, 0.2
    high = EmotionState()
    high.curiosity, high.engagement = 0.9, 0.9
    # Low drive → think LESS (factor > 1); high drive → think MORE (factor < 1)
    assert low.thinking_interval_factor() > 1.0
    assert high.thinking_interval_factor() < 1.0
    assert low.thinking_interval_factor() > high.thinking_interval_factor()


def test_restore_sets_dimensions():
    e = EmotionState()
    e.restore({"engagement": 0.9, "confidence": 0.1, "curiosity": 0.3, "concern": 0.7})
    assert e.engagement == 0.9 and e.concern == 0.7
    assert e.to_dict()["arousal"] >= 0.0  # exposed in serialization


def test_to_dict_exposes_arousal_and_valence():
    d = EmotionState().to_dict()
    assert "arousal" in d and "valence" in d


@pytest.mark.asyncio
async def test_emotion_persists_and_restores_across_restart(tmp_path):
    """Emotion logged on interaction must be recoverable on the next startup
    (before S2 it reset to baseline on every non-evolution restart)."""
    store = await MemoryStore.create(str(tmp_path / "anima.db"))
    assert store.get_latest_emotion() is None  # nothing logged yet

    await store.log_emotion_async(0.82, 0.33, 0.91, 0.12, trigger="interaction")
    latest = store.get_latest_emotion()
    assert latest is not None and latest["engagement"] == pytest.approx(0.82)

    # Simulate a fresh process: new EmotionState restored from DB
    restored = EmotionState()
    restored.restore(latest)
    assert restored.engagement == pytest.approx(0.82)
    assert restored.curiosity == pytest.approx(0.91)
    await store.close()
