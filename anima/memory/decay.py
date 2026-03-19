"""Memory decay — importance-weighted exponential decay with consolidation.

High-importance memories decay slowly; low-importance ones fade fast.
When a memory's effective score drops below a threshold it becomes
eligible for *consolidation*: clustering nearby memories of the same
type and replacing them with a summary (LLM-generated when budget
allows, rule-truncated otherwise).
"""

from __future__ import annotations

import json
import math
import time
from typing import Any

from anima.utils.logging import get_logger

log = get_logger("memory_decay")


class MemoryDecay:
    """Compute time-decayed importance scores and consolidate stale memories.

    The effective score formula::

        effective = importance * e^(-lambda_base / importance * dt_hours)
                    * (1 + 0.1 * access_count)

    where ``importance`` is clamped to >= 0.1 to prevent division by zero.
    """

    # Base decay rate per type — higher = faster forgetting
    LAMBDA_BASE: dict[str, float] = {
        "chat_user":      0.03,
        "chat_assistant": 0.03,
        "thought":        0.04,
        "action":         0.02,
        "observation":    0.06,
    }

    # Default for unknown types
    _DEFAULT_LAMBDA = 0.04

    def __init__(
        self,
        cluster_window_hours: float = 6.0,
        consolidation_threshold: float = 0.1,
    ):
        # L-07/L-08: configurable decay threshold and cluster window
        self.cluster_window_hours = cluster_window_hours
        self.cluster_window_secs = cluster_window_hours * 3600.0
        self.consolidation_threshold = consolidation_threshold

    # ------------------------------------------------------------------ #
    #  Effective score                                                      #
    # ------------------------------------------------------------------ #

    def compute_effective_score(
        self,
        memory: dict[str, Any],
        now: float | None = None,
    ) -> float:
        """Compute the time-decayed effective score for a single memory.

        Parameters
        ----------
        memory:
            A dict with at least ``importance``, ``type``, ``created_at``,
            ``access_count``.  Typically a row from ``episodic_memories``.
        now:
            Current timestamp (``time.time()``).  Defaults to *now*.

        Returns
        -------
        Effective score in [0, +inf) — though in practice values above
        1.0 are only possible with very high access counts.
        """
        if now is None:
            now = time.time()

        importance = max(float(memory.get("importance", 0.5)), 0.1)
        mem_type = memory.get("type", "observation")
        created_at = float(memory.get("created_at", now))
        access_count = int(memory.get("access_count", 0))

        dt_hours = max((now - created_at) / 3600.0, 0.0)
        lam = self.LAMBDA_BASE.get(mem_type, self._DEFAULT_LAMBDA)

        decay = math.exp(-lam / importance * dt_hours)
        access_boost = 1.0 + 0.1 * access_count

        return importance * decay * access_boost

    # ------------------------------------------------------------------ #
    #  Batch update                                                        #
    # ------------------------------------------------------------------ #

    async def update_all_scores(self, store: Any) -> int:
        """Recompute effective scores for all unconsolidated memories.

        Reads every row from ``episodic_memories`` that has not been
        consolidated (no ``consolidated`` flag in metadata), recomputes
        the effective score, and writes the new importance back.

        Parameters
        ----------
        store:
            A :class:`~anima.memory.store.MemoryStore` instance.

        Returns
        -------
        Number of memories updated.
        """
        now = time.time()
        rows = store._conn.execute(
            "SELECT id, type, importance, created_at, access_count, "
            "decay_score, metadata_json FROM episodic_memories"
        ).fetchall()

        count = 0
        for row in rows:
            row_dict = dict(row)
            meta = _parse_meta(row_dict.get("metadata_json", "{}"))
            if meta.get("consolidated"):
                continue

            effective = self.compute_effective_score(row_dict, now)

            # Only write if the score actually changed
            old = float(row_dict.get("decay_score") or row_dict.get("importance", 0))
            if abs(effective - old) < 1e-6:
                continue

            store._conn.execute(
                "UPDATE episodic_memories SET decay_score = ? WHERE id = ?",
                (effective, row_dict["id"]),
            )
            count += 1

        if count:
            store._conn.commit()
            log.info("Updated effective scores for %d memories", count)
        return count

    # ------------------------------------------------------------------ #
    #  Consolidation                                                       #
    # ------------------------------------------------------------------ #

    async def consolidate(
        self,
        store: Any,
        llm_router: Any,
        budget_ok: bool,
        threshold: float = 0.1,
    ) -> int:
        """Consolidate stale memories whose effective score < *threshold*.

        Steps
        -----
        1. Fetch memories below the threshold that are not yet consolidated.
        2. Cluster them by type + 6-hour time windows.
        3. Summarise each cluster:
           - If *budget_ok*, use the LLM (``tier=2``) for a concise summary.
           - Otherwise, rule-truncate: first 50 characters of each memory.
        4. Archive the summary as a new ``knowledge`` memory.
        5. Mark the originals as consolidated.

        Returns
        -------
        Number of original memories consolidated.
        """
        now = time.time()
        rows = store._conn.execute(
            "SELECT id, type, content, importance, created_at, access_count, "
            "metadata_json FROM episodic_memories "
            "ORDER BY created_at ASC"
        ).fetchall()

        # Filter to stale, unconsolidated memories
        stale: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            meta = _parse_meta(d.get("metadata_json", "{}"))
            if meta.get("consolidated"):
                continue
            effective = self.compute_effective_score(d, now)
            if effective < threshold:
                d["_meta"] = meta
                stale.append(d)

        if not stale:
            return 0

        clusters = self._cluster_by_topic(stale)
        total_consolidated = 0

        for cluster in clusters:
            if len(cluster) < 2:
                # L-06: Clean up very old singletons (effective_score < 0.05)
                for mem in cluster:
                    eff = self.compute_effective_score(mem, now)
                    if eff < 0.05:
                        meta = mem.get("_meta", {})
                        meta["consolidated"] = True
                        meta["singleton_cleaned"] = True
                        store._conn.execute(
                            "UPDATE episodic_memories SET metadata_json = ? WHERE id = ?",
                            (json.dumps(meta), mem["id"]),
                        )
                continue

            summary = await self._summarise_cluster(
                cluster, llm_router, budget_ok,
            )
            mem_type = cluster[0].get("type", "observation")

            # Archive the summary as a knowledge entry
            store.save_memory(
                content=summary,
                type="knowledge",
                importance=0.4,
                metadata={"consolidated_from": [m["id"] for m in cluster]},
                tags=["consolidated", mem_type],
            )

            # C-07 fix: use explicit transaction for consolidation
            store._conn.execute("BEGIN IMMEDIATE")
            try:
                # Mark originals as consolidated
                for mem in cluster:
                    meta = mem.get("_meta", {})
                    meta["consolidated"] = True
                    store._conn.execute(
                        "UPDATE episodic_memories SET metadata_json = ? WHERE id = ?",
                        (json.dumps(meta), mem["id"]),
                    )
                store._conn.commit()
            except Exception:
                store._conn.rollback()
                raise
            total_consolidated += len(cluster)

        if total_consolidated:
            log.info(
                "Consolidated %d memories into %d summaries",
                total_consolidated,
                sum(1 for c in clusters if len(c) >= 2),
            )
        return total_consolidated

    # ------------------------------------------------------------------ #
    #  Clustering                                                          #
    # ------------------------------------------------------------------ #

    def _cluster_by_topic(
        self,
        memories: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        """Group memories by same type and within 6-hour time windows.

        Memories must be sorted by ``created_at`` ascending.

        Algorithm: iterate sorted memories.  For each one, if it shares
        the same ``type`` as the current cluster's head AND falls within
        ``cluster_window_secs`` of the cluster's first entry, append
        it.  Otherwise start a new cluster.
        """
        if not memories:
            return []

        # Sort defensively (caller should pre-sort, but be safe)
        by_time = sorted(memories, key=lambda m: float(m.get("created_at", 0)))

        clusters: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = [by_time[0]]

        for mem in by_time[1:]:
            head = current[0]
            same_type = mem.get("type") == head.get("type")
            dt = float(mem.get("created_at", 0)) - float(head.get("created_at", 0))
            within_window = dt <= self.cluster_window_secs

            if same_type and within_window:
                current.append(mem)
            else:
                clusters.append(current)
                current = [mem]

        clusters.append(current)
        return clusters

    # ------------------------------------------------------------------ #
    #  Summarisation                                                       #
    # ------------------------------------------------------------------ #

    async def _summarise_cluster(
        self,
        cluster: list[dict[str, Any]],
        llm_router: Any,
        budget_ok: bool,
    ) -> str:
        """Produce a summary string for a cluster of memories.

        When *budget_ok* is True, asks the LLM for a concise summary.
        Otherwise falls back to a rule-based truncation (first 50 chars
        of each memory, joined by newline).
        """
        if budget_ok and llm_router is not None:
            return await self._llm_summarise(cluster, llm_router)
        return self._rule_summarise(cluster)

    async def _llm_summarise(
        self,
        cluster: list[dict[str, Any]],
        llm_router: Any,
    ) -> str:
        """Ask the LLM to summarise a memory cluster."""
        snippets = "\n".join(
            f"- [{m.get('type', '?')}] {m.get('content', '')[:200]}"
            for m in cluster
        )
        messages = [
            {
                "role": "user",
                "content": (
                    "Summarise the following memory entries into one concise "
                    "paragraph (max 150 characters).  Preserve key facts, "
                    "drop filler.\n\n" + snippets
                ),
            },
        ]
        try:
            result = await llm_router.call(messages, tier=2, temperature=0.3)
            if result:
                return result.strip()
        except Exception as exc:
            log.warning("LLM summarisation failed: %s — falling back to rule", exc)

        return self._rule_summarise(cluster)

    @staticmethod
    def _rule_summarise(cluster: list[dict[str, Any]]) -> str:
        """Rule-based fallback: first 50 chars of each memory."""
        parts = [
            m.get("content", "")[:50].replace("\n", " ")
            for m in cluster
        ]
        return "\n".join(f"- {p}" for p in parts if p)


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #

def _parse_meta(raw: str | dict) -> dict:
    """Safely parse metadata_json (may already be a dict)."""
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        return {}
