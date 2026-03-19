"""Unified memory retrieval with RRF fusion across all tiers.

Pipeline
--------
1. **Tier 0** — Always load (core identity + user profile + feelings).
2. **Tier 1** — Query by event type (static knowledge with node partition).
3. **Lorebook** — Keyword scan over recent messages -> hit IDs.
4. **Tier 3** — Dual-channel (semantic + time-weighted recent).
5. **Tier 2** — Semantic search (exclude Lorebook hit IDs).
6. **Unified RRF fusion** of Lorebook + Tier 3 + Tier 2.
7. **Token budget truncation**.
8. **Touch** ``access_count`` for retrieved memories.
"""

from __future__ import annotations

import json
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from anima.utils.logging import get_logger

log = get_logger("memory_retriever")

# RRF constant — standard value from the original paper.
_RRF_K: int = 60


# ------------------------------------------------------------------ #
#  Protocols for pluggable stores                                      #
# ------------------------------------------------------------------ #

@runtime_checkable
class MemoryStoreProto(Protocol):
    """Minimal interface expected from the episodic memory store."""

    def get_recent_memories(self, limit: int = 10, type: str | None = None) -> list[dict]: ...

    def search_memories(self, query: str | None = None, type: str | None = None,
                        limit: int = 10) -> list[dict]: ...


@runtime_checkable
class StaticStoreProto(Protocol):
    """Minimal interface for the static-knowledge (Tier 1) store."""

    def query(self, categories: list[str]) -> list[dict]: ...


@runtime_checkable
class LorebookProto(Protocol):
    """Minimal interface for the lorebook scanner."""

    def scan(self, messages: list[dict], budget: int) -> Any: ...


@runtime_checkable
class DecayProto(Protocol):
    """Minimal interface for the time-decay scorer."""

    def compute_effective_score(self, memory: dict, now: float) -> float: ...


# ------------------------------------------------------------------ #
#  Data containers                                                     #
# ------------------------------------------------------------------ #

@dataclass
class MemoryContext:
    """Container for retrieval results across all tiers."""

    core: str = ""
    """Tier 0 content (identity + user profile + feelings)."""

    static: list[dict[str, Any]] = field(default_factory=list)
    """Tier 1 results (static knowledge)."""

    episodic: list[dict[str, Any]] = field(default_factory=list)
    """Tier 3 + Lorebook + Tier 2 fused results."""

    core_tokens: int = 0
    static_tokens: int = 0
    total_tokens: int = 0

    all_ids: list[str] = field(default_factory=list)
    """IDs of all episodic memories included in this context."""


# ------------------------------------------------------------------ #
#  Event type -> static-knowledge category mapping                     #
# ------------------------------------------------------------------ #

_EVENT_CATEGORY_MAP: dict[str, list[str]] = {
    "USER_MESSAGE":   ["project", "contact"],
    "SELF_THINKING":  ["env", "project", "config"],
    "STARTUP":        ["env", "config", "project"],
    "EVOLUTION":      ["project"],
    "SCHEDULED_TASK": ["project", "config"],
    "IDLE_TASK":      ["env", "project"],
}


# ------------------------------------------------------------------ #
#  MemoryRetriever                                                     #
# ------------------------------------------------------------------ #

class MemoryRetriever:
    """Unified memory retrieval with Reciprocal Rank Fusion.

    All four store dependencies are optional — when ``None`` the
    corresponding pipeline stage is simply skipped.  This lets the
    retriever work in degraded mode during early bootstrap (before
    ChromaDB or the lorebook are initialised).

    Parameters
    ----------
    memory_store:
        Episodic memory backend (``MemoryStore``).
    static_store:
        Static knowledge backend (Tier 1).
    lorebook:
        Lorebook scanner.
    decay:
        Time-decay scorer used for Tier 3 recency weighting.
    """

    def __init__(
        self,
        memory_store: MemoryStoreProto | None = None,
        static_store: StaticStoreProto | None = None,
        lorebook: LorebookProto | None = None,
        decay: DecayProto | None = None,
    ) -> None:
        self._store = memory_store
        self._static = static_store
        self._lorebook = lorebook
        self._decay = decay

        # M-20: configurable RRF channel weights
        self._rrf_weights = {
            "lorebook": 1.5,
            "recent": 1.0,
            "knowledge": 0.8,
        }

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    async def retrieve(
        self,
        query: str,
        event_type: str,
        recent_messages: list[dict[str, Any]] | None = None,
        max_tokens: int = 1500,
    ) -> MemoryContext:
        """Run the full retrieval pipeline and return a :class:`MemoryContext`.

        Parameters
        ----------
        query:
            Free-text search query (typically the latest user message).
        event_type:
            One of the keys in ``_EVENT_CATEGORY_MAP`` — controls which
            static-knowledge categories are fetched.
        recent_messages:
            Last few conversation turns, passed to the lorebook scanner.
        max_tokens:
            Hard ceiling on total token usage across all tiers.
        """
        from anima.llm.token_budget import count_tokens

        ctx = MemoryContext()

        # ---- 1. Tier 0: core identity (always loaded) ----------------
        ctx.core = self._load_core_memory()
        ctx.core_tokens = count_tokens(ctx.core)
        remaining = max(max_tokens - ctx.core_tokens, 0)

        # ---- 2. Tier 1: static knowledge by event type ---------------
        if self._static is not None and remaining > 0:
            categories = _EVENT_CATEGORY_MAP.get(event_type, [])
            if categories:
                try:
                    ctx.static = self._static.query(categories=categories)
                except Exception as exc:
                    log.warning("Tier 1 query failed: %s", exc)
                ctx.static_tokens = sum(
                    count_tokens(str(s.get("value", ""))) for s in ctx.static
                )
                remaining = max(remaining - ctx.static_tokens, 0)

        if remaining == 0:
            ctx.total_tokens = ctx.core_tokens + ctx.static_tokens
            return ctx

        # ---- 3-6: Lorebook + Tier 3 + Tier 2 -> RRF fusion ----------
        all_candidates: list[dict[str, Any]] = []
        lorebook_hit_ids: set[str] = set()

        # 3. Lorebook keyword scan
        self._stage_lorebook(
            all_candidates, lorebook_hit_ids,
            recent_messages, remaining,
        )

        # 4. Tier 3: recent important (time-weighted)
        self._stage_recent(all_candidates)

        # 5. Tier 2: semantic / LIKE search (exclude lorebook IDs)
        self._stage_semantic(all_candidates, lorebook_hit_ids, query, event_type)

        # 6. RRF fusion scoring
        self._score_rrf(all_candidates)

        # 7. Token budget truncation
        used = 0
        for cand in all_candidates:
            tok = count_tokens(cand.get("content", ""))
            if used + tok > remaining:
                break
            ctx.episodic.append(cand)
            used += tok

        # 8. Touch access_count for non-lorebook results
        touch_ids = [
            c["id"]
            for c in ctx.episodic
            if c.get("source") != "lorebook" and c.get("id")
        ]
        if touch_ids:
            self._touch(touch_ids)

        ctx.all_ids = [c["id"] for c in ctx.episodic if c.get("id")]
        ctx.total_tokens = ctx.core_tokens + ctx.static_tokens + used

        log.info(
            "Retrieved: core=%d tok, static=%d entries, episodic=%d entries, "
            "lorebook=%d hits, total=%d tok (event=%s)",
            ctx.core_tokens, len(ctx.static), len(ctx.episodic),
            len(lorebook_hit_ids), ctx.total_tokens, event_type,
        )
        return ctx

    # ------------------------------------------------------------------ #
    #  Pipeline stages                                                     #
    # ------------------------------------------------------------------ #

    def _stage_lorebook(
        self,
        candidates: list[dict[str, Any]],
        hit_ids: set[str],
        recent_messages: list[dict[str, Any]] | None,
        budget_remaining: int,
    ) -> None:
        """Stage 3: lorebook keyword scan over recent messages."""
        if self._lorebook is None or not recent_messages:
            return
        try:
            lore_result = self._lorebook.scan(
                messages=recent_messages[-4:],
                budget=budget_remaining // 3,
            )
        except Exception as exc:
            log.warning("Lorebook scan failed: %s", exc)
            return

        # Collect hit IDs so Tier 2 can skip them later.
        lore_hit_ids: set[str] = getattr(lore_result, "hit_ids", set())
        hit_ids.update(lore_hit_ids)

        entries: list[dict[str, Any]] = getattr(lore_result, "entries", [])
        for rank, entry in enumerate(entries):
            candidates.append({
                "id": entry.get("id", f"lore_{rank}"),
                "content": entry.get("content", ""),
                "source": "lorebook",
                "rank_lorebook": rank,
            })

    def _stage_recent(self, candidates: list[dict[str, Any]]) -> None:
        """Stage 4: Tier 3 time-weighted recent memories."""
        if self._store is None or self._decay is None:
            return

        now = _time.time()
        try:
            recent_mems = self._store.get_recent_memories(limit=30, type=None)
        except Exception as exc:
            log.warning("Tier 3 recent-memories query failed: %s", exc)
            return

        scored: list[dict[str, Any]] = []
        for mem in recent_mems:
            # Check consolidated status from metadata_json
            meta_raw = mem.get("metadata_json", "{}")
            if isinstance(meta_raw, str):
                try:
                    meta = json.loads(meta_raw)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            else:
                meta = meta_raw or {}
            if meta.get("consolidated"):
                continue
            # If decay_score is already computed, use it directly
            decay_score = mem.get("decay_score")
            if decay_score is not None:
                eff = float(decay_score)
            else:
                try:
                    eff = self._decay.compute_effective_score(mem, now)
                except Exception:
                    continue
            if eff >= 0.2:
                mem["_eff"] = eff
                scored.append(mem)

        scored.sort(key=lambda x: x["_eff"], reverse=True)

        for rank, mem in enumerate(scored[:10]):
            mid = mem.get("id", "")
            # Merge into existing candidate if already added by lorebook.
            existing = _find_candidate(candidates, mid)
            if existing is not None:
                existing["rank_recent"] = rank
            else:
                candidates.append({
                    "id": mid,
                    "content": mem.get("content", ""),
                    "source": "recent",
                    "rank_recent": rank,
                })

    def _stage_semantic(
        self,
        candidates: list[dict[str, Any]],
        lorebook_hit_ids: set[str],
        query: str,
        event_type: str,
    ) -> None:
        """Stage 5: Tier 2 semantic search (SQLite LIKE fallback).

        ChromaDB vector search is delegated to
        ``MemoryStore.search_memories`` which tries ChromaDB first,
        then falls back to SQLite ``LIKE``.
        """
        if self._store is None or not query:
            return
        # Only run semantic search on user-facing events.
        if event_type != "USER_MESSAGE":
            return

        try:
            knowledge_hits = self._store.search_memories(query=query, limit=5)
        except Exception as exc:
            log.warning("Tier 2 semantic search failed: %s", exc)
            return

        for rank, mem in enumerate(knowledge_hits):
            mid = mem.get("id", "")
            if mid in lorebook_hit_ids:
                continue  # De-duplicate with lorebook.
            existing = _find_candidate(candidates, mid)
            if existing is not None:
                existing["rank_knowledge"] = rank
            else:
                candidates.append({
                    "id": mid,
                    "content": mem.get("content", ""),
                    "source": "knowledge",
                    "rank_knowledge": rank,
                })

    # ------------------------------------------------------------------ #
    #  RRF scoring                                                         #
    # ------------------------------------------------------------------ #

    def _score_rrf(self, candidates: list[dict[str, Any]]) -> None:
        """Compute Reciprocal Rank Fusion score and sort in-place.

        Channel weights (configurable via ``self._rrf_weights``):
          * lorebook  — 1.5x (curated content is high-signal)
          * recent    — 1.0x (time-weighted recall)
          * knowledge — 0.8x (broad semantic similarity)
        """
        w = self._rrf_weights
        for cand in candidates:
            score = 0.0
            rank_lore = cand.get("rank_lorebook")
            if rank_lore is not None:
                score += w["lorebook"] / (_RRF_K + rank_lore + 1)
            rank_recent = cand.get("rank_recent")
            if rank_recent is not None:
                score += w["recent"] / (_RRF_K + rank_recent + 1)
            rank_know = cand.get("rank_knowledge")
            if rank_know is not None:
                score += w["knowledge"] / (_RRF_K + rank_know + 1)
            cand["rrf_score"] = score

        candidates.sort(key=lambda x: x.get("rrf_score", 0.0), reverse=True)

    # ------------------------------------------------------------------ #
    #  Tier 0 loader                                                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _load_core_memory() -> str:
        """Load Tier 0: ``identity/core.md`` + ``user_profile.md`` + ``feelings.md``.

        Feelings is capped to the last ~500 tokens to avoid starving
        other memory tiers of their token budget.
        """
        from anima.config import agent_dir, data_dir
        from anima.llm.token_budget import truncate_to_tokens

        _FEELINGS_MAX_TOKENS = 500  # cap feelings to leave room for Tier 1-3

        agent = agent_dir()
        data = data_dir()

        identity_path = agent / "identity" / "core.md"
        if not identity_path.exists():
            identity_path = agent / "identity" / "soul.md"

        candidates: list[tuple[Path, int]] = [
            (identity_path, 0),              # no cap
            (data / "user_profile.md", 0),   # no cap
            (agent / "memory" / "feelings.md", _FEELINGS_MAX_TOKENS),
        ]

        parts: list[str] = []
        for path, max_tok in candidates:
            try:
                if path.exists():
                    content = path.read_text(encoding="utf-8").strip()
                    if content:
                        if max_tok > 0:
                            # Take the TAIL (most recent feelings)
                            lines = content.split("\n")
                            tail = "\n".join(lines[-40:])  # last ~40 lines
                            content = truncate_to_tokens(tail, max_tok)
                        parts.append(content)
            except OSError as exc:
                log.debug("Could not read %s: %s", path, exc)

        return "\n\n".join(parts)

    # ------------------------------------------------------------------ #
    #  Access-count touch                                                  #
    # ------------------------------------------------------------------ #

    def _touch(self, ids: list[str]) -> None:
        """Update ``access_count`` and ``last_accessed`` for retrieved memories."""
        if self._store is None or not ids:
            return
        try:
            self._store.touch_memories(ids)
            log.debug("Touched %d memories (access_count++)", len(ids))
        except Exception as exc:
            log.warning("touch_memories failed: %s", exc)


# ------------------------------------------------------------------ #
#  Module-level helpers                                                #
# ------------------------------------------------------------------ #

def _find_candidate(
    candidates: list[dict[str, Any]],
    candidate_id: str,
) -> dict[str, Any] | None:
    """Return the first candidate dict whose ``id`` matches, or ``None``."""
    if not candidate_id:
        return None
    for cand in candidates:
        if cand.get("id") == candidate_id:
            return cand
    return None
