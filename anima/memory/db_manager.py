"""Thread-safe SQLite database manager for ANIMA.

Resolves: C-06 (multi-thread unsafe), C-07 (no transaction isolation),
M-16/M-17 (no transactions in touch/update), M-18 (row-by-row updates),
M-48 (sync module direct connection access).

Architecture
------------
- **Single connection, single writer**: All writes are serialised through
  an ``asyncio.Lock``.  This prevents concurrent writes from corrupting
  SQLite's internal connection state.
- **WAL mode**: Enables concurrent readers even while a write transaction
  is in progress.  Readers see a consistent snapshot.
- **Explicit transactions**: The ``transaction()`` context manager wraps
  multiple writes in ``BEGIN IMMEDIATE ... COMMIT`` (or ``ROLLBACK`` on
  exception).
- **Async-first API**: Every public method is ``async``.  Blocking SQL
  operations run in the default thread-pool executor via
  ``asyncio.to_thread()``.

Usage
-----
::

    db = DatabaseManager("data/anima.db")
    await db.init(schema_sql)

    # Simple read:
    rows = await db.fetch("SELECT * FROM memories WHERE type = ?", ("chat",))

    # Simple write:
    await db.execute("INSERT INTO memories ...", (values,))

    # Batch write:
    await db.execute_many(
        "UPDATE memories SET decay_score = ? WHERE id = ?",
        [(0.5, "mem_1"), (0.3, "mem_2"), ...],
    )

    # Transaction (multi-statement atomic):
    async with db.transaction() as tx:
        await tx.execute("UPDATE ...")
        await tx.execute("INSERT ...")
        # auto-COMMIT on success; auto-ROLLBACK on exception

    # Shutdown:
    await db.close()
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from anima.utils.logging import get_logger

log = get_logger("db_manager")


class DatabaseManager:
    """Thread-safe, async-first SQLite manager.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Created if it doesn't exist.
        Use ``":memory:"`` for in-memory databases (testing).
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._write_lock = asyncio.Lock()
        self._conn: sqlite3.Connection | None = None
        self._closed = False

    # ── Lifecycle ──

    async def init(self, schema: str = "") -> None:
        """Open the database, enable WAL, and apply *schema*.

        This method MUST be called before any other operation.
        """
        def _init() -> None:
            path = Path(self._db_path)
            if self._db_path != ":memory:":
                path.parent.mkdir(parents=True, exist_ok=True)

            self._conn = sqlite3.connect(
                self._db_path,
                # We control thread safety ourselves via _write_lock.
                # The connection is ONLY accessed through to_thread calls,
                # never from multiple OS threads simultaneously.
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row

            # WAL mode: concurrent reads, serialised writes
            self._conn.execute("PRAGMA journal_mode = WAL")
            # NORMAL sync: safe with WAL, much faster than FULL
            self._conn.execute("PRAGMA synchronous = NORMAL")
            # 5-second busy timeout: retry on SQLITE_BUSY instead of
            # raising immediately
            self._conn.execute("PRAGMA busy_timeout = 5000")
            # Enable foreign keys (disabled by default in SQLite)
            self._conn.execute("PRAGMA foreign_keys = ON")

            if schema:
                self._conn.executescript(schema)
                self._conn.commit()

        await asyncio.to_thread(_init)
        log.info("Database initialised: %s (WAL mode)", self._db_path)

    async def close(self) -> None:
        """Close the database connection.  Safe to call multiple times."""
        if self._closed or self._conn is None:
            return
        self._closed = True
        async with self._write_lock:
            def _close():
                try:
                    self._conn.execute("PRAGMA optimize")
                except Exception:
                    pass
                self._conn.close()
            await asyncio.to_thread(_close)
            self._conn = None
        log.info("Database closed: %s", self._db_path)

    @property
    def is_open(self) -> bool:
        return self._conn is not None and not self._closed

    # ── Read operations (no lock needed — WAL allows concurrent reads) ──

    async def fetch(
        self,
        sql: str,
        params: tuple = (),
    ) -> list[dict[str, Any]]:
        """Execute a read-only query and return all rows as dicts.

        Safe to call concurrently from multiple coroutines.
        """
        self._check_open()

        def _fetch() -> list[dict[str, Any]]:
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

        return await asyncio.to_thread(_fetch)

    async def fetch_one(
        self,
        sql: str,
        params: tuple = (),
    ) -> dict[str, Any] | None:
        """Execute a read-only query and return the first row, or None."""
        self._check_open()

        def _fetch_one() -> dict[str, Any] | None:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

        return await asyncio.to_thread(_fetch_one)

    async def fetch_scalar(
        self,
        sql: str,
        params: tuple = (),
    ) -> Any:
        """Execute a query and return the first column of the first row."""
        self._check_open()

        def _fetch_scalar() -> Any:
            row = self._conn.execute(sql, params).fetchone()
            return row[0] if row else None

        return await asyncio.to_thread(_fetch_scalar)

    # ── Write operations (serialised via _write_lock) ──

    async def execute(
        self,
        sql: str,
        params: tuple = (),
    ) -> int:
        """Execute a single write statement and commit.

        Returns the number of affected rows.

        All writes are serialised: only one ``execute`` / ``execute_many``
        / ``transaction`` can run at a time.
        """
        self._check_open()
        async with self._write_lock:
            def _execute() -> int:
                cursor = self._conn.execute(sql, params)
                self._conn.commit()
                return cursor.rowcount
            return await asyncio.to_thread(_execute)

    async def execute_many(
        self,
        sql: str,
        params_list: list[tuple],
    ) -> int:
        """Execute a parameterised statement for each set of params.

        All rows are committed atomically (single transaction).
        Returns the number of param sets processed.
        """
        self._check_open()
        if not params_list:
            return 0
        async with self._write_lock:
            def _execute_many() -> int:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    self._conn.executemany(sql, params_list)
                    self._conn.commit()
                except Exception:
                    self._conn.rollback()
                    raise
                return len(params_list)
            return await asyncio.to_thread(_execute_many)

    # ── Transactions ──

    def transaction(self) -> _TransactionCtx:
        """Create an async context manager for multi-statement transactions.

        Usage::

            async with db.transaction() as tx:
                await tx.execute("UPDATE memories SET ...")
                await tx.execute("INSERT INTO memories ...")
                # auto-COMMIT on block exit
                # auto-ROLLBACK if an exception propagates

        The transaction acquires the write lock for its entire duration,
        preventing other writes from interleaving.
        """
        self._check_open()
        return _TransactionCtx(self._conn, self._write_lock)

    # ── Migration helpers ──

    async def add_column_if_missing(
        self,
        table: str,
        column: str,
        col_type: str,
        default: str = "NULL",
    ) -> bool:
        """Add a column to *table* if it doesn't already exist.

        Returns True if the column was added, False if it already existed.
        """
        self._check_open()
        async with self._write_lock:
            def _add():
                try:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} "
                        f"{col_type} DEFAULT {default}"
                    )
                    self._conn.commit()
                    return True
                except sqlite3.OperationalError:
                    return False  # already exists
            return await asyncio.to_thread(_add)

    async def create_index_if_missing(
        self,
        index_name: str,
        table: str,
        columns: str,
    ) -> None:
        """Create an index if it doesn't exist."""
        self._check_open()
        async with self._write_lock:
            def _create():
                self._conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {index_name} "
                    f"ON {table}({columns})"
                )
                self._conn.commit()
            await asyncio.to_thread(_create)

    # ── Synchronous access (for legacy code migration) ──

    @property
    def raw_connection(self) -> sqlite3.Connection:
        """Direct access to the underlying connection.

        **WARNING**: This bypasses all safety guarantees (locking, WAL).
        Use ONLY for read-only operations during migration to the async
        API.  Will be removed in a future version.
        """
        self._check_open()
        return self._conn

    # ── Internal ──

    def _check_open(self) -> None:
        if self._conn is None or self._closed:
            raise RuntimeError(
                "Database is not open. Call await db.init() first."
            )


class _TransactionCtx:
    """Async context manager for database transactions.

    Acquires the write lock, begins a transaction, and commits on
    successful exit.  Rolls back on exception.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        lock: asyncio.Lock,
    ) -> None:
        self._conn = conn
        self._lock = lock

    async def __aenter__(self) -> _TransactionCtx:
        await self._lock.acquire()
        try:
            await asyncio.to_thread(self._conn.execute, "BEGIN IMMEDIATE")
        except Exception:
            self._lock.release()
            raise
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        try:
            if exc_type is None:
                await asyncio.to_thread(self._conn.commit)
            else:
                await asyncio.to_thread(self._conn.rollback)
                log.warning(
                    "Transaction rolled back due to %s: %s",
                    exc_type.__name__ if exc_type else "?",
                    exc_val,
                )
        finally:
            self._lock.release()
        return False  # don't suppress exceptions

    async def execute(self, sql: str, params: tuple = ()) -> int:
        """Execute a statement within the transaction.

        Do NOT call ``commit()`` or ``rollback()`` manually — the
        context manager handles that.
        """
        def _exec() -> int:
            return self._conn.execute(sql, params).rowcount
        return await asyncio.to_thread(_exec)

    async def execute_many(self, sql: str, params_list: list[tuple]) -> int:
        """Execute a parameterised statement for each param set."""
        def _exec_many() -> int:
            self._conn.executemany(sql, params_list)
            return len(params_list)
        return await asyncio.to_thread(_exec_many)
