"""Importance scorer — assigns a 0.0-1.0 score to each memory.

Scoring is deterministic and fast (pure regex, no LLM call).
The score is composed of:
  1. A *base score* determined by memory type.
  2. *Signal bonuses* detected from the content text and context dict.

The final score is clamped to [0.0, 1.0].
"""

from __future__ import annotations

import re

from anima.utils.logging import get_logger

log = get_logger("importance")


# ------------------------------------------------------------------ #
#  Signal detection patterns (compiled once)                           #
# ------------------------------------------------------------------ #

_QUESTION_RE = re.compile(r"[?？]|吗\s*[。？?]?\s*$", re.MULTILINE)
_INSTRUCTION_RE = re.compile(r"帮我|去做|修改|记住|不要|请")
_EMOTION_RE = re.compile(r"谢谢|生气|开心|难过|爱|喜欢|讨厌")
_CODE_BLOCK_RE = re.compile(r"```|(?:^|\n)\s*(?:def |class )\s*\w+", re.MULTILINE)
_EVOLUTION_RE = re.compile(
    r"evolv|进化|突变|mutation|自我改进|self[-_]?improv|upgrade|迭代",
    re.IGNORECASE,
)
_NAME_MENTION_RE = re.compile(r"Eva|伊娃|ANIMA", re.IGNORECASE)


class ImportanceScorer:
    """Score memory entries by type + content signals.

    Usage::

        scorer = ImportanceScorer()
        score = scorer.score("帮我修改这个文件", "chat_user")
    """

    # Base importance by memory type
    BASE_SCORES: dict[str, float] = {
        "chat_user":      0.7,
        "chat_assistant":  0.5,
        "thought":        0.3,
        "action":         0.6,
        "observation":    0.2,
    }

    # Weight of each signal when detected
    SIGNAL_WEIGHTS: dict[str, float] = {
        "has_question":      0.15,
        "has_instruction":   0.20,
        "has_emotion":       0.10,
        "has_name_mention":  0.10,
        "is_long_message":   0.05,
        "has_code":          0.10,
        "tool_call_failed":  0.15,
        "evolution_related": 0.15,
    }

    def score(
        self,
        content: str,
        type: str,
        context: dict | None = None,
    ) -> float:
        """Return an importance score in [0.0, 1.0].

        Parameters
        ----------
        content:
            The raw text of the memory entry.
        type:
            One of the keys in :attr:`BASE_SCORES` (unknown types get 0.5).
        context:
            Optional dict carrying out-of-band info.  Recognised keys:
            ``tool_call_failed`` (bool) and ``evolution_related`` (bool).
        """
        base = self.BASE_SCORES.get(type, 0.5)
        signals = self._detect_signals(content, context or {})
        bonus = sum(self.SIGNAL_WEIGHTS[s] for s in signals)
        final = min(max(base + bonus, 0.0), 1.0)

        if signals:
            log.debug(
                "Importance %.2f (base=%.2f, signals=%s) for type=%s",
                final, base, signals, type,
            )

        return final

    # ------------------------------------------------------------------ #
    #  Signal detection                                                    #
    # ------------------------------------------------------------------ #

    def _detect_signals(
        self,
        content: str,
        context: dict,
    ) -> list[str]:
        """Detect all applicable signals from *content* and *context*.

        Returns a list of signal names (matching keys in
        :attr:`SIGNAL_WEIGHTS`).
        """
        signals: list[str] = []

        if _QUESTION_RE.search(content):
            signals.append("has_question")

        if _INSTRUCTION_RE.search(content):
            signals.append("has_instruction")

        if _EMOTION_RE.search(content):
            signals.append("has_emotion")

        if _NAME_MENTION_RE.search(content):
            signals.append("has_name_mention")

        if len(content) > 200:
            signals.append("is_long_message")

        if _CODE_BLOCK_RE.search(content):
            signals.append("has_code")

        # Context-driven signals
        if context.get("tool_call_failed"):
            signals.append("tool_call_failed")

        if context.get("evolution_related") or _EVOLUTION_RE.search(content):
            signals.append("evolution_related")

        return signals
