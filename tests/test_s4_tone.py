"""S2-tail / S4: emotion deterministically shapes tone via SoulContainer's
emoji density (no LLM prompt prescription → no cross-model style drift).
"""

from __future__ import annotations

import yaml

from anima.llm.soul_container import SoulContainer, _EMOJI_RE
from anima.emotion.state import EmotionState


def _emoji_chars(s: str) -> int:
    return sum(len(m) for m in _EMOJI_RE.findall(s))


# Emoji interspersed with text so each is a separate density match.
_TEXT = "你好😊今天😄天气🎉不错✨真的💕很好🌟啊🔥嗯😎好🥳呀🌸"


def test_emoji_density_scales_with_mood_factor():
    rule = {"type": "emoji_density", "max_density": 0.05, "strip_if_over": True}
    worried = SoulContainer._apply_emoji_density(_TEXT, rule, 0.4)
    happy = SoulContainer._apply_emoji_density(_TEXT, rule, 1.8)
    # A positive mood keeps at least as many emoji as a worried one.
    assert _emoji_chars(happy) >= _emoji_chars(worried)
    # And both never add emoji.
    assert _emoji_chars(happy) <= _emoji_chars(_TEXT)


def test_transform_uses_emotion_valence(tmp_path):
    (tmp_path / "style_rules.yaml").write_text(
        yaml.dump({"rules": [{"type": "emoji_density", "max_density": 0.05, "strip_if_over": True}]}),
        encoding="utf-8",
    )
    pos = EmotionState()
    pos.engagement, pos.confidence, pos.concern = 0.9, 0.9, 0.05   # valence strongly +
    neg = EmotionState()
    neg.engagement, neg.confidence, neg.concern = 0.3, 0.3, 0.85   # valence strongly -
    assert pos.valence() > neg.valence()

    out_pos = SoulContainer(tmp_path).transform(_TEXT, emotion=pos)
    out_neg = SoulContainer(tmp_path).transform(_TEXT, emotion=neg)
    assert _emoji_chars(out_pos) >= _emoji_chars(out_neg)


def test_transform_without_emotion_is_unchanged_behavior(tmp_path):
    """No emotion → factor 1.0 → behaves exactly as before (back-compat)."""
    (tmp_path / "style_rules.yaml").write_text(
        yaml.dump({"rules": [{"type": "emoji_density", "max_density": 0.05, "strip_if_over": True}]}),
        encoding="utf-8",
    )
    no_emo = SoulContainer(tmp_path).transform(_TEXT)
    rule = {"type": "emoji_density", "max_density": 0.05, "strip_if_over": True}
    baseline = SoulContainer._apply_emoji_density(_TEXT, rule, 1.0)
    assert _emoji_chars(no_emo) == _emoji_chars(baseline)
