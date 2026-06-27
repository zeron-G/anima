"""Postgres test-DB helpers shared by conftest fixtures and tests.

Tests run against the REAL Postgres store on a local 'anima_test' database, so
they exercise production code rather than a different SQLite dialect. Embeddings
are mocked to None in conftest, so the store stores no vectors and semantic
search falls back to deterministic ILIKE — offline, free, and stable.
"""
from __future__ import annotations

import os

_PG_TABLES = (
    "episodic_memories, emotion_log, static_knowledge, audit_log, "
    "state_snapshots, llm_usage, env_catalog, env_scan_progress"
)


def test_dsn() -> str:
    """DSN for the local test database, or "" if no local Postgres is configured."""
    local = os.environ.get("LOCAL_DATABASE_URL", "")
    if not local:
        return ""
    base, _db = local.rsplit("/", 1)
    return f"{base}/anima_test"


def ensure_test_db() -> bool:
    """Create the 'anima_test' database if missing. False if PG is unreachable."""
    local = os.environ.get("LOCAL_DATABASE_URL", "")
    if not local:
        return False
    base, _db = local.rsplit("/", 1)
    try:
        import psycopg
        with psycopg.connect(f"{base}/postgres", autocommit=True, connect_timeout=10) as c:
            if not c.execute("SELECT 1 FROM pg_database WHERE datname='anima_test'").fetchone():
                c.execute("CREATE DATABASE anima_test")
        return True
    except Exception:
        return False


async def make_test_store(*, truncate: bool = True):
    """A PgMemoryStore on the local test DB. truncate=False to keep prior data
    (e.g. simulating a restart against the same database)."""
    from anima.memory.pg_store import PgMemoryStore
    dsn = test_dsn()
    store = await PgMemoryStore.create(dsn=dsn)
    if truncate:
        store._db.write_sync(f"TRUNCATE {_PG_TABLES}")
    return store
