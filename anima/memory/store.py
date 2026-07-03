"""Memory backend factory.

ANIMA is Postgres-only: Neon (cloud) as primary, a local Postgres as offline
failover, pgvector for semantic recall. The legacy SQLite + ChromaDB store
(``MemoryStore`` / ``DatabaseManager``) has been removed — see ``pg_store`` and
``pg_db`` for the live backend.
"""
from __future__ import annotations

from anima.utils.logging import get_logger

log = get_logger("memory.store")


class TieredMemoryStore:
    """Composite of a LOCAL working store + a CLOUD long-term/persona store
    (DISTRIBUTED_DESIGN v0.3 tiered memory).

    Every ordinary PgMemoryStore operation is delegated to ``.working`` (the hot
    path — high-frequency raw writes/reads land on the node-local DB, zero shared
    contention). The retriever additionally recalls from ``.long_term`` (cloud
    shared: consolidated salient memories + persona). Consolidation (P3c)
    periodically promotes salient working memories into ``.long_term``.

    Non-tiered deployments never construct this — ``create_memory_store`` returns a
    plain PgMemoryStore unchanged, so the current single-Neon path is untouched.
    """

    def __init__(self, working, long_term) -> None:
        self.working = working
        self.long_term = long_term
        self.tiered = working is not long_term

    @property
    def _db(self):
        # Back-compat: consumers that reach for ._db (DocumentStore, DbRecoverFixer)
        # get the LOCAL working DB.
        return self.working._db

    async def close(self) -> None:
        try:
            await self.working.close()
        finally:
            if self.tiered:
                try:
                    await self.long_term.close()
                except Exception as e:  # noqa: BLE001
                    log.debug("long_term close: %s", e)

    def __getattr__(self, name):
        # Anything not defined here (save_memory_async, search_memories_async,
        # audit_async, log_emotion_async, get_session_conversation, ...) delegates
        # to the working store — the local tier is the default for all ops.
        working = self.__dict__.get("working")
        if working is None:
            raise AttributeError(name)
        return getattr(working, name)


async def create_memory_store(db_path: str = ""):
    """Create the memory store.

    Default (non-tiered): a single Postgres store (Neon primary + local failover) —
    unchanged. When ``memory.tiered`` is enabled AND a distinct LOCAL_DATABASE_URL
    (working) + DATABASE_URL (cloud) are configured, returns a TieredMemoryStore
    (local working + cloud long-term); pg_sync is then skipped by main (the two
    tiers are independent, not a failover pair).
    """
    from anima.memory.pg_store import PgMemoryStore
    from anima.config import get

    if get("memory.tiered", False):
        from anima.secret_store import get_secret
        working_dsn = (get_secret("LOCAL_DATABASE_URL") or "").strip()
        cloud_dsn = (get_secret("DATABASE_URL") or "").strip()
        # Fold common host aliases so two DSNs that point at the SAME physical DB
        # (localhost vs 127.0.0.1) aren't treated as distinct tiers — that would make
        # the promoter write self-referential archive churn into one DB. Not a full
        # URL parse, just enough to catch the realistic misconfig.
        def _norm(dsn: str) -> str:
            return dsn.lower().replace("127.0.0.1", "localhost").replace("[::1]", "localhost")
        if working_dsn and cloud_dsn and _norm(working_dsn) != _norm(cloud_dsn):
            long_term = await PgMemoryStore.create(dsn=cloud_dsn)
            working = await PgMemoryStore.create(dsn=working_dsn)
            log.info("Memory backend: TIERED (working=local Postgres, long_term=cloud)")
            return TieredMemoryStore(working, long_term)
        log.warning("memory.tiered set but LOCAL_DATABASE_URL/DATABASE_URL missing or "
                    "identical — falling back to single store")

    log.info("Memory backend: Postgres (Neon primary + local fallback)")
    return await PgMemoryStore.create(db_path)
