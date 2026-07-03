"""Memory decay — importance-weighted exponential decay with consolidation.

High-importance memories decay slowly; low-importance ones fade fast.
When a memory's effective score drops below a threshold it becomes
eligible for *consolidation*: clustering nearby memories of the same
type and replacing them with a summary (LLM-generated when budget
allows, rule-truncated otherwise).
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from anima.utils.logging import get_logger

log = get_logger("memory_decay")

# Serialises promotion across invocations (idle consolidation task vs the shutdown
# flush) on one node — without it both could read the same not-yet-marked salient
# rows and write duplicate archives to the shared cloud tier.
_PROMOTE_LOCK = asyncio.Lock()


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
        # Use the PG-correct store primitives (were raw `?`-placeholder SQL on
        # store._db, which Postgres rejects → this whole task was a silent no-op).
        rows = await store.get_unconsolidated_memories_async(limit=5000)

        updates: list[tuple[str, float]] = []
        for row_dict in rows:
            effective = self.compute_effective_score(row_dict, now)
            old = float(row_dict.get("decay_score") or row_dict.get("importance", 0))
            if abs(effective - old) < 1e-6:
                continue
            updates.append((row_dict["id"], effective))   # (id, score)

        if updates:
            await store.batch_update_decay_scores_async(updates)
            log.info("Updated effective scores for %d memories", len(updates))
        return len(updates)

    # ------------------------------------------------------------------ #
    #  Consolidation                                                       #
    # ------------------------------------------------------------------ #

    async def consolidate(
        self,
        store: Any,
        llm_router: Any,
        budget_ok: bool,
        min_salience: float = 0.6,
    ) -> int:
        """PROMOTE salient local working memories into the shared CLOUD long-term
        store (tiered memory, DISTRIBUTED_DESIGN v0.3 §3). Policy is promote-the-
        important, NOT forget-the-stale:

        1. Fetch SALIENT (importance ≥ ``min_salience``), not-yet-promoted local rows.
        2. Cluster by type + 6h window.
        3. A cluster of ≥2 related salient events → one LLM/rule summary; a standalone
           salient memory → copied VERBATIM (a lone important memory must not be lost
           to a 50-char truncation).
        4. Write to the CLOUD long-term store (append-only, origin-tagged).
        5. Mark the local originals consolidated so they leave local recall — recall
           then finds them via the cloud tier (no double-count). Low-value memories
           are left local to decay out of recall naturally.

        Tiered: local = ``store.working``, cloud = ``store.long_term``. Non-tiered:
        local IS cloud → no-op (nothing to promote to). Cloud write happens BEFORE
        the local mark — a crash between them re-promotes (rare duplicate) rather
        than loses the memory (accepted tradeoff). Marking consolidated drops the
        local original from BOTH recall stages (recency and — see retriever
        _is_consolidated — semantic), so recall of a promoted memory comes from the
        cloud copy; the embedder gate below keeps that copy recallable.

        Returns the number of local memories promoted.
        """
        local = getattr(store, "working", store)
        cloud = getattr(store, "long_term", store)

        # Promotion only means something across TWO distinct tiers. In the default
        # single-store (non-tiered) path local IS cloud — promoting would just create
        # archive duplicates and hide the originals (churn, no benefit). Passive decay
        # (recall's decay_score floor) already handles forgetting there, so no-op.
        if local is cloud:
            return 0

        # A promoted memory is recalled from its CLOUD copy only. When embeddings are
        # ON (key present), that copy must carry a non-NULL embedding to be findable
        # via pgvector — so if the embedder is momentarily down we must NOT promote,
        # or the memory becomes unrecallable from every tier (local original is about
        # to be marked consolidated). One cheap probe gates the whole cycle. With no
        # key, recall uses ILIKE and a NULL-embedding cloud copy is still findable.
        from anima.memory import embedder
        if embedder.openai_available() and await embedder.embed_openai("healthcheck") is None:
            log.warning("Promotion skipped this cycle — embedder key present but unavailable")
            return 0

        async with _PROMOTE_LOCK:
            salient = await local.get_salient_unconsolidated_async(min_salience, limit=200)
            if not salient:
                return 0

            clusters = self._cluster_by_topic(salient)
            promoted = 0
            for cluster in clusters:
                ids = [m["id"] for m in cluster]
                if len(cluster) >= 2:
                    content = await self._summarise_cluster(cluster, llm_router, budget_ok)
                    meta = {"promoted": True, "cluster_type": cluster[0].get("type", "observation")}
                else:
                    content = cluster[0].get("content", "")
                    meta = {"promoted": True, "verbatim": True, "orig_type": cluster[0].get("type", "")}
                if not content:
                    continue
                await cloud.archive_to_knowledge_async(content, ids, metadata=meta)  # cloud, origin-tagged
                await local.mark_consolidated_async(ids)                            # then mark local
                promoted += len(cluster)

        if promoted:
            log.info("Promoted %d salient memories to long-term (%d entries)",
                     promoted, len(clusters))
        return promoted

    async def flush_promote(self, store: Any, llm_router: Any = None) -> int:
        """Best-effort promotion flush (e.g. on shutdown): push any pending salient
        local memories to the shared cloud tier before the node goes down."""
        # If an idle consolidation is mid-flight, don't run a second concurrent
        # promoter (it would double-write archives) and don't block shutdown waiting
        # on it — just skip; the in-flight run covers the pending memories.
        if _PROMOTE_LOCK.locked():
            log.debug("flush_promote skipped — consolidation already running")
            return 0
        try:
            return await self.consolidate(store, llm_router, budget_ok=False)
        except Exception as exc:  # noqa: BLE001 — flush must never block shutdown
            log.debug("flush_promote skipped: %s", exc)
            return 0

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
