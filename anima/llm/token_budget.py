"""Token budget manager — allocate context window across prompt layers.

Each LLM call has a fixed context window.  This module divides that window
among six priority layers (identity, rules, context, memory, tools,
conversation) so that high-priority sections are never starved and
conversation history gets the remaining space.

Token counting uses a multi-tier approach:
  1. tiktoken (if installed) — ~95% accurate for Claude models
  2. CJK/ASCII split estimator — ~85% accurate
  3. UTF-8 bytes / 3 fallback — ~70% accurate
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from anima.utils.logging import get_logger

log = get_logger("token_budget")


# ------------------------------------------------------------------ #
#  Module-level helpers                                                #
# ------------------------------------------------------------------ #

def count_tokens(text: str) -> int:
    """Estimate token count with improved accuracy for Chinese-English mixed text.

    Accuracy tiers:
      1. tiktoken (if installed) — ~95% accurate for Claude models
      2. CJK/ASCII split estimator — ~85% accurate
      3. UTF-8 bytes / 3 fallback — ~70% accurate

    The CJK split estimator counts characters by category:
      - CJK characters (U+4E00-U+9FFF): ~1.5 tokens each
      - ASCII characters: ~0.25 tokens each (4 chars ≈ 1 token)
      - Other (punctuation, emoji, etc.): ~0.5 tokens each
    """
    if not text:
        return 0

    # Tier 1: tiktoken (most accurate, but optional dependency)
    encoder = _get_tiktoken_encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:
            pass

    # Tier 2: CJK/ASCII split estimator
    cjk = 0
    ascii_chars = 0
    other = 0
    for ch in text:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or 0xF900 <= cp <= 0xFAFF:
            cjk += 1
        elif cp < 128:
            ascii_chars += 1
        else:
            other += 1

    estimated = cjk * 1.5 + ascii_chars * 0.25 + other * 0.5
    return max(1, int(estimated))


# Lazy-loaded tiktoken encoder
_tiktoken_encoder = None
_tiktoken_attempted = False


def _get_tiktoken_encoder():
    """Try to load tiktoken encoder (optional dependency)."""
    global _tiktoken_encoder, _tiktoken_attempted
    if _tiktoken_encoder is not None:
        return _tiktoken_encoder
    if _tiktoken_attempted:
        return None
    _tiktoken_attempted = True
    try:
        import tiktoken
        _tiktoken_encoder = tiktoken.get_encoding("cl100k_base")
        return _tiktoken_encoder
    except (ImportError, Exception):
        return None


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate *text* so its estimated token count fits *max_tokens*.

    Cuts at the last sentence boundary (。！？!?.\\n) that fits.
    If no boundary is found the text is hard-cut at the character limit.
    """
    if count_tokens(text) <= max_tokens:
        return text

    # max_tokens * 3 gives the approximate character budget
    char_limit = max_tokens * 3
    truncated = text[:char_limit]

    # Try to find the last sentence boundary
    boundary = _last_sentence_boundary(truncated)
    if boundary > 0:
        return truncated[:boundary]
    return truncated


_SENTENCE_END_RE = re.compile(r"[。！？!?\n]")


def _last_sentence_boundary(text: str) -> int:
    """Return the index *after* the last sentence-ending character, or 0."""
    best = 0
    for m in _SENTENCE_END_RE.finditer(text):
        best = m.end()
    return best


# ------------------------------------------------------------------ #
#  Layer definitions                                                   #
# ------------------------------------------------------------------ #

@dataclass(frozen=True)
class _Layer:
    """A single budget layer with minimum and optional maximum tokens."""
    name: str
    min_tokens: int
    max_tokens: int | None  # None == unlimited (gets all remaining)

    @property
    def is_unbounded(self) -> bool:
        return self.max_tokens is None


# The six layers in priority order.  Higher-priority layers are
# allocated first; conversation is always last and gets the remainder.
_LAYERS: list[_Layer] = [
    _Layer("identity",      min_tokens=300,  max_tokens=800),
    _Layer("rules",         min_tokens=300,  max_tokens=600),
    _Layer("context",       min_tokens=200,  max_tokens=1000),
    _Layer("memory",        min_tokens=200,  max_tokens=2000),
    _Layer("tools",         min_tokens=0,    max_tokens=1500),
    _Layer("conversation",  min_tokens=500,  max_tokens=None),
]

_LAYER_INDEX = {layer.name: layer for layer in _LAYERS}


# ------------------------------------------------------------------ #
#  TokenBudget                                                         #
# ------------------------------------------------------------------ #

class TokenBudget:
    """Allocate a model's context window across prompt layers.

    Usage::

        budget = TokenBudget(max_context=200_000)
        messages = budget.compile({
            "identity": identity_text,
            "rules": rules_text,
            "context": context_text,
            "memory": memory_text,
            "tools": tools_text,
            "conversation": conversation_messages_json,
        })
    """

    def __init__(
        self,
        max_context: int = 200_000,
        reserve_response: int = 8192,
    ) -> None:
        self.max_context = max_context
        self.reserve_response = reserve_response
        self.available = max_context - reserve_response

    # -------------------------------------------------------------- #
    #  Public API                                                      #
    # -------------------------------------------------------------- #

    def compile(self, sections: dict[str, str]) -> list[dict[str, Any]]:
        """Allocate tokens and build the final message list.

        Parameters
        ----------
        sections:
            Mapping of layer name -> raw text for that layer.
            ``"conversation"`` is expected to be a JSON-encoded list of
            ``{"role": ..., "content": ...}`` dicts, or a plain string that
            will be wrapped in a single user message.

        Returns
        -------
        A list starting with a single ``{"role": "system", "content": ...}``
        message (the merged system prompt) followed by conversation messages.
        """
        allocated = self._allocate(sections)

        # Build the system prompt from all non-conversation layers
        system_parts: list[str] = []
        for layer in _LAYERS:
            if layer.name == "conversation":
                continue
            text = allocated.get(layer.name, "")
            if text:
                system_parts.append(text)

        messages: list[dict[str, Any]] = []
        system_prompt = "\n\n".join(system_parts)
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Append conversation messages
        conv_text = allocated.get("conversation", "")
        if conv_text:
            messages.extend(self._parse_conversation(conv_text))

        return messages

    def get_conversation_budget(self, used_by_other_layers: int) -> int:
        """Return how many tokens remain for the conversation layer.

        This is the number Compaction Flush should target when deciding
        whether to summarise old conversation turns.
        """
        remaining = self.available - used_by_other_layers
        conv_layer = _LAYER_INDEX["conversation"]
        return max(remaining, conv_layer.min_tokens)

    # -------------------------------------------------------------- #
    #  Allocation algorithm                                            #
    # -------------------------------------------------------------- #

    def _allocate(self, sections: dict[str, str]) -> dict[str, str]:
        """Two-pass allocation: minimums first, then proportional fill."""
        raw_sizes: dict[str, int] = {}
        for layer in _LAYERS:
            text = sections.get(layer.name, "")
            raw_sizes[layer.name] = count_tokens(text)

        budget = self.available

        # --- Pass 1: guarantee minimums for bounded layers ----------
        alloc: dict[str, int] = {}
        for layer in _LAYERS:
            if layer.is_unbounded:
                continue
            needed = min(raw_sizes[layer.name], layer.min_tokens)
            alloc[layer.name] = needed
            budget -= needed

        if budget < 0:
            from anima.utils.errors import ContextTooSmallError
            raise ContextTooSmallError(deficit=-budget, available=self.available)

        # --- Pass 2: distribute remaining budget proportionally -----
        #     up to each layer's max (or raw size, whichever is less)
        wants: dict[str, int] = {}
        for layer in _LAYERS:
            if layer.is_unbounded:
                continue
            raw = raw_sizes[layer.name]
            cap = layer.max_tokens  # type: ignore[assignment]
            already = alloc.get(layer.name, 0)
            want = min(raw, cap) - already  # how much more this layer wants
            wants[layer.name] = max(want, 0)

        total_want = sum(wants.values())
        if total_want > 0 and budget > 0:
            share = min(budget, total_want)
            for name, want in wants.items():
                extra = int(share * want / total_want) if total_want else 0
                alloc[name] = alloc.get(name, 0) + extra
                budget -= extra

            # Distribute rounding remainder to the most demanding layer
            if budget > 0 and wants:
                most_wanted = max(wants, key=lambda n: wants[n])
                give = min(budget, wants[most_wanted])
                alloc[most_wanted] = alloc.get(most_wanted, 0) + give
                budget -= give

        # --- Pass 3: conversation gets everything that's left -------
        conv_layer = _LAYER_INDEX["conversation"]
        conv_alloc = max(budget, conv_layer.min_tokens)
        alloc["conversation"] = min(conv_alloc, raw_sizes.get("conversation", conv_alloc))

        # --- Truncate each section to its allocation ----------------
        result: dict[str, str] = {}
        for layer in _LAYERS:
            text = sections.get(layer.name, "")
            if not text:
                continue
            tokens = alloc.get(layer.name, 0)
            result[layer.name] = truncate_to_tokens(text, tokens)

        return result

    # -------------------------------------------------------------- #
    #  Conversation parsing                                            #
    # -------------------------------------------------------------- #

    @staticmethod
    def _parse_conversation(text: str) -> list[dict[str, Any]]:
        """Parse conversation text into message dicts.

        Accepts either a JSON array of ``{"role": ..., "content": ...}``
        or plain text (wrapped as a single user message).
        """
        import json

        text = text.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [
                        {"role": m.get("role", "user"), "content": m.get("content", "")}
                        for m in parsed
                        if isinstance(m, dict)
                    ]
            except (json.JSONDecodeError, TypeError) as e:
                # M-34: Malformed JSON starting with '[' — warn so it's visible in logs
                log.warning("Malformed JSON in conversation layer: %s", e)

        # Fallback: treat as a single user message
        if text:
            return [{"role": "user", "content": text}]
        return []
