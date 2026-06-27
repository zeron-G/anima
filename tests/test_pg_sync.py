"""PgSyncManager: reconcile append-only tables between two Postgres DBs.

Uses two local databases — anima_test (the 'local' replica) and
anima_test_primary (the 'primary') — to verify watermark + ON CONFLICT replay
is correct and idempotent in both directions.
"""
import os
from pathlib import Path

import pytest

import pgutil  # test_dsn() imported via module to avoid pytest collecting it as a test

_TABLES = ("episodic_memories, emotion_log, static_knowledge, audit_log, "
           "state_snapshots, llm_usage, documents")


def _primary_dsn() -> str:
    base, _db = os.environ["LOCAL_DATABASE_URL"].rsplit("/", 1)
    return f"{base}/anima_test_primary"


def _ensure_primary_db() -> None:
    base, _db = os.environ["LOCAL_DATABASE_URL"].rsplit("/", 1)
    import psycopg
    with psycopg.connect(f"{base}/postgres", autocommit=True, connect_timeout=10) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname='anima_test_primary'").fetchone():
            c.execute("CREATE DATABASE anima_test_primary")


def _apply_schema(dsn: str) -> None:
    from anima import memory
    schema = (Path(memory.__file__).parent / "pg_schema.sql").read_text(encoding="utf-8")
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(schema)


@pytest.fixture
def two_dbs():
    """(local_dsn, primary_dsn) — both schema-applied and truncated clean."""
    if not pgutil.test_dsn() or not pgutil.ensure_test_db():
        pytest.skip("local Postgres unavailable")
    _ensure_primary_db()
    local = pgutil.test_dsn().replace("@localhost:", "@127.0.0.1:")
    primary = _primary_dsn().replace("@localhost:", "@127.0.0.1:")
    for dsn in (local, primary):
        _apply_schema(dsn)
    from anima.memory.pg_sync import PgSyncManager
    for dsn in (local, primary):
        conn = PgSyncManager._open(dsn)
        try:
            conn.execute(f"TRUNCATE {_TABLES}")
        finally:
            conn.close()
    return local, primary


def _insert_episodic(dsn, mid, content, created_at):
    from anima.memory.pg_sync import PgSyncManager
    conn = PgSyncManager._open(dsn)
    try:
        conn.execute(
            "INSERT INTO episodic_memories (id, type, content, importance, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (mid, "chat", content, 0.5, created_at),
        )
    finally:
        conn.close()


def _count(dsn, table="episodic_memories"):
    from anima.memory.pg_sync import PgSyncManager
    conn = PgSyncManager._open(dsn)
    try:
        with conn.cursor() as c:
            c.execute(f"SELECT COUNT(*) AS n FROM {table}")
            return c.fetchone()["n"]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_replay_local_to_primary_is_idempotent(two_dbs):
    from anima.memory.pg_sync import PgSyncManager
    local, primary = two_dbs
    for i in range(3):
        _insert_episodic(local, f"mem_{i}", f"local row {i}", 1000.0 + i)

    lc = PgSyncManager._open(local)
    pc = PgSyncManager._open(primary)
    try:
        spec = {"t": "episodic_memories", "ts": "created_at", "pk": "id"}
        n = PgSyncManager._reconcile_table(lc, pc, spec)
        assert n == 3
        assert _count(primary) == 3
        # Running again copies nothing (watermark + ON CONFLICT).
        assert PgSyncManager._reconcile_table(lc, pc, spec) == 0
        assert _count(primary) == 3
    finally:
        lc.close()
        pc.close()


@pytest.mark.asyncio
async def test_incremental_only_copies_new_rows(two_dbs):
    from anima.memory.pg_sync import PgSyncManager
    local, primary = two_dbs
    _insert_episodic(local, "mem_a", "a", 1000.0)
    spec = {"t": "episodic_memories", "ts": "created_at", "pk": "id"}

    lc = PgSyncManager._open(local)
    pc = PgSyncManager._open(primary)
    try:
        assert PgSyncManager._reconcile_table(lc, pc, spec) == 1
        # A newer local row → only that one copies.
        _insert_episodic(local, "mem_b", "b", 2000.0)
        assert PgSyncManager._reconcile_table(lc, pc, spec) == 1
        assert _count(primary) == 2
    finally:
        lc.close()
        pc.close()


@pytest.mark.asyncio
async def test_backup_primary_to_local_full_reconcile(two_dbs):
    from anima.memory.pg_sync import PgSyncManager
    local, primary = two_dbs
    for i in range(4):
        _insert_episodic(primary, f"mem_p{i}", f"primary row {i}", 500.0 + i)

    pc = PgSyncManager._open(primary)
    lc = PgSyncManager._open(local)
    try:
        total = PgSyncManager._reconcile_table(
            pc, lc, {"t": "episodic_memories", "ts": "created_at", "pk": "id"})
        assert total == 4
        assert _count(local) == 4
    finally:
        pc.close()
        lc.close()


@pytest.mark.asyncio
async def test_static_knowledge_skips_serial_id(two_dbs):
    """static_knowledge keys on (category,key,scope); its SERIAL id must not be
    copied across (each DB owns its own sequence)."""
    from anima.memory.pg_sync import PgSyncManager
    local, primary = two_dbs
    conn = PgSyncManager._open(local)
    try:
        conn.execute(
            "INSERT INTO static_knowledge (category, key, value, updated_at, scope) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("env", "os", "windows", 100.0, "global"),
        )
    finally:
        conn.close()

    lc = PgSyncManager._open(local)
    pc = PgSyncManager._open(primary)
    try:
        spec = {"t": "static_knowledge", "ts": "updated_at",
                "pk": "category,key,scope", "exclude": ("id",)}
        assert PgSyncManager._reconcile_table(lc, pc, spec) == 1
        with pc.cursor() as c:
            c.execute("SELECT key, value FROM static_knowledge WHERE category='env'")
            row = c.fetchone()
        assert row["key"] == "os" and row["value"] == "windows"
    finally:
        lc.close()
        pc.close()
