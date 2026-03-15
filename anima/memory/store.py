"""SQLite backend + optional ChromaDB for memory storage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from anima.config import project_root
from anima.utils.logging import get_logger
from anima.utils.ids import gen_id

log = get_logger("memory_store")

# Try to import ChromaDB
try:
    import chromadb
    HAS_CHROMADB = True
except ImportError:
    HAS_CHROMADB = False


_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodic_memories (
    id TEXT PRIMARY KEY,
    type TEXT,
    content TEXT,
    importance REAL,
    access_count INTEGER DEFAULT 0,
    created_at REAL,
    last_accessed REAL,
    metadata_json TEXT DEFAULT '{}',
    tags_json TEXT DEFAULT '[]',
    sync_seq INTEGER DEFAULT 0,
    content_hash TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS emotion_log (
    id TEXT PRIMARY KEY,
    engagement REAL,
    confidence REAL,
    curiosity REAL,
    concern REAL,
    trigger TEXT,
    timestamp REAL
);

CREATE TABLE IF NOT EXISTS state_snapshots (
    id TEXT PRIMARY KEY,
    state_json TEXT,
    timestamp REAL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    action TEXT,
    details TEXT,
    timestamp REAL
);

CREATE INDEX IF NOT EXISTS idx_episodic_type ON episodic_memories(type);
CREATE INDEX IF NOT EXISTS idx_episodic_importance ON episodic_memories(importance);
CREATE INDEX IF NOT EXISTS idx_episodic_created ON episodic_memories(created_at);
CREATE INDEX IF NOT EXISTS idx_emotion_ts ON emotion_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_snapshot_ts ON state_snapshots(timestamp);
-- idx_sync_seq and idx_content_hash created after ALTER TABLE migration

CREATE TABLE IF NOT EXISTS llm_usage (
    id TEXT PRIMARY KEY,
    timestamp REAL,
    model TEXT,
    provider TEXT,
    auth_mode TEXT,
    tier TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    estimated_cost_usd REAL,
    event_type TEXT,
    success INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_llm_usage_model ON llm_usage(model);
"""


class MemoryStore:
    """SQLite-backed memory store with optional ChromaDB vector search."""

    def __init__(self, db_path: str) -> None:
        resolved = Path(db_path)
        if not resolved.is_absolute():
            resolved = project_root() / db_path
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(resolved)
        self._conn: sqlite3.Connection | None = None
        self._chroma_collection = None

    @classmethod
    async def create(cls, db_path: str) -> MemoryStore:
        """Factory method — creates and initializes the store."""
        store = cls(db_path)
        store._conn = sqlite3.connect(store._db_path, check_same_thread=False)
        store._conn.row_factory = sqlite3.Row
        store._conn.executescript(_SCHEMA)
        # Migrate: add columns that may not exist in older DBs
        for col, default in [("sync_seq", "0"), ("content_hash", "''")]:
            try:
                store._conn.execute(f"ALTER TABLE episodic_memories ADD COLUMN {col} {'INTEGER' if default == '0' else 'TEXT'} DEFAULT {default}")
            except sqlite3.OperationalError:
                pass  # Column already exists
        # Create indexes for sync columns
        try:
            store._conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_seq ON episodic_memories(sync_seq)")
            store._conn.execute("CREATE INDEX IF NOT EXISTS idx_content_hash ON episodic_memories(content_hash)")
        except sqlite3.OperationalError:
            pass
        store._conn.commit()
        log.info("Memory store initialized: %s", store._db_path)

        # Optional ChromaDB
        if HAS_CHROMADB:
            try:
                chroma_path = Path(store._db_path).parent / "chroma"
                chroma_path.mkdir(exist_ok=True)
                client = chromadb.PersistentClient(path=str(chroma_path))
                store._chroma_collection = client.get_or_create_collection("episodic")
                log.info("ChromaDB initialized for vector search")
            except Exception as e:
                log.warning("ChromaDB init failed, falling back to SQLite: %s", e)

        return store

    # ---- Episodic Memory ----

    # Lamport clock for sync_seq
    _sync_seq_counter: int = 0

    def _next_sync_seq(self) -> int:
        """Increment and return the next sync_seq value."""
        MemoryStore._sync_seq_counter += 1
        return MemoryStore._sync_seq_counter

    @staticmethod
    def _content_hash(content: str, type_: str) -> str:
        """Compute dedup hash: sha256(content + type)[:16]."""
        raw = f"{content}:{type_}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def save_memory(
        self,
        content: str,
        type: str,
        importance: float = 0.5,
        metadata: dict | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Save an episodic memory. Returns the ID."""
        mid = gen_id("mem")
        now = time.time()
        seq = self._next_sync_seq()
        chash = self._content_hash(content, type)
        self._conn.execute(
            "INSERT INTO episodic_memories "
            "(id, type, content, importance, access_count, created_at, last_accessed, "
            "metadata_json, tags_json, sync_seq, content_hash) "
            "VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)",
            (mid, type, content, importance, now, now,
             json.dumps(metadata or {}), json.dumps(tags or []),
             seq, chash),
        )
        self._conn.commit()

        # Optional: add to ChromaDB
        if self._chroma_collection is not None:
            try:
                self._chroma_collection.add(
                    ids=[mid], documents=[content],
                    metadatas=[{"type": type, "importance": importance}],
                )
            except Exception as e:
                log.debug("ChromaDB add failed: %s", e)

        return mid

    def search_memories(
        self,
        query: str | None = None,
        type: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Search episodic memories. Uses ChromaDB if available, else SQLite LIKE."""
        # Try vector search first
        if query and self._chroma_collection is not None:
            try:
                results = self._chroma_collection.query(
                    query_texts=[query], n_results=limit,
                )
                if results["ids"] and results["ids"][0]:
                    ids = results["ids"][0]
                    placeholders = ",".join("?" * len(ids))
                    rows = self._conn.execute(
                        f"SELECT * FROM episodic_memories WHERE id IN ({placeholders})",
                        ids,
                    ).fetchall()
                    return [dict(r) for r in rows]
            except Exception:
                pass

        # Fallback: SQLite
        conditions = []
        params: list[Any] = []
        if type:
            conditions.append("type = ?")
            params.append(type)
        if query:
            conditions.append("content LIKE ?")
            params.append(f"%{query}%")

        where = " AND ".join(conditions) if conditions else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM episodic_memories WHERE {where} ORDER BY importance DESC, created_at DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_memories(self, limit: int = 10, type: str | None = None) -> list[dict]:
        """Get most recent memories."""
        if type:
            rows = self._conn.execute(
                "SELECT * FROM episodic_memories WHERE type = ? ORDER BY created_at DESC LIMIT ?",
                (type, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM episodic_memories ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- Emotion Log ----

    def log_emotion(
        self,
        engagement: float,
        confidence: float,
        curiosity: float,
        concern: float,
        trigger: str = "",
    ) -> None:
        self._conn.execute(
            "INSERT INTO emotion_log (id, engagement, confidence, curiosity, concern, trigger, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (gen_id("emo"), engagement, confidence, curiosity, concern, trigger, time.time()),
        )
        self._conn.commit()

    # ---- State Snapshots ----

    def save_snapshot(self, state: dict) -> None:
        self._conn.execute(
            "INSERT INTO state_snapshots (id, state_json, timestamp) VALUES (?, ?, ?)",
            (gen_id("snap"), json.dumps(state), time.time()),
        )
        self._conn.commit()

    # ---- Audit Log ----

    def audit(self, action: str, details: str = "") -> None:
        self._conn.execute(
            "INSERT INTO audit_log (id, action, details, timestamp) VALUES (?, ?, ?, ?)",
            (gen_id("audit"), action, details, time.time()),
        )
        self._conn.commit()

    # ---- LLM Usage ----

    _COST_PER_1M = {
        "haiku": (0.25, 1.25),
        "sonnet": (3.0, 15.0),
        "opus": (15.0, 75.0),
    }

    @staticmethod
    def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Estimate USD cost based on model name."""
        model_lower = model.lower()
        for key, (inp, out) in MemoryStore._COST_PER_1M.items():
            if key in model_lower:
                return (prompt_tokens * inp + completion_tokens * out) / 1_000_000
        # Default to sonnet pricing
        return (prompt_tokens * 3.0 + completion_tokens * 15.0) / 1_000_000

    def log_llm_usage(
        self,
        model: str,
        provider: str,
        auth_mode: str,
        tier: str,
        prompt_tokens: int,
        completion_tokens: int,
        event_type: str = "",
        success: bool = True,
    ) -> None:
        """Record an LLM API call."""
        total = prompt_tokens + completion_tokens
        cost = self._estimate_cost(model, prompt_tokens, completion_tokens)
        self._conn.execute(
            "INSERT INTO llm_usage (id, timestamp, model, provider, auth_mode, tier, prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd, event_type, success) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (gen_id("llm"), time.time(), model, provider, auth_mode, tier,
             prompt_tokens, completion_tokens, total, cost, event_type, int(success)),
        )
        self._conn.commit()

    def get_usage_history(self, limit: int = 100) -> list[dict]:
        """Return recent LLM usage records."""
        rows = self._conn.execute(
            "SELECT * FROM llm_usage ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_usage_summary(self) -> dict:
        """Return usage totals grouped by model, provider, and day."""
        # By model
        rows = self._conn.execute(
            "SELECT model, COUNT(*) as calls, SUM(prompt_tokens) as prompt, SUM(completion_tokens) as completion, SUM(total_tokens) as total, SUM(estimated_cost_usd) as cost FROM llm_usage GROUP BY model"
        ).fetchall()
        by_model = {r["model"]: {"calls": r["calls"], "prompt_tokens": r["prompt"] or 0, "completion_tokens": r["completion"] or 0, "total_tokens": r["total"] or 0, "cost_usd": round(r["cost"] or 0, 6)} for r in rows}

        # By provider
        rows = self._conn.execute(
            "SELECT provider, COUNT(*) as calls, SUM(total_tokens) as total, SUM(estimated_cost_usd) as cost FROM llm_usage GROUP BY provider"
        ).fetchall()
        by_provider = {r["provider"]: {"calls": r["calls"], "total_tokens": r["total"] or 0, "cost_usd": round(r["cost"] or 0, 6)} for r in rows}

        # By day
        rows = self._conn.execute(
            "SELECT date(timestamp, 'unixepoch') as day, COUNT(*) as calls, SUM(total_tokens) as total, SUM(estimated_cost_usd) as cost FROM llm_usage GROUP BY day ORDER BY day DESC LIMIT 30"
        ).fetchall()
        by_day = {r["day"]: {"calls": r["calls"], "total_tokens": r["total"] or 0, "cost_usd": round(r["cost"] or 0, 6)} for r in rows}

        # Grand totals
        row = self._conn.execute(
            "SELECT COUNT(*) as calls, SUM(prompt_tokens) as prompt, SUM(completion_tokens) as completion, SUM(total_tokens) as total, SUM(estimated_cost_usd) as cost FROM llm_usage"
        ).fetchone()

        return {
            "total_calls": row["calls"] or 0,
            "total_prompt_tokens": row["prompt"] or 0,
            "total_completion_tokens": row["completion"] or 0,
            "total_tokens": row["total"] or 0,
            "total_cost_usd": round(row["cost"] or 0, 6),
            "by_model": by_model,
            "by_provider": by_provider,
            "by_day": by_day,
        }

    # ---- Lifecycle ----

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
            log.info("Memory store closed.")
