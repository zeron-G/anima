"""Soul Container — deterministic personality post-processing layer.

Inspired by AIRI's approach: instead of relying solely on the LLM to
follow personality prompts, we apply a rule-based transform AFTER
generation and BEFORE output.  This catches stylistic drift that
prompt-following alone cannot prevent.

Only applied to user-facing responses (not self-thoughts or tool calls).

Rule types (loaded from ``style_rules.yaml``):
  * **tone_particle** — regex match -> random replacement from candidates
  * **emoji_density** — control emoji frequency per character
  * **length_guard** — truncate overly long casual responses
  * **catchphrase_ensure** — ensure certain phrases appear every N messages
"""

from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any

import yaml

from anima.utils.logging import get_logger

log = get_logger("soul_container")

# ------------------------------------------------------------------ #
#  Pre-compiled patterns                                               #
# ------------------------------------------------------------------ #

# Broad Unicode emoji ranges — used by emoji_density rule.
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # misc symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002600-\U000026FF"  # misc symbols
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # ZWJ
    "]+",
    re.UNICODE,
)

# Sentence-ending characters for length_guard truncation.
_SENTENCE_ENDS = ("。", "！", "？", ".", "!", "?")


# ------------------------------------------------------------------ #
#  SoulContainer                                                       #
# ------------------------------------------------------------------ #

class SoulContainer:
    """Deterministic personality post-processing layer.

    Applied AFTER the LLM generates a response, BEFORE output to the
    user.  Ensures personality consistency without relying solely on
    prompt-following.

    Parameters
    ----------
    rules_dir:
        Directory containing ``style_rules.yaml``.  If the file is
        missing the container silently operates as a no-op pass-through.
    """

    def __init__(self, rules_dir: Path) -> None:
        self._rules: list[dict[str, Any]] = []
        self._message_counter: int = 0
        self._load_rules(rules_dir)

    # ------------------------------------------------------------------ #
    #  Rule loading                                                        #
    # ------------------------------------------------------------------ #

    def _load_rules(self, rules_dir: Path) -> None:
        """Load ``style_rules.yaml`` from *rules_dir*.

        If the file does not exist or cannot be parsed, ``self._rules``
        remains an empty list and all transforms become no-ops.
        """
        rules_file = rules_dir / "style_rules.yaml"
        if not rules_file.exists():
            log.debug("No style_rules.yaml in %s — soul container is a no-op", rules_dir)
            return
        try:
            data = yaml.safe_load(rules_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._rules = data.get("rules", [])
            # Pre-compile regex patterns for tone_particle rules (L-19)
            for rule in self._rules:
                if rule.get("type") == "tone_particle" and rule.get("patterns"):
                    for p in rule["patterns"]:
                        try:
                            p["_compiled"] = re.compile(p["match"])
                        except re.error:
                            p["_compiled"] = None
            log.info(
                "Loaded %d style rules from %s",
                len(self._rules), rules_file,
            )
        except Exception as exc:
            log.warning("Failed to parse style_rules.yaml: %s", exc)
            self._rules = []

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def transform(self, response: str, *, is_user_facing: bool = True) -> str:
        """Apply all loaded style rules to *response*.

        Parameters
        ----------
        response:
            Raw LLM output text.
        is_user_facing:
            Set to ``False`` for self-thoughts and tool calls so that
            rules are not applied.

        Returns
        -------
        The (possibly modified) response string.
        """
        if not is_user_facing or not self._rules or not response:
            return response

        self._message_counter += 1
        result = response

        for rule in self._rules:
            rtype = rule.get("type", "")
            try:
                if rtype == "tone_particle":
                    result = self._apply_tone(result, rule)
                elif rtype == "emoji_density":
                    result = self._apply_emoji_density(result, rule)
                elif rtype == "length_guard":
                    result = self._apply_length_guard(result, rule)
                elif rtype == "catchphrase_ensure":
                    result = self._apply_catchphrase(result, rule)
                elif rtype == "style_check":
                    result = self._apply_style_checks(result, rule)
                else:
                    log.debug("Unknown rule type: %s", rtype)
            except Exception as exc:
                # A single broken rule must never crash the response path.
                log.warning("Rule %s raised %s — skipping", rtype, exc)

        return result

    @property
    def rule_count(self) -> int:
        """Number of active rules."""
        return len(self._rules)

    @property
    def message_counter(self) -> int:
        """Total messages processed since instantiation."""
        return self._message_counter

    # ------------------------------------------------------------------ #
    #  Rule implementations                                                #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _apply_tone(text: str, rule: dict[str, Any]) -> str:
        """Apply *tone_particle* rule.

        Each pattern definition in ``rule["patterns"]`` contains:
          * ``match`` — a regex pattern string
          * ``replace_candidates`` — list of replacement strings
          * ``probability`` — float 0..1, chance to apply (default 0.5)

        If *replacement* contains ``{0}`` it is substituted with the
        last character of the matched span (useful for preserving the
        trailing punctuation while swapping the particle before it).
        """
        patterns = rule.get("patterns")
        if not patterns:
            return text

        for pattern_def in patterns:
            prob: float = pattern_def.get("probability", 0.5)
            if random.random() > prob:
                continue

            match_pattern: str = pattern_def.get("match", "")
            candidates: list[str] = pattern_def.get("replace_candidates", [])
            if not match_pattern or not candidates:
                continue

            compiled = pattern_def.get("_compiled")
            if compiled is None:
                try:
                    compiled = re.compile(match_pattern)
                except re.error as exc:
                    log.debug("Bad tone regex %r: %s", match_pattern, exc)
                    continue

            m = compiled.search(text)
            if m is None:
                continue

            replacement = random.choice(candidates)
            # Support {0} placeholder for the last char of the matched span.
            if "{0}" in replacement and m.group(0):
                replacement = replacement.replace("{0}", m.group(0)[-1])

            text = text[: m.start()] + replacement + text[m.end() :]

        return text

    @staticmethod
    def _apply_emoji_density(text: str, rule: dict[str, Any]) -> str:
        """Strip excess emoji when density exceeds *max_density*.

        Density is defined as ``(emoji_match_count / character_count)``.
        When over the limit, randomly-selected emoji matches are removed
        until the count drops to ``floor(text_len * max_density)`` (at
        least 1).
        """
        max_density: float = rule.get("max_density", 0.05)
        strip_if_over: bool = rule.get("strip_if_over", True)

        matches = list(_EMOJI_RE.finditer(text))
        if not matches:
            return text

        # M-36 fix: exclude emoji from denominator
        emoji_chars = sum(m.end() - m.start() for m in matches)
        non_emoji_len = max(len(text) - emoji_chars, 1)
        current_density = len(matches) / non_emoji_len

        if current_density <= max_density or not strip_if_over:
            return text

        target_count = max(1, int(non_emoji_len * max_density))

        # Work on a list of characters for efficient random removal.
        # We remove entire match spans chosen at random until we hit
        # the target count.
        while True:
            current_matches = list(_EMOJI_RE.finditer(text))
            if len(current_matches) <= target_count or not current_matches:
                break
            victim = random.choice(current_matches)
            text = text[: victim.start()] + text[victim.end() :]

        return text

    @staticmethod
    def _apply_length_guard(text: str, rule: dict[str, Any]) -> str:
        """Truncate text that exceeds *max_chars* at a sentence boundary.

        Looks for the last sentence-ending character (。！？.!?) within
        the allowed range.  Only truncates if a boundary is found in the
        latter half; otherwise hard-cuts at *max_chars* to avoid
        losing too much content.

        If the text contains any string from ``skip_if_contains``
        (e.g. code blocks, tables, headings), truncation is skipped
        entirely — technical content should never be cut.
        """
        # Skip truncation for technical/structured content
        skip_markers = rule.get("skip_if_contains", [])
        if skip_markers and any(marker in text for marker in skip_markers):
            return text

        max_chars: int = rule.get("max_chars", 2000)
        if len(text) <= max_chars:
            return text

        truncated = text[:max_chars]
        # Find the last sentence boundary in the allowed range.
        best_idx = -1
        for sep in _SENTENCE_ENDS:
            idx = truncated.rfind(sep)
            if idx >= max_chars // 2 and idx > best_idx:
                best_idx = idx

        if best_idx > 0:
            return truncated[: best_idx + 1]
        return truncated

    def _apply_catchphrase(self, text: str, rule: dict[str, Any]) -> str:
        """Check whether a catchphrase appears on the N-th message.

        This rule is *advisory-only*: it logs a warning if none of the
        configured catchphrases appear in the response on the expected
        cadence, but never force-injects text.  Force-injecting would
        risk breaking the response mid-sentence.

        Config keys:
          * ``phrases`` — list of catchphrase strings to look for
          * ``ensure_per_n_messages`` — check interval (default 3)
        """
        phrases: list[str] = rule.get("phrases", [])
        n: int = rule.get("ensure_per_n_messages", 3)

        if not phrases or n < 1:
            return text

        if self._message_counter < n:
            return text  # Not enough messages yet
        if (self._message_counter % n) != 0:
            return text

        # Short outputs (e.g. tool confirmations) are not worth checking.
        if len(text) < 20:
            return text

        has_any = any(phrase in text for phrase in phrases)
        if not has_any:
            log.debug(
                "Catchphrase check at message #%d: none of %s found in response",
                self._message_counter,
                phrases,
            )

        return text

    # ------------------------------------------------------------------ #
    #  style_check — deterministic checks from rules/style.md             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _apply_style_checks(text: str, rule: dict[str, Any]) -> str:
        """Apply deterministic style checks (zero LLM cost).

        Each check in ``rule["checks"]`` has:
          * ``name``    — identifier for logging
          * ``pattern`` — regex to detect the violation
          * ``action``  — what to do: trim_to_3, keep_first, remove_sentence, log_only
          * ``max_count`` — (optional) max allowed matches
        """
        checks = rule.get("checks", [])
        if not checks:
            return text

        original_text = text
        fired_checks: list[str] = []

        for check in checks:
            name = check.get("name", "unnamed")
            action = check.get("action", "log_only")
            pattern = check.get("pattern")
            max_count = check.get("max_count")

            try:
                if max_count is not None:
                    # Count-based check (e.g. 主人_frequency)
                    word = check.get("word", pattern or "")
                    if word and text.count(word) > max_count:
                        if action == "trim_excess":
                            # Keep first N occurrences, replace rest
                            parts = text.split(word)
                            if len(parts) > max_count + 1:
                                kept = word.join(parts[:max_count + 1])
                                rest = word.join(parts[max_count + 1:])
                                # Replace remaining occurrences with "" or "你"
                                rest = rest.replace(word, "")
                                text = kept + rest
                        fired_checks.append(name)
                        log.debug("style_check %s: trimmed excess", name)
                    continue

                if not pattern:
                    continue

                matches = list(re.finditer(pattern, text))
                if not matches:
                    continue

                if action == "trim_to_3":
                    # Keep first 3 emoji, remove rest
                    for m in reversed(matches[3:]):
                        text = text[:m.start()] + text[m.end():]
                    fired_checks.append(name)
                elif action == "keep_first":
                    # Keep first match, remove subsequent
                    for m in reversed(matches[1:]):
                        text = text[:m.start()] + text[m.end():]
                    fired_checks.append(name)
                elif action == "remove_sentence":
                    # Remove entire sentence containing the match
                    for m in reversed(matches):
                        # Find sentence boundaries
                        start = max(text.rfind("。", 0, m.start()),
                                    text.rfind(".", 0, m.start()),
                                    text.rfind("\n", 0, m.start()), 0)
                        end = len(text)
                        for sep in ("。", ".", "\n"):
                            idx = text.find(sep, m.end())
                            if idx != -1 and idx < end:
                                end = idx + 1
                        text = text[:start] + text[end:]
                    fired_checks.append(name)
                elif action == "log_only":
                    fired_checks.append(name)
                    log.debug("style_check %s: pattern matched (log only)", name)

            except Exception as exc:
                log.debug("style_check %s error: %s", name, exc)

        # Drift scoring — log style violations for growth system feedback
        if fired_checks:
            import json as _json
            import time as _time
            drift_score = min(1.0, len(fired_checks) * 0.2 +
                            abs(len(text) - len(original_text)) / max(len(original_text), 1))
            try:
                drift_path = Path(__file__).parent.parent.parent / "data" / "logs" / "drift.jsonl"
                drift_path.parent.mkdir(parents=True, exist_ok=True)
                entry = _json.dumps({
                    "timestamp": _time.time(),
                    "drift_score": round(drift_score, 3),
                    "flags": fired_checks,
                    "chars_modified": abs(len(text) - len(original_text)),
                }, ensure_ascii=False)
                with open(drift_path, "a", encoding="utf-8") as f:
                    f.write(entry + "\n")
            except Exception:
                pass  # Drift logging must never crash response path

        return text.strip()
