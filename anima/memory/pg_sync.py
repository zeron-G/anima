"""Postgres replica sync: keep the local failover DB warm, and reconcile
local-only writes back to the primary after an outage.

Two directions, picked by the live connection's state:
  - ONLINE  (serving primary): copy NEW primary rows → local, so the local
    failover always holds a recent backup ("本地最新备份版本").
  - OFFLINE (serving local fallback): once the primary is reachable again,
    replay local-only rows → primary, then switch the live connection back.

Reconciliation is per append-only table: take MAX(timestamp) on the destination
as a watermark, pull source rows at/after it, and INSERT ... ON CONFLICT DO
NOTHING. Append-only + a stable primary key makes this idempotent and safe to
run repeatedly. Node-local operational tables (env_catalog / env_scan_progress)
are intentionally NOT synced — they're a per-machine filesystem cache.
"""
from __future__ import annotations

import asyncio

import psycopg
from psycopg.rows import dict_row

from anima.memory.pg_db import PgDatabaseManager
from anima.utils.logging import get_logger

log = get_logger("pg_sync")


def _adapt(value):
    """JSONB columns load as dict/list — wrap them so they re-bind as jsonb (a
    bare dict/list isn't auto-adapted). pgvector embeddings arrive as ndarray
    (or a text literal), neither a plain list, so they pass through untouched."""
    if isinstance(value, (dict, list)):
        from psycopg.types.json import Jsonb
        return Jsonb(value)
    return value


# (table, timestamp/watermark column, conflict target, columns to skip on copy)
_SPECS = [
    {"t": "episodic_memories", "ts": "created_at", "pk": "id"},
    {"t": "emotion_log", "ts": "timestamp", "pk": "id"},
    {"t": "llm_usage", "ts": "timestamp", "pk": "id"},
    {"t": "audit_log", "ts": "timestamp", "pk": "id"},
    {"t": "state_snapshots", "ts": "timestamp", "pk": "id"},
    {"t": "documents", "ts": "imported_at", "pk": "chunk_id"},
    # static_knowledge keys on (category,key,scope); its `id` is a per-DB SERIAL,
    # so skip it on copy and let the destination assign its own.
    {"t": "static_knowledge", "ts": "updated_at", "pk": "category,key,scope", "exclude": ("id",)},
]


class PgSyncManager:
    """Background reconciliation between the primary (Neon) and local Postgres."""

    def __init__(self, db: PgDatabaseManager, *, interval_s: int = 300) -> None:
        self._db = db
        self._primary = db.primary_dsn
        self._local = db.local_dsn
        self._interval = interval_s
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if not (self._primary and self._local):
            log.info("PgSync disabled — needs both DATABASE_URL and LOCAL_DATABASE_URL")
            return
        if self._primary == self._local:
            log.info("PgSync disabled — primary and local point at the same DB")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="pg_sync")
        log.info("PgSync started (interval=%ds)", self._interval)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._interval)
            if not self._running:
                break
            try:
                if self._db.using_local:
                    # Offline → try to recover: replay local-only writes to the
                    # primary, then switch the live connection back to it.
                    recovered = await asyncio.to_thread(self._replay_local_to_primary)
                    if recovered and await self._db.switch_to_primary():
                        log.info("PgSync: recovered to primary (local-only writes replayed)")
                else:
                    # Online → keep the local failover replica warm.
                    await asyncio.to_thread(self._backup_primary_to_local)
            except Exception as e:  # noqa: BLE001 — never let the loop die
                log.warning("PgSync tick failed: %s", str(e)[:160])

    # ── sync-on-demand (also callable directly, e.g. tests / shutdown) ──

    def _replay_local_to_primary(self) -> bool:
        """Push local-only rows up to the primary. Returns True iff the primary
        was reachable (so the caller may switch the live connection back)."""
        try:
            primary = self._open(self._primary)
        except Exception as e:  # noqa: BLE001 — primary still down; stay local
            log.debug("PgSync: primary still unreachable: %s", str(e)[:120])
            return False
        try:
            local = self._open(self._local)
        except Exception as e:  # noqa: BLE001
            log.warning("PgSync: local unreachable during replay: %s", str(e)[:120])
            primary.close()
            return False
        try:
            n = self._reconcile(local, primary)
            if n:
                log.info("PgSync: replayed %d local-only row(s) to primary", n)
            return True
        finally:
            local.close()
            primary.close()

    def _backup_primary_to_local(self) -> int:
        """Copy new primary rows down to the local replica. Returns rows copied."""
        try:
            primary = self._open(self._primary)
        except Exception as e:  # noqa: BLE001
            log.debug("PgSync: primary unreachable for backup: %s", str(e)[:120])
            return 0
        try:
            local = self._open(self._local)
        except Exception as e:  # noqa: BLE001
            log.debug("PgSync: local replica unreachable: %s", str(e)[:120])
            primary.close()
            return 0
        try:
            n = self._reconcile(primary, local)
            if n:
                log.debug("PgSync: warmed local replica with %d row(s)", n)
            return n
        finally:
            local.close()
            primary.close()

    # ── internals ──

    @staticmethod
    def _open(dsn: str) -> psycopg.Connection:
        conn = psycopg.connect(dsn, connect_timeout=10, autocommit=True, row_factory=dict_row)
        try:
            from pgvector.psycopg import register_vector
            register_vector(conn)
        except Exception:  # noqa: BLE001 — non-vector tables still work
            pass
        return conn

    def _reconcile(self, src: psycopg.Connection, dst: psycopg.Connection) -> int:
        total = 0
        for spec in _SPECS:
            try:
                total += self._reconcile_table(src, dst, spec)
            except Exception as e:  # noqa: BLE001 — one table failing shouldn't stop the rest
                log.warning("PgSync: reconcile %s failed: %s", spec["t"], str(e)[:120])
        return total

    @staticmethod
    def _reconcile_table(src: psycopg.Connection, dst: psycopg.Connection, spec: dict) -> int:
        t, ts, pk = spec["t"], spec["ts"], spec["pk"]
        exclude = set(spec.get("exclude", ()))

        with dst.cursor() as c:
            c.execute(f"SELECT COALESCE(MAX({ts}), 0) AS w FROM {t}")
            watermark = c.fetchone()["w"]

        with src.cursor() as c:
            c.execute(f"SELECT * FROM {t} WHERE {ts} >= %s", (watermark,))
            rows = c.fetchall()
        if not rows:
            return 0

        cols = [k for k in rows[0].keys() if k not in exclude]
        collist = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))
        sql = (f"INSERT INTO {t} ({collist}) VALUES ({placeholders}) "
               f"ON CONFLICT ({pk}) DO NOTHING")
        inserted = 0
        with dst.cursor() as c:
            for r in rows:
                c.execute(sql, tuple(_adapt(r[k]) for k in cols))
                inserted += c.rowcount
        return inserted
