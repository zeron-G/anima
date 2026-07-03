"""Tier 1: Static knowledge store with node partition.

Wraps MemoryStore's static_knowledge table with node-aware query/upsert.
Each node only sees its own local knowledge + global knowledge by default.
"""

from __future__ import annotations

import json
import time
from typing import Any

from anima.utils.logging import get_logger

log = get_logger("static_store")


class StaticKnowledgeStore:
    """Tier 1 static knowledge — node-partitioned.

    Tiered memory (P3e-2): the ``global`` scope is CLOUD-authoritative — global
    entries live in the shared long-term store so knowledge converges across nodes;
    ``node:<id>`` entries stay in the local working tier (per-locus). Reads union the
    two (cloud global + local), version-wins on duplicates, and degrade to local-only
    when the cloud is unreachable. Non-tiered (no distinct ``long_term``) behaves
    exactly as before — a single store, unchanged.
    """

    def __init__(self, memory_store, node_id: str = "local", long_term=None) -> None:
        self._store = memory_store          # local working tier (or the single store)
        self._node_id = node_id
        self._my_scope = f"node:{node_id}"
        # Cloud store for the global scope. In non-tiered deployments long_term is
        # None (or the same object) → cloud IS the single store → original behavior.
        self._cloud = long_term if (long_term is not None and long_term is not memory_store) else memory_store
        self._tiered = self._cloud is not self._store

    def _store_for(self, scope: str):
        return self._cloud if (self._tiered and scope == "global") else self._store

    @staticmethod
    def _merge(*lists: list[dict], limit: int) -> list[dict]:
        """Union entries keyed by (category, key, scope); FIRST list wins a dup. Callers
        pass the authoritative tier first (cloud before local for global). We must NOT
        compare `version` across tiers — the working and cloud stores are independent
        lineages (pg_sync is off in tiered mode), so their per-row version counters
        aren't comparable; a stale legacy local row can carry a higher number than the
        fresh cloud value. Cloud is the single authority for the global scope. Sort by
        importance, cap at *limit*."""
        best: dict[tuple, dict] = {}
        for lst in lists:
            for e in lst:
                best.setdefault((e.get("category"), e.get("key"), e.get("scope")), e)
        merged = sorted(best.values(), key=lambda e: float(e.get("importance", 0) or 0), reverse=True)
        return merged[:limit]

    @staticmethod
    def _finalize(results: list[dict]) -> list[dict]:
        # M-19: Auto-deserialize JSON values
        for entry in results:
            val = entry.get("value", "")
            if isinstance(val, str) and val and val[0] in ('{', '['):
                try:
                    entry["value"] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass  # Keep as string
        return results

    def query(
        self,
        categories: list[str] | None = None,
        keywords: list[str] | None = None,
        include_other_nodes: bool = False,
        limit: int = 20,
    ) -> list[dict]:
        """Query static knowledge.

        Default: returns global + this node's entries only.
        Set include_other_nodes=True to also see other nodes' entries.
        """
        if not self._tiered:
            scopes = None if include_other_nodes else ["global", self._my_scope]
            return self._finalize(self._store.query_static_knowledge(
                categories=categories, keywords=keywords, scopes=scopes, limit=limit))

        # Tiered: local (legacy-global + my node) unioned with cloud global. Querying
        # local for "global" too keeps any pre-P3e-2 local-global rows readable during
        # migration. Cloud down → local-only (offline resilience).
        local_scopes = None if include_other_nodes else ["global", self._my_scope]
        local = self._store.query_static_knowledge(
            categories=categories, keywords=keywords, scopes=local_scopes, limit=limit)
        cloud: list[dict] = []
        try:
            cloud = self._cloud.query_static_knowledge(
                categories=categories, keywords=keywords, scopes=["global"], limit=limit)
        except Exception as e:  # noqa: BLE001
            log.warning("Tier-1 cloud static query failed (local-only): %s", e)
        return self._finalize(self._merge(cloud, local, limit=limit))

    def upsert(
        self,
        category: str,
        key: str,
        value: Any,
        scope: str = "global",
        source: str = "agent",
        importance: float = 0.5,
    ) -> None:
        """Write or update a static knowledge entry.

        Raises ValueError if trying to write to another node's scope. In tiered mode a
        ``global`` write lands in the shared cloud tier; ``node:<id>`` stays local.
        """
        if scope.startswith("node:") and scope != self._my_scope:
            raise ValueError(f"Cannot write to other node's scope: {scope}")

        value_str = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        node_id = self._node_id if scope != "global" else None
        kw = dict(category=category, key=key, value=value_str, scope=scope,
                  node_id=node_id, source=source, importance=importance, updated_at=time.time())

        if self._tiered and scope == "global":
            # Cloud is authoritative for global. If Neon is unreachable, don't lose the
            # write and don't crash the caller — record it in the local working tier.
            # Cloud-wins merge means this local copy is superseded once the cloud has
            # the key again; until then the node still sees its own write. (Auto-push
            # of an offline write back to cloud on reconnect is a mesh-time follow-up.)
            try:
                self._cloud.upsert_static_knowledge(**kw)
            except Exception as e:  # noqa: BLE001
                log.warning("Tier-1 cloud global upsert failed — wrote local fallback: %s", e)
                self._store.upsert_static_knowledge(**kw)
        else:
            self._store_for(scope).upsert_static_knowledge(**kw)

    def upsert_local(self, category: str, key: str, value: Any,
                     source: str = "env_scan", importance: float = 0.5) -> None:
        """Shorthand for writing to this node's local scope."""
        self.upsert(category, key, value, scope=self._my_scope,
                    source=source, importance=importance)

    def delete(self, category: str, key: str, scope: str = "global") -> bool:
        """Delete a static knowledge entry (tombstone). Non-tiered / node scope: single
        store. Tiered global: tombstone BOTH the cloud tier (so the deletion converges)
        AND any local-global copy (legacy pre-P3e-2, or an offline-fallback write) so a
        lingering local row can't resurrect the entry on the union read. Best-effort on
        each side — a cloud outage still lets the local tombstone succeed. Returns True
        if either side removed a row."""
        if not (self._tiered and scope == "global"):
            return self._store_for(scope).delete_static_knowledge(category, key, scope)

        cloud_ok = local_ok = False
        try:
            cloud_ok = self._cloud.delete_static_knowledge(category, key, scope)
        except Exception as e:  # noqa: BLE001
            log.warning("Tier-1 cloud tombstone failed (Neon down?): %s", e)
        try:
            local_ok = self._store.delete_static_knowledge(category, key, scope)
        except Exception as e:  # noqa: BLE001
            log.debug("local-global tombstone skipped: %s", e)
        return cloud_ok or local_ok

    def get(self, category: str, key: str) -> dict | None:
        """Get a single entry by category + key (global + this node, cloud+local)."""
        results = self.query(categories=[category], keywords=[key], limit=1)
        return results[0] if results else None

    def populate_from_environment(self, env_data: dict) -> int:
        """Populate Tier 1 from environment.md structured data.

        env_data should be a dict with hardware, paths, etc.
        Returns count of entries written.
        """
        count = 0
        for section, entries in env_data.items():
            if isinstance(entries, dict):
                for k, v in entries.items():
                    self.upsert_local(
                        category="env",
                        key=f"{section}.{k}",
                        value=v,
                        source="env_scan",
                        importance=0.6,
                    )
                    count += 1
        return count
