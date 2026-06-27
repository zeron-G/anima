"""Postgres backend (Neon cloud primary + local fallback) with pgvector.

Mirrors DatabaseManager's async-first surface so the memory store can target
Postgres instead of SQLite. Selected when DATABASE_URL is set.

Resilience: on a connection failure to the primary (Neon), falls back to
LOCAL_DATABASE_URL (a local Postgres) so Eva keeps running offline. Episodic
memory is append-only (id + sync_seq), so writes made against the local fallback
during a cloud outage can be reconciled to the primary on reconnect.

psycopg3 (sync core wrapped in asyncio.to_thread, mirroring the SQLite manager's
proven pattern) + autocommit + dict rows. pgvector types are registered so
Python lists bind directly to vector columns.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

import psycopg
from psycopg.rows import dict_row

from anima.secret_store import get_secret
from anima.utils.logging import get_logger

log = get_logger("pg_db")


class PgDatabaseManager:
    """Thread-safe, async-first Postgres manager with Neon→local failover."""

    def __init__(self, dsn: str = "", local_dsn: str = "") -> None:
        self._dsn = dsn or get_secret("DATABASE_URL")
        self._local_dsn = local_dsn or get_secret("LOCAL_DATABASE_URL")
        self._conn: psycopg.Connection | None = None
        self._using_local = False
        self._sync_write_lock = threading.Lock()
        self._closed = False

    # ── Lifecycle ──

    def _connect(self) -> psycopg.Connection:
        """Connect to the primary; fall back to local on failure."""
        last_err: Exception | None = None
        for dsn, is_local in ((self._dsn, False), (self._local_dsn, True)):
            if not dsn:
                continue
            try:
                conn = psycopg.connect(
                    dsn, connect_timeout=15, autocommit=True, row_factory=dict_row,
                )
                self._using_local = is_local
                log.info("Postgres connected (%s)", "LOCAL fallback" if is_local else "primary")
                return conn
            except Exception as e:  # noqa: BLE001 — try the next endpoint
                last_err = e
                log.warning("Postgres connect failed (%s): %s",
                            "local" if is_local else "primary", str(e)[:200])
        raise RuntimeError(f"No Postgres reachable (primary + local): {last_err}")

    async def init(self, schema: str = "") -> None:
        """Connect, register pgvector, and apply *schema* (idempotent DDL)."""
        def _init() -> None:
            self._conn = self._connect()
            try:
                from pgvector.psycopg import register_vector
                register_vector(self._conn)
            except Exception as e:  # noqa: BLE001
                log.debug("pgvector register skipped: %s", e)
            if schema:
                with self._conn.cursor() as cur:
                    cur.execute(schema)
        await asyncio.to_thread(_init)
        log.info("Postgres initialised (%s)", "local fallback" if self._using_local else "primary")

    async def close(self) -> None:
        if self._closed or self._conn is None:
            return
        self._closed = True
        def _close() -> None:
            try:
                self._conn.close()
            except Exception:
                pass
        await asyncio.to_thread(_close)
        self._conn = None
        log.info("Postgres closed")

    @property
    def is_open(self) -> bool:
        return self._conn is not None and not self._closed

    @property
    def using_local(self) -> bool:
        """True when serving from the local fallback (cloud was unreachable)."""
        return self._using_local

    # ── Reads (dict rows; %s placeholders) ──

    async def fetch(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        self._check_open()
        def _fetch() -> list[dict[str, Any]]:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        return await asyncio.to_thread(_fetch)

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        self._check_open()
        def _fetch_one() -> dict[str, Any] | None:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()
        return await asyncio.to_thread(_fetch_one)

    async def fetch_scalar(self, sql: str, params: tuple = ()) -> Any:
        row = await self.fetch_one(sql, params)
        if not row:
            return None
        return next(iter(row.values()))

    # ── Writes (serialised under a thread lock; autocommit) ──

    def write_sync(self, sql: str, params: tuple = ()) -> int:
        """Single write under a thread lock (for *_sync methods in to_thread)."""
        self._check_open()
        with self._sync_write_lock:
            with self._conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.rowcount

    def write_many_sync(self, sql: str, params_list: list[tuple]) -> int:
        self._check_open()
        if not params_list:
            return 0
        with self._sync_write_lock:
            with self._conn.cursor() as cur:
                cur.executemany(sql, params_list)
            return len(params_list)

    async def execute(self, sql: str, params: tuple = ()) -> int:
        return await asyncio.to_thread(self.write_sync, sql, params)

    async def execute_many(self, sql: str, params_list: list[tuple]) -> int:
        return await asyncio.to_thread(self.write_many_sync, sql, params_list)

    # ── Internal ──

    def _check_open(self) -> None:
        if self._conn is None or self._closed:
            raise RuntimeError("Postgres is not open. Call await db.init() first.")
