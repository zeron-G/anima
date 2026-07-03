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

    def __init__(self, dsn: str = "", local_dsn: str = "", allow_local_failover: bool = True) -> None:
        # On Windows, "localhost" resolves to IPv6 ::1 first; if Postgres only
        # listens on IPv4 the connect SILENTLY hangs the full connect_timeout
        # (~15s) before falling back to 127.0.0.1. That would stall every
        # offline failover. Pin local hosts to 127.0.0.1 to dodge it.
        self._dsn = self._ipv4_localhost(dsn or get_secret("DATABASE_URL"))
        # allow_local_failover=False is used for the tiered CLOUD tier: its failover
        # DSN would otherwise default to LOCAL_DATABASE_URL — the SAME physical DB as
        # the working tier — so a cloud-down write would silently land in the local
        # tier and strand shared/global data. A cloud-only store must instead raise so
        # callers can degrade explicitly (read local-only, write local fallback).
        self._local_dsn = (
            self._ipv4_localhost(local_dsn or get_secret("LOCAL_DATABASE_URL"))
            if allow_local_failover else "")
        self._conn: psycopg.Connection | None = None
        self._using_local = False
        self._sync_write_lock = threading.Lock()
        self._closed = False

    # ── Lifecycle ──

    @staticmethod
    def _ipv4_localhost(dsn: str) -> str:
        """Rewrite a localhost host to 127.0.0.1 (avoids the Windows ::1 stall)."""
        if not dsn:
            return dsn
        return dsn.replace("@localhost:", "@127.0.0.1:").replace("@localhost/", "@127.0.0.1/")

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

    @property
    def primary_dsn(self) -> str:
        return self._dsn

    @property
    def local_dsn(self) -> str:
        return self._local_dsn

    def status(self) -> dict:
        """A cheap, side-effect-free snapshot for the Sentinel health probe."""
        return {
            "is_open": self.is_open,
            "using_local": self._using_local,
            "primary_configured": bool(self._dsn),
            "local_configured": bool(self._local_dsn),
        }

    def primary_reachable(self, *, connect_timeout: int = 5) -> bool:
        """Read-only probe of the PRIMARY endpoint (no side effects on the live
        connection, no failover). Used to decide whether failback is possible.
        Returns False if no primary is configured or it can't be reached."""
        if not self._dsn:
            return False
        try:
            with psycopg.connect(self._dsn, connect_timeout=connect_timeout,
                                 autocommit=True) as c:
                c.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001 — unreachable is the answer, not an error
            return False

    # ── Runtime failover ──
    # psycopg raises OperationalError/InterfaceError when the connection drops
    # mid-operation (e.g. Neon goes away after we connected). We catch those,
    # reconnect (primary→local, same order as startup), and retry ONCE.
    _CONN_ERRORS = (psycopg.OperationalError, psycopg.InterfaceError)

    def _reconnect_locked(self) -> None:
        """Reopen the connection (primary→local). Caller MUST hold the lock."""
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass
        self._conn = self._connect()  # sets _using_local; tries primary then local
        try:
            from pgvector.psycopg import register_vector
            register_vector(self._conn)
        except Exception as e:  # noqa: BLE001
            log.debug("pgvector register skipped on reconnect: %s", e)

    def _run_locked(self, work):
        """Run work(cur) under the lock; on a dropped connection, reconnect and
        retry once. This is the runtime offline-failover path."""
        self._check_open()
        with self._sync_write_lock:
            try:
                with self._conn.cursor() as cur:
                    return work(cur)
            except self._CONN_ERRORS as e:
                log.warning("Postgres connection lost (%s) — reconnecting + retrying",
                            str(e)[:120])
                self._reconnect_locked()
                with self._conn.cursor() as cur:
                    return work(cur)

    async def switch_to_primary(self) -> bool:
        """Force the live connection back to the primary (used after the sync
        manager has replayed local-only writes on reconnect). Returns True if
        now on the primary; leaves the existing connection untouched on failure."""
        def _switch() -> bool:
            with self._sync_write_lock:
                if not self._dsn or self._closed:
                    return False
                try:
                    conn = psycopg.connect(
                        self._dsn, connect_timeout=15, autocommit=True, row_factory=dict_row,
                    )
                except Exception as e:  # noqa: BLE001 — primary still down
                    log.debug("switch_to_primary: primary still unreachable: %s", str(e)[:120])
                    return False
                try:
                    if self._conn is not None:
                        self._conn.close()
                except Exception:
                    pass
                self._conn = conn
                self._using_local = False
                try:
                    from pgvector.psycopg import register_vector
                    register_vector(self._conn)
                except Exception:
                    pass
                log.info("Postgres switched back to primary")
                return True
        return await asyncio.to_thread(_switch)

    # ── Reads (dict rows; %s placeholders) ──
    # A psycopg Connection is NOT thread-safe, so ALL access (reads + writes)
    # funnels through the one lock; async variants wrap the locked sync calls.

    def fetch_sync(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        def work(cur):
            cur.execute(sql, params)
            return cur.fetchall()
        return self._run_locked(work)

    def fetch_one_sync(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        def work(cur):
            cur.execute(sql, params)
            return cur.fetchone()
        return self._run_locked(work)

    async def fetch(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.fetch_sync, sql, params)

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.fetch_one_sync, sql, params)

    async def fetch_scalar(self, sql: str, params: tuple = ()) -> Any:
        row = await self.fetch_one(sql, params)
        if not row:
            return None
        return next(iter(row.values()))

    # ── Writes (serialised under a thread lock; autocommit) ──

    def write_sync(self, sql: str, params: tuple = ()) -> int:
        """Single write under a thread lock (for *_sync methods in to_thread)."""
        def work(cur):
            cur.execute(sql, params)
            return cur.rowcount
        return self._run_locked(work)

    def write_many_sync(self, sql: str, params_list: list[tuple]) -> int:
        if not params_list:
            return 0
        def work(cur):
            cur.executemany(sql, params_list)
            return len(params_list)
        return self._run_locked(work)

    async def execute(self, sql: str, params: tuple = ()) -> int:
        return await asyncio.to_thread(self.write_sync, sql, params)

    async def execute_many(self, sql: str, params_list: list[tuple]) -> int:
        return await asyncio.to_thread(self.write_many_sync, sql, params_list)

    # ── Internal ──

    def _check_open(self) -> None:
        if self._conn is None or self._closed:
            raise RuntimeError("Postgres is not open. Call await db.init() first.")
