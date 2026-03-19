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
        ("兴奋", 0.05), ("期待", 0.04), ("好奇", 0.04), ("有趣", 0.04),
        ("想试试", 0.05), ("让我看看", 0.04), ("马上", 0.03),
        ("excited", 0.04), ("interesting", 0.04), ("let me", 0.03),
    ],
    "confidence": [
        ("搞定了", 0.06), ("没问题", 0.05), ("很确定", 0.05),
        ("我知道怎么做", 0.06), ("done", 0.04), ("fixed", 0.05),
        ("successfully", 0.04), ("completed", 0.04),
    ],
    "curiosity": [
        ("为什么", 0.03), ("怎么回事", 0.03), ("研究一下", 0.04),
        ("interesting", 0.03), ("wonder", 0.03), ("investigate", 0.04),
    ],
}

_NEGATIVE_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "engagement": [
        ("无聊", -0.04), ("没什么", -0.03), ("算了", -0.03),
        ("boring", -0.03), ("nothing to", -0.03),
    ],
    "confidence": [
        ("不太确定", -0.05), ("可能不对", -0.04), ("抱歉", -0.03),
        ("sorry", -0.03), ("not sure", -0.04), ("failed", -0.04),
        ("error", -0.03), ("couldn't", -0.03),
    ],
    "concern": [
        ("出错了", 0.05), ("有问题", 0.04), ("失败了", 0.05),
        ("warning", 0.03), ("ERROR", 0.05), ("exception", 0.04),
        ("crash", 0.05), ("bug", 0.03), ("broken", 0.04),
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
        adjustments["engagement"] = adjustments.get("engagement", 0) + 0.03
    elif len(response) < 30:
        adjustments["engagement"] = adjustments.get("engagement", 0) - 0.02

    # Tool usage → confidence
    if had_tool_calls:
        if tool_success_rate >= 0.8:
            adjustments["confidence"] = adjustments.get("confidence", 0) + 0.04
        elif tool_success_rate < 0.5:
            adjustments["confidence"] = adjustments.get("confidence", 0) - 0.03
            adjustments["concern"] = adjustments.get("concern", 0) + 0.03

    # Questions in response → curiosity
    question_count = response.count("?") + response.count("？")
    if question_count >= 2:
        adjustments["curiosity"] = adjustments.get("curiosity", 0) + 0.03

    # Code blocks → engagement + confidence (active work)
    if "```" in response:
        adjustments["engagement"] = adjustments.get("engagement", 0) + 0.02
        adjustments["confidence"] = adjustments.get("confidence", 0) + 0.02

    # ── Clamp individual adjustments to prevent single-response spikes ──
    clamped: dict[str, float] = {}
    for dim, val in adjustments.items():
        clamped[dim] = max(-0.15, min(0.15, val))

    if clamped:
        log.debug("Emotion adjustments: %s",
                  {k: f"{v:+.3f}" for k, v in clamped.items()})

    return clamped
