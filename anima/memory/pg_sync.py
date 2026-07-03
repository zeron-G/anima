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
import json

import psycopg
from psycopg.rows import dict_row

from anima.memory.pg_db import PgDatabaseManager
from anima.utils.logging import get_logger

log = get_logger("pg_sync")

# Re-examine this many seconds below the destination watermark on each reconcile.
# A pure `ts >= MAX(ts)` window can permanently skip a row that exists on src but
# not dst when the two nodes' clocks differ (the row's ts lands below the dst max
# but it was never copied) — a silent lost write on failback (CODE_REVIEW P0-9).
# ON CONFLICT DO NOTHING dedups the re-examined rows, so the only cost is rescanning
# a bounded recent window; the margin is generous enough to cover real clock skew.
_RECONCILE_SAFETY_MARGIN_S = 86400  # 24h


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
    # emotion_log is intentionally NOT synced: emotion is a PER-LOCUS signal
    # (DISTRIBUTED_DESIGN v0.3 §emotion). Each node stamps its own node_id and
    # restores only its own mood (pg_store.get_latest_emotion), so a node's feelings
    # must not propagate into a shared/failover store and bleed into another locus.
    # Tradeoff: on a Neon-outage failover a single node's mood resets to baseline
    # (best-effort restore, re-accumulates quickly) — accepted.
    {"t": "llm_usage", "ts": "timestamp", "pk": "id"},
    {"t": "audit_log", "ts": "timestamp", "pk": "id"},
    {"t": "state_snapshots", "ts": "timestamp", "pk": "id"},
    {"t": "documents", "ts": "imported_at", "pk": "chunk_id"},
    # static_knowledge is the ONE mutable synced table → version-aware merge
    # (not append-only DO NOTHING). Its `id` is a per-DB SERIAL, never copied.
    {"t": "static_knowledge", "pk": "category,key,scope", "merge": "version"},
]


def _journal_lww(src_row: dict, dst_row: dict, *, winner: str) -> None:
    """Record a static_knowledge merge conflict where one side's value is
    dropped — append to guardian_actions.jsonl so the loser is recoverable by
    hand (never silently overwritten)."""
    keep, drop = (src_row, dst_row) if winner == "src" else (dst_row, src_row)
    rec = {
        "phase": "merge_lww", "component": "db",
        "ts": _now(), "key": f"{src_row.get('category')}/{src_row.get('key')}@{src_row.get('scope')}",
        "winner": winner,
        "kept_value": keep.get("value"), "kept_version": keep.get("version"),
        "dropped_value": drop.get("value"), "dropped_version": drop.get("version"),
    }
    try:
        from anima.config import data_dir
        d = data_dir() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "guardian_actions.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 — best effort
        log.debug("journal_lww: %s", e)
    log.warning("PgSync: static_knowledge LWW — kept v%s, dropped v%s for %s",
                keep.get("version"), drop.get("version"), rec["key"])


def _now() -> float:
    import time as _t
    return _t.time()


class PgSyncManager:
    """Background reconciliation between the primary (Neon) and local Postgres."""

    def __init__(self, db: PgDatabaseManager, *, interval_s: int = 300) -> None:
        self._db = db
        self._primary = db.primary_dsn
        self._local = db.local_dsn
        self._interval = interval_s
        self._task: asyncio.Task | None = None
        self._running = False
        # Serialises failback: the 300s loop and an on-demand recover_now() must
        # never replay/switch concurrently.
        self._failback_lock = asyncio.Lock()

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
                    # Offline → try to recover (replay-then-switch, under the lock).
                    async with self._failback_lock:
                        recovered = await asyncio.to_thread(self._replay_local_to_primary)
                        if recovered and await self._db.switch_to_primary():
                            log.info("PgSync: recovered to primary (local-only writes replayed)")
                else:
                    # Online → keep the local failover replica warm.
                    await asyncio.to_thread(self._backup_primary_to_local)
            except Exception as e:  # noqa: BLE001 — never let the loop die
                log.warning("PgSync tick failed: %s", str(e)[:160])

    async def recover_now(self) -> dict:
        """On-demand accelerated failback (the Guardian's DbRecoverFixer calls
        this instead of waiting up to interval_s). Same replay-then-switch under
        the same lock as the loop — never a second, unsynchronised failback path.
        Returns {recovered: bool, reason/detail}."""
        if not self._db.using_local:
            return {"recovered": False, "reason": "already_on_primary"}
        if self._failback_lock.locked():
            return {"recovered": False, "reason": "in_progress"}
        async with self._failback_lock:
            if not self._db.using_local:                      # re-check under lock
                return {"recovered": False, "reason": "already_on_primary"}
            recovered = await asyncio.to_thread(self._replay_local_to_primary)
            if not recovered:
                return {"recovered": False, "reason": "primary_unreachable"}
            switched = await self._db.switch_to_primary()
            if switched:
                log.info("PgSync: accelerated failback to primary (recover_now)")
                return {"recovered": True, "detail": "replayed + switched to primary"}
            return {"recovered": False, "reason": "switch_failed"}

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
                if spec.get("merge") == "version":
                    total += self._reconcile_versioned(src, dst, spec)
                else:
                    total += self._reconcile_table(src, dst, spec)
            except Exception as e:  # noqa: BLE001 — one table failing shouldn't stop the rest
                log.warning("PgSync: reconcile %s failed: %s", spec["t"], str(e)[:120])
        return total

    @staticmethod
    def _reconcile_versioned(src: psycopg.Connection, dst: psycopg.Connection, spec: dict) -> int:
        """Version-aware merge for the one mutable table (static_knowledge).
        Higher `version` wins (wall-clock only as a tiebreak); tombstones
        (is_deleted) propagate by the same rule; a dropped value is journaled."""
        t = spec["t"]
        cols = ("category", "key", "value", "source", "importance",
                "updated_at", "scope", "node_id", "version", "is_deleted")
        collist = ", ".join(cols)
        with src.cursor() as c:
            c.execute(f"SELECT {collist} FROM {t}")
            src_rows = c.fetchall()
        if not src_rows:
            return 0
        merged = 0
        with dst.cursor() as c:
            for r in src_rows:
                c.execute(f"SELECT value, version, updated_at, is_deleted FROM {t} "
                          "WHERE category=%s AND key=%s AND scope=%s",
                          (r["category"], r["key"], r["scope"]))
                d = c.fetchone()
                if d is None:                                  # new key on dst → copy
                    c.execute(f"INSERT INTO {t} ({collist}) VALUES "
                              f"({', '.join(['%s'] * len(cols))})",
                              tuple(r[k] for k in cols))
                    merged += 1
                    continue
                src_wins = (r["version"] > d["version"]) or (
                    r["version"] == d["version"]
                    and (r["updated_at"] or 0) > (d["updated_at"] or 0))
                if src_wins:
                    if d["value"] != r["value"] or d["is_deleted"] != r["is_deleted"]:
                        _journal_lww(r, d, winner="src")       # dst's value overwritten
                    c.execute(
                        f"UPDATE {t} SET value=%s, source=%s, importance=%s, updated_at=%s, "
                        "node_id=%s, version=%s, is_deleted=%s "
                        "WHERE category=%s AND key=%s AND scope=%s",
                        (r["value"], r["source"], r["importance"], r["updated_at"],
                         r["node_id"], r["version"], r["is_deleted"],
                         r["category"], r["key"], r["scope"]))
                    merged += 1
                elif d["value"] != r["value"]:
                    _journal_lww(r, d, winner="dst")           # src's value dropped (its loss is logged)
        return merged

    @staticmethod
    def _reconcile_table(src: psycopg.Connection, dst: psycopg.Connection, spec: dict) -> int:
        t, ts, pk = spec["t"], spec["ts"], spec["pk"]
        exclude = set(spec.get("exclude", ()))

        with dst.cursor() as c:
            c.execute(f"SELECT COALESCE(MAX({ts}), 0) AS w FROM {t}")
            watermark = c.fetchone()["w"]
        # Drop below the watermark by a safety margin so clock skew can't silently
        # skip a src-only row (CODE_REVIEW P0-9). ON CONFLICT DO NOTHING dedups.
        if watermark:
            watermark = max(0, watermark - _RECONCILE_SAFETY_MARGIN_S)

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
