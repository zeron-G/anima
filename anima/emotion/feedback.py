"""Emotion feedback extraction — closes the emotion system loop (H-23 fix).

The old emotion system was decorative: engagement +0.1 on every user message,
then decay to baseline. No actual feedback from what the LLM said or did.

This module extracts emotional signals from the LLM's response text:
  - Positive/negative sentiment via keyword detection
  - Response length as engagement proxy
  - Tool usage as confidence signal
  - Error mentions as concern signal
  - Question marks as curiosity signal

The extracted adjustments are applied to EmotionState after each response,
creating a true feedback loop: emotion → prompt tone → LLM behavior →
response analysis → emotion adjustment.

Usage (in ResponseHandler):
    from anima.emotion.feedback import extract_emotion_adjustments
    adjustments = extract_emotion_adjustments(response_text)
    ctx.emotion.adjust(**adjustments)
"""

from __future__ import annotations

from anima.utils.logging import get_logger

log = get_logger("emotion.feedback")

# ── Sentiment signal definitions ──

# Each signal maps a dimension to keywords that push it up or down.
# Weights are intentionally small (±0.02 to ±0.08) to prevent wild swings.

_POSITIVE_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "engagement": [
        ("兴奋", 0.10), ("期待", 0.09), ("好奇", 0.09), ("有趣", 0.09),
        ("想试试", 0.10), ("让我看看", 0.08), ("马上", 0.07),
        ("excited", 0.09), ("interesting", 0.09), ("let me", 0.07),
    ],
    "confidence": [
        ("搞定了", 0.12), ("没问题", 0.10), ("很确定", 0.10),
        ("我知道怎么做", 0.12), ("done", 0.09), ("fixed", 0.10),
        ("successfully", 0.09), ("completed", 0.09),
    ],
    "curiosity": [
        ("为什么", 0.07), ("怎么回事", 0.07), ("研究一下", 0.09),
        ("interesting", 0.07), ("wonder", 0.07), ("investigate", 0.09),
    ],
}

_NEGATIVE_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "engagement": [
        ("无聊", -0.09), ("没什么", -0.07), ("算了", -0.07),
        ("boring", -0.07), ("nothing to", -0.07),
    ],
    "confidence": [
        ("不太确定", -0.10), ("可能不对", -0.09), ("抱歉", -0.07),
        ("sorry", -0.07), ("not sure", -0.09), ("failed", -0.09),
        ("error", -0.07), ("couldn't", -0.07),
    ],
    "concern": [
        ("出错了", 0.12), ("有问题", 0.10), ("失败了", 0.12),
        ("warning", 0.08), ("ERROR", 0.12), ("exception", 0.10),
        ("crash", 0.12), ("bug", 0.08), ("broken", 0.10),
    ],
}


def extract_emotion_adjustments(
    response: str,
    *,
    had_tool_calls: bool = False,
    tool_success_rate: float = 1.0,
) -> dict[str, float]:
    """Extract emotion adjustments from an LLM response.

    Args:
        response: The LLM's response text.
        had_tool_calls: Whether the response included tool calls.
        tool_success_rate: Fraction of successful tool calls (0.0-1.0).

    Returns:
        Dict of dimension → adjustment value, e.g.:
        {"engagement": 0.05, "confidence": -0.03, "concern": 0.02}
    """
    adjustments: dict[str, float] = {}
    response_lower = response.lower()

    # ── Keyword-based sentiment extraction ──

    for dimension, signals in _POSITIVE_SIGNALS.items():
        for keyword, weight in signals:
            if keyword.lower() in response_lower:
                adjustments[dimension] = adjustments.get(dimension, 0) + weight

    for dimension, signals in _NEGATIVE_SIGNALS.items():
        for keyword, weight in signals:
            if keyword.lower() in response_lower:
                adjustments[dimension] = adjustments.get(dimension, 0) + weight

    # ── Structural signals ──

    # Response length → engagement (long = engaged, short = disengaged)
    if len(response) > 500:
        adjustments["engagement"] = adjustments.get("engagement", 0) + 0.08
    elif len(response) < 30:
        adjustments["engagement"] = adjustments.get("engagement", 0) - 0.06

    # Tool usage → confidence
    if had_tool_calls:
        if tool_success_rate >= 0.8:
            adjustments["confidence"] = adjustments.get("confidence", 0) + 0.10
        elif tool_success_rate < 0.5:
            adjustments["confidence"] = adjustments.get("confidence", 0) - 0.08
            adjustments["concern"] = adjustments.get("concern", 0) + 0.08

    # Questions in response → curiosity
    question_count = response.count("?") + response.count("？")
    if question_count >= 2:
        adjustments["curiosity"] = adjustments.get("curiosity", 0) + 0.08

    # Code blocks → engagement + confidence (active work)
    if "```" in response:
        adjustments["engagement"] = adjustments.get("engagement", 0) + 0.07
        adjustments["confidence"] = adjustments.get("confidence", 0) + 0.06

    # ── Clamp individual adjustments to prevent single-response spikes ──
    clamped: dict[str, float] = {}
    for dim, val in adjustments.items():
        clamped[dim] = max(-0.30, min(0.30, val))

    if clamped:
        log.debug("Emotion adjustments: %s",
                  {k: f"{v:+.3f}" for k, v in clamped.items()})

    return clamped
