"""Memory backend factory.

ANIMA is Postgres-only: Neon (cloud) as primary, a local Postgres as offline
failover, pgvector for semantic recall. The legacy SQLite + ChromaDB store
(``MemoryStore`` / ``DatabaseManager``) has been removed — see ``pg_store`` and
``pg_db`` for the live backend.
"""
from __future__ import annotations

from anima.utils.logging import get_logger

log = get_logger("memory.store")


async def create_memory_store(db_path: str = ""):
    """Create the memory store (Postgres: Neon primary + local fallback).

    ``db_path`` is accepted for call-site compatibility but ignored — the
    Postgres backend connects via DATABASE_URL / LOCAL_DATABASE_URL, not a file.
    """
    from anima.memory.pg_store import PgMemoryStore
    log.info("Memory backend: Postgres (Neon primary + local fallback)")
    return await PgMemoryStore.create(db_path)
