"""Keyword-triggered context injection — inspired by SillyTavern World Info.

Scans recent messages for keywords defined in ``_index.yaml``.
When matched, loads the corresponding ``.md`` files and injects their
content into the prompt.  Supports sticky persistence, cooldown,
priority-based ordering, and a per-scan token budget.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from anima.llm.token_budget import count_tokens, truncate_to_tokens
from anima.utils.logging import get_logger
from anima.utils.path_safety import validate_path_within

log = get_logger("lorebook")


# ------------------------------------------------------------------ #
#  Result type                                                         #
# ------------------------------------------------------------------ #

@dataclass
class LorebookHit:
    """Result of a lorebook scan."""

    entries: list[dict] = field(default_factory=list)
    hit_ids: set[str] = field(default_factory=set)
    total_tokens: int = 0


# ------------------------------------------------------------------ #
#  Engine                                                              #
# ------------------------------------------------------------------ #

class LorebookEngine:
    """Keyword-triggered context injection engine.

    Parameters
    ----------
    lorebook_dir:
        Directory containing ``_index.yaml`` and the ``.md`` content files.
    """

    def __init__(self, lorebook_dir: Path) -> None:
        self._dir = lorebook_dir
        self._index: list[dict] = []
        self._sticky_state: dict[str, int] = {}   # file -> remaining sticky rounds
        self._cooldown_state: dict[str, float] = {}  # file -> last trigger timestamp
        self._cache: dict[str, str] = {}           # file -> content cache
        self._load_index()

    # ------------------------------------------------------------------ #
    #  Index loading                                                      #
    # ------------------------------------------------------------------ #

    def _load_index(self) -> None:
        """Load ``_index.yaml``.  Sets ``self._index = []`` on failure."""
        index_path = self._dir / "_index.yaml"
        if not index_path.exists():
            log.warning("Lorebook index not found: %s", index_path)
            self._index = []
            return
        try:
            with open(index_path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if isinstance(data, dict):
                self._index = data.get("entries", [])
            else:
                self._index = []
            log.info(
                "Lorebook loaded: %d entries from %s",
                len(self._index), index_path,
            )
        except Exception:
            log.exception("Failed to load lorebook index: %s", index_path)
            self._index = []

    # ------------------------------------------------------------------ #
    #  Scan                                                               #
    # ------------------------------------------------------------------ #

    def scan(self, messages: list[dict], budget: int) -> LorebookHit:
        """Scan *messages* for keyword triggers and return matched entries.

        Algorithm
        ---------
        1. For each **enabled** entry:
           a. If sticky > 0 for this entry, include it (decrement sticky).
           b. Check cooldown — skip if still cooling down.
           c. Match primary keywords in ``messages[-scan_depth:]``
              (case-insensitive).
           d. If ``secondary_keywords`` is set, require **all** secondary
              keywords to also match.
        2. Sort matched entries by priority (descending).
        3. Load content from ``.md`` files; truncate per-entry to
           ``max_tokens``.
        4. Fit within total *budget*.
        5. Update sticky / cooldown state.
        """
        if not self._index:
            return LorebookHit()

        now = time.time()
        candidates: list[dict] = []

        for entry in self._index:
            if not entry.get("enabled", True):
                continue

            file_key = entry.get("file", "")
            entry_id = entry.get("id", file_key)
            scan_depth = entry.get("scan_depth", 4)
            cooldown = entry.get("cooldown", 0)

            # --- (a) sticky carry-over -----------------------------------
            remaining_sticky = self._sticky_state.get(file_key, 0)
            if remaining_sticky > 0:
                self._sticky_state[file_key] = remaining_sticky - 1
                candidates.append(entry)
                continue

            # --- (b) cooldown check --------------------------------------
            last_trigger = self._cooldown_state.get(file_key, 0.0)
            if cooldown > 0 and (now - last_trigger) < cooldown:
                continue

            # --- (c) primary keyword match -------------------------------
            window = messages[-scan_depth:] if scan_depth else messages
            window_text = self._flatten_messages(window).lower()

            keywords = entry.get("keywords", [])
            if not keywords:
                continue

            primary_hit = any(kw.lower() in window_text for kw in keywords)
            if not primary_hit:
                continue

            # --- (d) secondary keyword gate ------------------------------
            secondary = entry.get("secondary_keywords", [])
            if secondary:
                if not all(kw.lower() in window_text for kw in secondary):
                    continue

            candidates.append(entry)

        # --- 2. Sort by priority (descending) ----------------------------
        candidates.sort(key=lambda e: e.get("priority", 0), reverse=True)

        # --- 3 + 4. Load content, truncate, fit budget -------------------
        hit = LorebookHit()
        remaining_budget = budget

        for entry in candidates:
            file_key = entry.get("file", "")
            entry_id = entry.get("id", file_key)
            max_tokens = entry.get("max_tokens", 200)

            content = self._load_content(file_key)
            if not content:
                continue

            # Per-entry truncation
            content = truncate_to_tokens(content, max_tokens)
            tokens = count_tokens(content)

            # Global budget check
            if tokens > remaining_budget:
                log.debug(
                    "Lorebook budget exhausted: %s needs %d, only %d left",
                    file_key, tokens, remaining_budget,
                )
                break

            remaining_budget -= tokens
            hit.entries.append({
                "id": entry_id,
                "file": file_key,
                "content": content,
                "tokens": tokens,
                "priority": entry.get("priority", 0),
            })
            hit.hit_ids.add(entry_id)
            hit.total_tokens += tokens

            # --- 5. Update sticky / cooldown state -----------------------
            sticky = entry.get("sticky", 0)
            if sticky > 0:
                self._sticky_state[file_key] = sticky
            self._cooldown_state[file_key] = time.time()

        log.debug(
            "Lorebook scan: %d entries matched, %d tokens used (budget %d)",
            len(hit.entries), hit.total_tokens, budget,
        )
        return hit

    # ------------------------------------------------------------------ #
    #  Content loading                                                     #
    # ------------------------------------------------------------------ #

    def _load_content(self, filename: str) -> str:
        """Load a ``.md`` file from the lorebook directory with caching."""
        if filename in self._cache:
            return self._cache[filename]

        filepath = self._dir / filename
        try:
            validate_path_within(filepath, self._dir)
        except Exception:
            log.warning("Lorebook path traversal blocked: %s", filename)
            self._cache[filename] = ""
            return ""
        if not filepath.exists():
            log.warning("Lorebook file not found: %s", filepath)
            self._cache[filename] = ""
            return ""
        try:
            content = filepath.read_text(encoding="utf-8").strip()
            self._cache[filename] = content
            return content
        except Exception:
            log.exception("Failed to read lorebook file: %s", filepath)
            self._cache[filename] = ""
            return ""

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _flatten_messages(messages: list[dict]) -> str:
        """Concatenate message contents into a single searchable string."""
        parts: list[str] = []
        for msg in messages:
            content = msg.get("content", "")
            if content:
                parts.append(content)
        return " ".join(parts)
