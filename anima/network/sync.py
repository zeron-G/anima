"""Incremental memory sync between ANIMA nodes.

Uses plain (non-async) ZMQ REQ/REP in threads to avoid Windows asyncio
event loop compatibility issues with pyzmq.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

import msgpack
import zmq

from anima.utils.logging import get_logger

log = get_logger("network.sync")


class MemorySync:
    """Incremental memory synchronization between nodes."""

    SYNC_INTERVAL = 60.0
    MAX_BATCH = 500

    def __init__(self, memory_store: Any, local_node_id: str, listen_port: int = 9422) -> None:
        self._store = memory_store
        self._local_node_id = local_node_id
        self._listen_port = listen_port
        self._local_seq = 0
        self._peer_watermarks: dict[str, int] = {}
        self._running = False
        self._thread: threading.Thread | None = None
        self._load_watermarks()

    async def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._server_loop, daemon=True)
        self._thread.start()
        log.info("Memory sync started on port %d", self._listen_port)

    async def stop(self) -> None:
        self._running = False
        self._save_watermarks()
        log.info("Memory sync stopped")

    def next_seq(self) -> int:
        self._local_seq += 1
        return self._local_seq

    @staticmethod
    def content_hash(content: str, type_: str) -> str:
        return hashlib.sha256(f"{content}:{type_}".encode()).hexdigest()[:16]

    async def sync_with_peer(self, peer_address: str, peer_node_id: str) -> dict:
        """Pull new records from a peer (runs sync zmq in thread)."""
        import asyncio
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_with_peer_blocking, peer_address, peer_node_id)

    def _sync_with_peer_blocking(self, peer_address: str, peer_node_id: str) -> dict:
        watermark = self._peer_watermarks.get(peer_node_id, 0)

        parts = peer_address.split(":")
        if len(parts) == 2:
            peer_ip, peer_port = parts[0], int(parts[1])
        else:
            peer_ip, peer_port = peer_address, self._listen_port

        ctx = zmq.Context()
        req = ctx.socket(zmq.REQ)
        req.setsockopt(zmq.RCVTIMEO, 10000)
        req.setsockopt(zmq.SNDTIMEO, 5000)
        req.setsockopt(zmq.LINGER, 0)

        try:
            req.connect(f"tcp://{peer_ip}:{peer_port}")
            req.send(msgpack.packb({
                "type": "sync_request",
                "from_node": self._local_node_id,
                "watermark": watermark,
                "max_batch": self.MAX_BATCH,
            }, use_bin_type=True))

            response = msgpack.unpackb(req.recv(), raw=False)
            records = response.get("records", [])
            peer_seq = response.get("current_seq", 0)

            pulled = sum(1 for r in records if self._write_if_new(r))

            if peer_seq > watermark:
                self._peer_watermarks[peer_node_id] = peer_seq
                self._save_watermarks()

            log.info("Synced with %s: pulled %d records", peer_node_id, pulled)
            return {"pulled": pulled, "pushed": 0, "errors": 0}
        except Exception as e:
            log.debug("Sync with %s failed: %s", peer_node_id, e)
            return {"pulled": 0, "pushed": 0, "errors": 1}
        finally:
            req.close()
            ctx.term()

    def _server_loop(self) -> None:
        """REP server thread — answers sync requests."""
        ctx = zmq.Context()
        rep = ctx.socket(zmq.REP)
        rep.setsockopt(zmq.RCVTIMEO, 1000)
        rep.setsockopt(zmq.LINGER, 0)
        rep.bind(f"tcp://*:{self._listen_port}")

        while self._running:
            try:
                data = rep.recv()
            except zmq.Again:
                continue
            except Exception:
                break

            try:
                request = msgpack.unpackb(data, raw=False)
                if request.get("type") == "sync_request":
                    watermark = request.get("watermark", 0)
                    limit = min(request.get("max_batch", 500), self.MAX_BATCH)
                    records = self._get_records_since(watermark, limit)
                    rep.send(msgpack.packb({
                        "records": records,
                        "current_seq": self._local_seq,
                        "node_id": self._local_node_id,
                    }, use_bin_type=True))
                else:
                    rep.send(msgpack.packb({"error": "unknown"}, use_bin_type=True))
            except Exception as e:
                try:
                    rep.send(msgpack.packb({"error": str(e)}, use_bin_type=True))
                except Exception as e:
                    log.warning("sync: %s", e)

        rep.close()
        ctx.term()

    def _get_records_since(self, watermark: int, limit: int) -> list[dict]:
        conn = self._store._conn
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT id, type, content, importance, created_at, metadata_json, sync_seq "
                "FROM episodic_memories WHERE sync_seq > ? ORDER BY sync_seq ASC LIMIT ?",
                (watermark, limit),
            ).fetchall()
            return [
                {"id": r[0], "type": r[1], "content": r[2], "importance": r[3],
                 "created_at": r[4], "metadata_json": r[5], "sync_seq": r[6],
                 "source_node": self._local_node_id}
                for r in rows
            ]
        except Exception as e:
            log.error("Failed to get records: %s", e)
            return []

    def _write_if_new(self, record: dict) -> bool:
        conn = self._store._conn
        if conn is None:
            return False
        try:
            if conn.execute("SELECT 1 FROM episodic_memories WHERE id=?", (record["id"],)).fetchone():
                return False
            chash = self.content_hash(record.get("content", ""), record.get("type", ""))
            if conn.execute("SELECT 1 FROM episodic_memories WHERE content_hash=? AND created_at>?",
                           (chash, time.time() - 60)).fetchone():
                return False
            seq = self.next_seq()
            conn.execute(
                "INSERT OR IGNORE INTO episodic_memories "
                "(id, type, content, importance, access_count, created_at, last_accessed, "
                "metadata_json, tags_json, sync_seq, content_hash) "
                "VALUES (?, ?, ?, ?, 0, ?, ?, ?, '[]', ?, ?)",
                (record["id"], record.get("type",""), record.get("content",""),
                 record.get("importance",0.5), record.get("created_at",time.time()),
                 time.time(), record.get("metadata_json","{}"), seq, chash),
            )
            conn.commit()
            return True
        except Exception as e:
            log.debug("Write synced record failed: %s", e)
            return False

    def _ensure_sync_columns(self) -> None:
        conn = self._store._conn
        if conn is None:
            return
        for sql in [
            "ALTER TABLE episodic_memories ADD COLUMN sync_seq INTEGER DEFAULT 0",
            "ALTER TABLE episodic_memories ADD COLUMN content_hash TEXT DEFAULT ''",
            "CREATE INDEX IF NOT EXISTS idx_sync_seq ON episodic_memories(sync_seq)",
            "CREATE INDEX IF NOT EXISTS idx_content_hash ON episodic_memories(content_hash)",
        ]:
            try:
                conn.execute(sql)
            except Exception as e:
                log.warning("_ensure_sync_columns: %s", e)
        conn.commit()

    def _load_watermarks(self) -> None:
        from anima.config import data_dir
        path = data_dir() / "sync_watermarks.json"
        if path.exists():
            try:
                self._peer_watermarks = json.loads(path.read_text(encoding="utf-8"))
                if self._peer_watermarks:
                    self._local_seq = max(self._peer_watermarks.values(), default=0)
            except Exception as e:
                log.warning("_load_watermarks: %s", e)

    def _save_watermarks(self) -> None:
        from anima.config import data_dir
        path = data_dir() / "sync_watermarks.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._peer_watermarks), encoding="utf-8")
