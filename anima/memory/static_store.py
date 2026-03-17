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
    """Tier 1 static knowledge — node-partitioned."""

    def __init__(self, memory_store, node_id: str = "local") -> None:
        self._store = memory_store
        self._node_id = node_id
        self._my_scope = f"node:{node_id}"

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
        scopes: list[str] | None = None
        if not include_other_nodes:
            scopes = ["global", self._my_scope]

        return self._store.query_static_knowledge(
            categories=categories,
            keywords=keywords,
            scopes=scopes,
            limit=limit,
        )

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

        Raises ValueError if trying to write to another node's scope.
        """
        if scope.startswith("node:") and scope != self._my_scope:
            raise ValueError(f"Cannot write to other node's scope: {scope}")

        value_str = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        node_id = self._node_id if scope != "global" else None

        self._store.upsert_static_knowledge(
            category=category,
            key=key,
            value=value_str,
            scope=scope,
            node_id=node_id,
            source=source,
            importance=importance,
            updated_at=time.time(),
        )

    def upsert_local(self, category: str, key: str, value: Any,
                     source: str = "env_scan", importance: float = 0.5) -> None:
        """Shorthand for writing to this node's local scope."""
        self.upsert(category, key, value, scope=self._my_scope,
                    source=source, importance=importance)

    def delete(self, category: str, key: str, scope: str = "global") -> bool:
        """Delete a static knowledge entry."""
        return self._store.delete_static_knowledge(category, key, scope)

    def get(self, category: str, key: str) -> dict | None:
        """Get a single entry by category + key (checks global first, then node)."""
        results = self._store.query_static_knowledge(
            categories=[category],
            keywords=[key],
            scopes=["global", self._my_scope],
            limit=1,
        )
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
