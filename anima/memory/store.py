"""SQLite backend + optional ChromaDB for memory storage.

All public DB methods are async (use asyncio.to_thread to avoid blocking
the event loop).  Methods that MUST also be callable from synchronous code
retain a ``*_sync`` sibling.
"""

from __future__ import annotations

import asyncio
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

-- Environment catalog (populated by EnvScanner)
CREATE TABLE IF NOT EXISTS env_catalog (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL DEFAULT 'file',
    size_bytes INTEGER DEFAULT 0,
    modified_at REAL DEFAULT 0,
    scanned_at REAL DEFAULT 0,
    scan_layer INTEGER DEFAULT 1,
    category TEXT DEFAULT 'other',
    extension TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    parent_dir TEXT DEFAULT '',
    is_important INTEGER DEFAULT 0,
    is_deleted INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_env_path ON env_catalog(path);
CREATE INDEX IF NOT EXISTS idx_env_parent ON env_catalog(parent_dir);
CREATE INDEX IF NOT EXISTS idx_env_category ON env_catalog(category);
CREATE INDEX IF NOT EXISTS idx_env_type ON env_catalog(type);
CREATE INDEX IF NOT EXISTS idx_env_important ON env_catalog(is_important);
CREATE INDEX IF NOT EXISTS idx_env_layer ON env_catalog(scan_layer);

-- Tier 1: Static knowledge with node partition
CREATE TABLE IF NOT EXISTS static_knowledge (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    source      TEXT DEFAULT 'user',
    importance  REAL DEFAULT 0.5,
    updated_at  REAL NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'global',
    node_id     TEXT,
    UNIQUE(category, key, scope)
);
CREATE INDEX IF NOT EXISTS idx_sk_scope ON static_knowledge(scope);
CREATE INDEX IF NOT EXISTS idx_sk_category_scope ON static_knowledge(category, scope);
CREATE INDEX IF NOT EXISTS idx_sk_key ON static_knowledge(key);

CREATE TABLE IF NOT EXISTS env_scan_progress (
    id TEXT PRIMARY KEY,
    status TEXT DEFAULT 'pending',
    total_dirs INTEGER DEFAULT 0,
    scanned_dirs INTEGER DEFAULT 0,
    total_files INTEGER DEFAULT 0,
    last_scanned_path TEXT DEFAULT '',
    started_at REAL DEFAULT 0,
    completed_at REAL DEFAULT 0,
    updated_at REAL DEFAULT 0
);
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

    # ------------------------------------------------------------------ #
    #  Factory                                                             #
    # ------------------------------------------------------------------ #

    @classmethod
    async def create(cls, db_path: str) -> MemoryStore:
        """Factory method — creates and initializes the store."""
        store = cls(db_path)

        def _init_db() -> None:
            store._conn = sqlite3.connect(store._db_path, check_same_thread=False)
            store._conn.row_factory = sqlite3.Row
            store._conn.executescript(_SCHEMA)
            # Migrate: add columns that may not exist in older DBs
            for col, default in [("sync_seq", "0"), ("content_hash", "''"), ("decay_score", "NULL")]:
                try:
                    col_type = "INTEGER" if default == "0" else ("REAL" if default == "NULL" else "TEXT")
                    store._conn.execute(f"ALTER TABLE episodic_memories ADD COLUMN {col} {col_type} DEFAULT {default}")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            # Create indexes for sync columns
            try:
                store._conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_seq ON episodic_memories(sync_seq)")
                store._conn.execute("CREATE INDEX IF NOT EXISTS idx_content_hash ON episodic_memories(content_hash)")
            except sqlite3.OperationalError:
                pass
            store._conn.commit()

        await asyncio.to_thread(_init_db)
        log.info("Memory store initialized: %s", store._db_path)

        # Optional ChromaDB
        if HAS_CHROMADB:
            try:
                def _init_chroma() -> Any:
                    chroma_path = Path(store._db_path).parent / "chroma"
                    chroma_path.mkdir(exist_ok=True)
                    client = chromadb.PersistentClient(path=str(chroma_path))
                    return client.get_or_create_collection("episodic")

                store._chroma_collection = await asyncio.to_thread(_init_chroma)
                log.info("ChromaDB initialized for vector search")
            except Exception as e:
                log.warning("ChromaDB init failed, falling back to SQLite: %s", e)

        return store

    # ------------------------------------------------------------------ #
    #  Internal helpers (pure computation — stay sync)                     #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  Episodic Memory                                                     #
    # ------------------------------------------------------------------ #

    def _save_memory_sync(
        self,
        content: str,
        type: str,
        importance: float,
        metadata: dict,
        tags: list[str],
    ) -> str:
        """Sync inner — runs in thread."""
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
             json.dumps(metadata), json.dumps(tags),
             seq, chash),
        )
        self._conn.commit()
        return mid

    def save_memory(
        self,
        content: str,
        type: str,
        importance: float = 0.5,
        metadata: dict | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Save an episodic memory (sync backward-compat). Returns the ID."""
        mid = self._save_memory_sync(
            content, type, importance, metadata or {}, tags or [],
        )
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

    async def save_memory_async(
        self,
        content: str,
        type: str,
        importance: float = 0.5,
        metadata: dict | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Save an episodic memory (non-blocking). Returns the ID."""
        mid = await asyncio.to_thread(
            self._save_memory_sync, content, type, importance,
            metadata or {}, tags or [],
        )

        # Optional: add to ChromaDB (also in thread)
        if self._chroma_collection is not None:
            try:
                await asyncio.to_thread(
                    self._chroma_collection.add,
                    ids=[mid], documents=[content],
                    metadatas=[{"type": type, "importance": importance}],
                )
            except Exception as e:
                log.debug("ChromaDB add failed: %s", e)

        return mid

    def _search_memories_sync(
        self,
        query: str | None,
        type: str | None,
        limit: int,
    ) -> list[dict]:
        """Sync inner — runs in thread."""
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

    def search_memories(
        self,
        query: str | None = None,
        type: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Search episodic memories (sync backward-compat)."""
        return self._search_memories_sync(query, type, limit)

    async def search_memories_async(
        self,
        query: str | None = None,
        type: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Search episodic memories (non-blocking)."""
        return await asyncio.to_thread(
            self._search_memories_sync, query, type, limit,
        )

    def _get_recent_memories_sync(self, limit: int, type: str | None) -> list[dict]:
        """Sync inner — runs in thread."""
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

    def get_recent_memories_sync(self, limit: int = 10, type: str | None = None) -> list[dict]:
        """Sync version — for use from sync callers (e.g. load_conversation_from_db)."""
        return self._get_recent_memories_sync(limit, type)

    # Keep backward-compat name pointing to sync for callers that haven't migrated
    def get_recent_memories(self, limit: int = 10, type: str | None = None) -> list[dict]:
        """Sync backward-compat shim (prefer async get_recent_memories_async)."""
        return self._get_recent_memories_sync(limit, type)

    async def get_recent_memories_async(self, limit: int = 10, type: str | None = None) -> list[dict]:
        """Get most recent memories (non-blocking)."""
        return await asyncio.to_thread(
            self._get_recent_memories_sync, limit, type,
        )

    # ------------------------------------------------------------------ #
    #  Emotion Log                                                         #
    # ------------------------------------------------------------------ #

    def _log_emotion_sync(
        self,
        engagement: float,
        confidence: float,
        curiosity: float,
        concern: float,
        trigger: str,
    ) -> None:
        """Sync inner — runs in thread."""
        self._conn.execute(
            "INSERT INTO emotion_log (id, engagement, confidence, curiosity, concern, trigger, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (gen_id("emo"), engagement, confidence, curiosity, concern, trigger, time.time()),
        )
        self._conn.commit()

    def log_emotion(
        self,
        engagement: float,
        confidence: float,
        curiosity: float,
        concern: float,
        trigger: str = "",
    ) -> None:
        """Log an emotion snapshot (sync backward-compat)."""
        self._log_emotion_sync(engagement, confidence, curiosity, concern, trigger)

    async def log_emotion_async(
        self,
        engagement: float,
        confidence: float,
        curiosity: float,
        concern: float,
        trigger: str = "",
    ) -> None:
        """Log an emotion snapshot (non-blocking)."""
        await asyncio.to_thread(
            self._log_emotion_sync,
            engagement, confidence, curiosity, concern, trigger,
        )

    # ------------------------------------------------------------------ #
    #  State Snapshots                                                     #
    # ------------------------------------------------------------------ #

    def _save_snapshot_sync(self, state: dict) -> None:
        """Sync inner — runs in thread."""
        self._conn.execute(
            "INSERT INTO state_snapshots (id, state_json, timestamp) VALUES (?, ?, ?)",
            (gen_id("snap"), json.dumps(state), time.time()),
        )
        self._conn.commit()

    def save_snapshot(self, state: dict) -> None:
        """Save a state snapshot (sync backward-compat)."""
        self._save_snapshot_sync(state)

    async def save_snapshot_async(self, state: dict) -> None:
        """Save a state snapshot (non-blocking)."""
        await asyncio.to_thread(self._save_snapshot_sync, state)

    # ------------------------------------------------------------------ #
    #  Audit Log                                                           #
    # ------------------------------------------------------------------ #

    def audit_sync(self, action: str, details: str = "") -> None:
        """Sync version — for use from sync callers."""
        self._conn.execute(
            "INSERT INTO audit_log (id, action, details, timestamp) VALUES (?, ?, ?, ?)",
            (gen_id("audit"), action, details, time.time()),
        )
        self._conn.commit()

    # Keep backward-compat name pointing to sync for callers that haven't migrated
    def audit(self, action: str, details: str = "") -> None:
        """Sync backward-compat shim (prefer async audit_async)."""
        self.audit_sync(action, details)

    async def audit_async(self, action: str, details: str = "") -> None:
        """Audit log entry (non-blocking)."""
        await asyncio.to_thread(self.audit_sync, action, details)

    # ------------------------------------------------------------------ #
    #  LLM Usage                                                           #
    # ------------------------------------------------------------------ #

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

    def _log_llm_usage_sync(
        self,
        model: str,
        provider: str,
        auth_mode: str,
        tier: str,
        prompt_tokens: int,
        completion_tokens: int,
        event_type: str,
        success: bool,
    ) -> None:
        """Sync inner — runs in thread."""
        total = prompt_tokens + completion_tokens
        cost = self._estimate_cost(model, prompt_tokens, completion_tokens)
        self._conn.execute(
            "INSERT INTO llm_usage (id, timestamp, model, provider, auth_mode, tier, prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd, event_type, success) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (gen_id("llm"), time.time(), model, provider, auth_mode, tier,
             prompt_tokens, completion_tokens, total, cost, event_type, int(success)),
        )
        self._conn.commit()

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
        """Record an LLM API call (sync backward-compat)."""
        self._log_llm_usage_sync(
            model, provider, auth_mode, tier,
            prompt_tokens, completion_tokens, event_type, success,
        )

    async def log_llm_usage_async(
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
        """Record an LLM API call (non-blocking)."""
        await asyncio.to_thread(
            self._log_llm_usage_sync,
            model, provider, auth_mode, tier,
            prompt_tokens, completion_tokens, event_type, success,
        )

    def _get_usage_history_sync(self, limit: int) -> list[dict]:
        """Sync inner — runs in thread."""
        rows = self._conn.execute(
            "SELECT * FROM llm_usage ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_usage_history(self, limit: int = 100) -> list[dict]:
        """Return recent LLM usage records (sync backward-compat)."""
        return self._get_usage_history_sync(limit)

    async def get_usage_history_async(self, limit: int = 100) -> list[dict]:
        """Return recent LLM usage records (non-blocking)."""
        return await asyncio.to_thread(self._get_usage_history_sync, limit)

    def _get_usage_summary_sync(self) -> dict:
        """Sync inner — runs in thread."""
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

    def get_usage_summary(self) -> dict:
        """Return usage totals (sync backward-compat)."""
        return self._get_usage_summary_sync()

    async def get_usage_summary_async(self) -> dict:
        """Return usage totals grouped by model, provider, and day (non-blocking)."""
        return await asyncio.to_thread(self._get_usage_summary_sync)

    # ------------------------------------------------------------------ #
    #  Environment Catalog                                                 #
    # ------------------------------------------------------------------ #

    def _upsert_env_catalog_batch_sync(self, entries: list[dict]) -> None:
        """Sync inner — runs in thread."""
        if not entries:
            return
        for e in entries:
            self._conn.execute(
                "INSERT OR REPLACE INTO env_catalog "
                "(id, path, type, size_bytes, modified_at, scanned_at, scan_layer, "
                "category, extension, parent_dir, is_important) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (e["id"], e["path"], e["type"], e.get("size_bytes", 0),
                 e.get("modified_at", 0), e.get("scanned_at", 0),
                 e.get("scan_layer", 1), e.get("category", "other"),
                 e.get("extension", ""), e.get("parent_dir", ""),
                 e.get("is_important", 0)),
            )
        self._conn.commit()

    # Keep backward-compat sync name — EnvScanner calls this from sync code
    def upsert_env_catalog_batch(self, entries: list[dict]) -> None:
        """Sync backward-compat shim."""
        self._upsert_env_catalog_batch_sync(entries)

    async def upsert_env_catalog_batch_async(self, entries: list[dict]) -> None:
        """Insert or update a batch of env_catalog entries (non-blocking)."""
        await asyncio.to_thread(self._upsert_env_catalog_batch_sync, entries)

    def _update_scan_progress_sync(self, layer_id: str, status: str, **kwargs: Any) -> None:
        """Sync inner — runs in thread."""
        now = time.time()
        row = self._conn.execute(
            "SELECT id FROM env_scan_progress WHERE id = ?", (layer_id,)
        ).fetchone()
        if row:
            sets = ["status = ?", "updated_at = ?"]
            vals: list = [status, now]
            for k, v in kwargs.items():
                sets.append(f"{k} = ?")
                vals.append(v)
            if status == "completed":
                sets.append("completed_at = ?")
                vals.append(now)
            vals.append(layer_id)
            self._conn.execute(
                f"UPDATE env_scan_progress SET {', '.join(sets)} WHERE id = ?",
                vals,
            )
        else:
            cols = ["id", "status", "started_at", "updated_at"]
            vals_list: list = [layer_id, status, now, now]
            for k, v in kwargs.items():
                cols.append(k)
                vals_list.append(v)
            if status == "completed":
                cols.append("completed_at")
                vals_list.append(now)
            placeholders = ", ".join("?" * len(cols))
            self._conn.execute(
                f"INSERT INTO env_scan_progress ({', '.join(cols)}) VALUES ({placeholders})",
                vals_list,
            )
        self._conn.commit()

    # Keep backward-compat sync name — EnvScanner calls this from sync code
    def update_scan_progress(self, layer_id: str, status: str, **kwargs: Any) -> None:
        """Sync backward-compat shim."""
        self._update_scan_progress_sync(layer_id, status, **kwargs)

    async def update_scan_progress_async(self, layer_id: str, status: str, **kwargs: Any) -> None:
        """Update scan progress for a layer (non-blocking)."""
        await asyncio.to_thread(self._update_scan_progress_sync, layer_id, status, **kwargs)

    def _get_scan_progress_sync(self, layer_id: str) -> dict | None:
        """Sync inner — runs in thread."""
        row = self._conn.execute(
            "SELECT * FROM env_scan_progress WHERE id = ?", (layer_id,)
        ).fetchone()
        return dict(row) if row else None

    # Keep backward-compat sync name — called from get_env_stats_sync internally
    def get_scan_progress(self, layer_id: str) -> dict | None:
        """Sync backward-compat shim."""
        return self._get_scan_progress_sync(layer_id)

    async def get_scan_progress_async(self, layer_id: str) -> dict | None:
        """Get scan progress (non-blocking)."""
        return await asyncio.to_thread(self._get_scan_progress_sync, layer_id)

    def _search_env_catalog_sync(
        self,
        query: str,
        category: str,
        extension: str,
        file_type: str,
        limit: int,
    ) -> list[dict]:
        """Sync inner — runs in thread."""
        conditions = ["is_deleted = 0"]
        params: list = []
        if query:
            conditions.append("(path LIKE ? OR summary LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        if category:
            conditions.append("category = ?")
            params.append(category)
        if extension:
            conditions.append("extension = ?")
            params.append(extension)
        if file_type:
            conditions.append("type = ?")
            params.append(file_type)
        where = " AND ".join(conditions)
        rows = self._conn.execute(
            f"SELECT path, type, size_bytes, category, extension, summary, "
            f"is_important, scan_layer, modified_at "
            f"FROM env_catalog WHERE {where} "
            f"ORDER BY is_important DESC, modified_at DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [dict(r) for r in rows]

    # Keep backward-compat sync name — env_tools calls this from sync code
    def search_env_catalog(self, query: str = "", category: str = "",
                           extension: str = "", file_type: str = "",
                           limit: int = 20) -> list[dict]:
        """Sync backward-compat shim."""
        return self._search_env_catalog_sync(query, category, extension, file_type, limit)

    async def search_env_catalog_async(self, query: str = "", category: str = "",
                                       extension: str = "", file_type: str = "",
                                       limit: int = 20) -> list[dict]:
        """Search the environment catalog (non-blocking)."""
        return await asyncio.to_thread(
            self._search_env_catalog_sync,
            query, category, extension, file_type, limit,
        )

    def _get_env_stats_sync(self) -> dict:
        """Sync inner — runs in thread."""
        total_files = self._conn.execute(
            "SELECT COUNT(*) FROM env_catalog WHERE type='file' AND is_deleted=0"
        ).fetchone()[0]
        total_dirs = self._conn.execute(
            "SELECT COUNT(*) FROM env_catalog WHERE type='directory' AND is_deleted=0"
        ).fetchone()[0]
        important = self._conn.execute(
            "SELECT COUNT(*) FROM env_catalog WHERE is_important=1 AND is_deleted=0"
        ).fetchone()[0]
        summarized = self._conn.execute(
            "SELECT COUNT(*) FROM env_catalog WHERE summary != '' AND is_deleted=0"
        ).fetchone()[0]
        by_category = {}
        for row in self._conn.execute(
            "SELECT category, COUNT(*) as cnt FROM env_catalog WHERE is_deleted=0 GROUP BY category"
        ).fetchall():
            by_category[row[0]] = row[1]

        progress = {}
        for lid in ("layer1", "layer2", "layer3"):
            p = self._get_scan_progress_sync(lid)
            progress[lid] = p if p else {"status": "pending"}

        return {
            "total_files": total_files,
            "total_dirs": total_dirs,
            "important_files": important,
            "summarized": summarized,
            "by_category": by_category,
            "scan_progress": progress,
        }

    # Keep backward-compat sync name — env_tools calls this from sync code
    def get_env_stats(self) -> dict:
        """Sync backward-compat shim."""
        return self._get_env_stats_sync()

    async def get_env_stats_async(self) -> dict:
        """Get environment scan statistics (non-blocking)."""
        return await asyncio.to_thread(self._get_env_stats_sync)

    def _get_scanned_dirs_sync(self, max_layer: int) -> list[dict]:
        """Sync inner — runs in thread."""
        rows = self._conn.execute(
            "SELECT path, scan_layer FROM env_catalog "
            "WHERE type='directory' AND scan_layer <= ? AND is_deleted=0",
            (max_layer,),
        ).fetchall()
        return [dict(r) for r in rows]

    # Keep backward-compat sync name — EnvScanner calls this from sync code
    def get_scanned_dirs(self, max_layer: int = 2) -> list[dict]:
        """Sync backward-compat shim."""
        return self._get_scanned_dirs_sync(max_layer)

    async def get_scanned_dirs_async(self, max_layer: int = 2) -> list[dict]:
        """Get directories scanned up to given layer (non-blocking)."""
        return await asyncio.to_thread(self._get_scanned_dirs_sync, max_layer)

    def _get_env_files_in_dir_sync(self, dir_path: str) -> list[dict]:
        """Sync inner — runs in thread."""
        rows = self._conn.execute(
            "SELECT path, modified_at, size_bytes FROM env_catalog "
            "WHERE parent_dir = ? AND is_deleted=0",
            (dir_path.replace("\\", "/"),),
        ).fetchall()
        return [dict(r) for r in rows]

    # Keep backward-compat sync name — EnvScanner calls this from sync code
    def get_env_files_in_dir(self, dir_path: str) -> list[dict]:
        """Sync backward-compat shim."""
        return self._get_env_files_in_dir_sync(dir_path)

    async def get_env_files_in_dir_async(self, dir_path: str) -> list[dict]:
        """Get known files in a specific directory (non-blocking)."""
        return await asyncio.to_thread(self._get_env_files_in_dir_sync, dir_path)

    def _get_unscanned_dirs_sync(self) -> list[str]:
        """Sync inner — runs in thread."""
        rows = self._conn.execute(
            "SELECT path FROM env_catalog "
            "WHERE type='directory' AND scan_layer=1 AND is_deleted=0 "
            "AND path NOT IN ("
            "  SELECT DISTINCT parent_dir FROM env_catalog WHERE scan_layer >= 2"
            ")"
        ).fetchall()
        return [r[0] for r in rows]

    # Keep backward-compat sync name — EnvScanner calls this from sync code
    def get_unscanned_dirs(self) -> list[str]:
        """Sync backward-compat shim."""
        return self._get_unscanned_dirs_sync()

    async def get_unscanned_dirs_async(self) -> list[str]:
        """Get layer1 directories not yet scanned by layer2/3 (non-blocking)."""
        return await asyncio.to_thread(self._get_unscanned_dirs_sync)

    def _mark_env_deleted_sync(self, path: str) -> None:
        """Sync inner — runs in thread."""
        self._conn.execute(
            "UPDATE env_catalog SET is_deleted=1 WHERE path=?",
            (path.replace("\\", "/"),),
        )
        self._conn.commit()

    # Keep backward-compat sync name — EnvScanner calls this from sync code
    def mark_env_deleted(self, path: str) -> None:
        """Sync backward-compat shim."""
        self._mark_env_deleted_sync(path)

    async def mark_env_deleted_async(self, path: str) -> None:
        """Mark an env entry as deleted (non-blocking)."""
        await asyncio.to_thread(self._mark_env_deleted_sync, path)

    def _update_env_entry_sync(self, path: str, updates: dict) -> None:
        """Sync inner — runs in thread."""
        if not updates:
            return
        sets = []
        vals = []
        for k, v in updates.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(path.replace("\\", "/"))
        self._conn.execute(
            f"UPDATE env_catalog SET {', '.join(sets)} WHERE path = ?", vals
        )
        self._conn.commit()

    # Keep backward-compat sync name — EnvScanner calls this from sync code
    def update_env_entry(self, path: str, updates: dict) -> None:
        """Sync backward-compat shim."""
        self._update_env_entry_sync(path, updates)

    async def update_env_entry_async(self, path: str, updates: dict) -> None:
        """Update an env catalog entry (non-blocking)."""
        await asyncio.to_thread(self._update_env_entry_sync, path, updates)

    def _get_unsummarized_important_files_sync(self, limit: int) -> list[dict]:
        """Sync inner — runs in thread."""
        rows = self._conn.execute(
            "SELECT path, category, extension FROM env_catalog "
            "WHERE is_important=1 AND summary='' AND is_deleted=0 "
            "ORDER BY modified_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_unsummarized_important_files(self, limit: int = 10) -> list[dict]:
        """Get unsummarized important files (sync backward-compat)."""
        return self._get_unsummarized_important_files_sync(limit)

    async def get_unsummarized_important_files_async(self, limit: int = 10) -> list[dict]:
        """Get unsummarized important files (non-blocking)."""
        return await asyncio.to_thread(
            self._get_unsummarized_important_files_sync, limit,
        )

    # ------------------------------------------------------------------ #
    #  Memory Retrieval Helpers (v3)                                       #
    # ------------------------------------------------------------------ #

    def _touch_memories_sync(self, ids: list[str]) -> None:
        """Sync inner — runs in thread."""
        if not ids:
            return
        now = time.time()
        for mid in ids:
            self._conn.execute(
                "UPDATE episodic_memories SET access_count = access_count + 1, last_accessed = ? WHERE id = ?",
                (now, mid),
            )
        self._conn.commit()

    # Keep backward-compat sync name — retriever calls this from sync code
    def touch_memories(self, ids: list[str]) -> None:
        """Sync backward-compat shim."""
        self._touch_memories_sync(ids)

    async def touch_memories_async(self, ids: list[str]) -> None:
        """Update access_count and last_accessed for retrieved memories (non-blocking)."""
        await asyncio.to_thread(self._touch_memories_sync, ids)

    def _get_memories_below_threshold_sync(self, threshold: float) -> list[dict]:
        """Sync inner — runs in thread."""
        rows = self._conn.execute(
            "SELECT * FROM episodic_memories WHERE decay_score IS NOT NULL AND decay_score < ? "
            "AND (id NOT IN (SELECT id FROM episodic_memories WHERE content_hash IN "
            "(SELECT content_hash FROM episodic_memories GROUP BY content_hash HAVING COUNT(*) > 1 AND MIN(created_at) = created_at))) "
            "ORDER BY decay_score ASC LIMIT 200",
            (threshold,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_memories_below_threshold(self, threshold: float) -> list[dict]:
        """Get unconsolidated memories with decay_score below threshold (sync backward-compat)."""
        return self._get_memories_below_threshold_sync(threshold)

    async def get_memories_below_threshold_async(self, threshold: float) -> list[dict]:
        """Get unconsolidated memories with decay_score below threshold (non-blocking)."""
        return await asyncio.to_thread(
            self._get_memories_below_threshold_sync, threshold,
        )

    def _get_unconsolidated_memories_sync(self, limit: int) -> list[dict]:
        """Sync inner — runs in thread."""
        rows = self._conn.execute(
            "SELECT id, type, importance, created_at, last_accessed, access_count, decay_score "
            "FROM episodic_memories ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # Keep backward-compat sync name — tests call this from sync code
    def get_unconsolidated_memories(self, limit: int = 500) -> list[dict]:
        """Sync backward-compat shim."""
        return self._get_unconsolidated_memories_sync(limit)

    async def get_unconsolidated_memories_async(self, limit: int = 500) -> list[dict]:
        """Get all memories not yet consolidated (non-blocking)."""
        return await asyncio.to_thread(
            self._get_unconsolidated_memories_sync, limit,
        )

    def _batch_update_decay_scores_sync(self, updates: list[tuple[str, float]]) -> None:
        """Sync inner — runs in thread."""
        if not updates:
            return
        for mid, score in updates:
            self._conn.execute(
                "UPDATE episodic_memories SET decay_score = ? WHERE id = ?",
                (score, mid),
            )
        self._conn.commit()

    # Keep backward-compat sync name — tests call this from sync code
    def batch_update_decay_scores(self, updates: list[tuple[str, float]]) -> None:
        """Sync backward-compat shim."""
        self._batch_update_decay_scores_sync(updates)

    async def batch_update_decay_scores_async(self, updates: list[tuple[str, float]]) -> None:
        """Batch update decay_score for memories (non-blocking)."""
        await asyncio.to_thread(self._batch_update_decay_scores_sync, updates)

    def _mark_consolidated_sync(self, ids: list[str]) -> None:
        """Sync inner — runs in thread."""
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        self._conn.execute(
            f"UPDATE episodic_memories SET content_hash = 'consolidated:' || content_hash WHERE id IN ({placeholders})",
            ids,
        )
        self._conn.commit()

    def mark_consolidated(self, ids: list[str]) -> None:
        """Mark memories as consolidated (sync backward-compat)."""
        self._mark_consolidated_sync(ids)

    async def mark_consolidated_async(self, ids: list[str]) -> None:
        """Mark memories as consolidated (non-blocking)."""
        await asyncio.to_thread(self._mark_consolidated_sync, ids)

    def _archive_to_knowledge_sync(self, summary: str, source_ids: list[str],
                                   metadata: dict | None) -> str:
        """Sync inner — runs in thread."""
        mid = gen_id("archive")
        now = time.time()
        meta = metadata or {}
        meta["source_ids"] = source_ids
        meta["archived"] = True
        self._conn.execute(
            "INSERT INTO episodic_memories "
            "(id, type, content, importance, access_count, created_at, last_accessed, "
            "metadata_json, tags_json, sync_seq, content_hash) "
            "VALUES (?, ?, ?, ?, 0, ?, ?, ?, '[]', ?, ?)",
            (mid, "archive", summary, 0.6, now, now,
             json.dumps(meta), self._next_sync_seq(),
             self._content_hash(summary, "archive")),
        )
        self._conn.commit()
        return mid

    def archive_to_knowledge(self, summary: str, source_ids: list[str],
                              metadata: dict | None = None) -> str:
        """Archive a consolidated summary as a new memory (sync backward-compat)."""
        mid = self._archive_to_knowledge_sync(summary, source_ids, metadata)
        # Also add to ChromaDB for semantic search
        if self._chroma_collection is not None:
            try:
                self._chroma_collection.add(
                    ids=[mid], documents=[summary],
                    metadatas=[{"type": "archive", "importance": 0.6}],
                )
            except Exception:
                pass
        return mid

    async def archive_to_knowledge_async(self, summary: str, source_ids: list[str],
                                         metadata: dict | None = None) -> str:
        """Archive a consolidated summary as a new memory (non-blocking)."""
        mid = await asyncio.to_thread(
            self._archive_to_knowledge_sync, summary, source_ids, metadata,
        )
        # Also add to ChromaDB for semantic search
        if self._chroma_collection is not None:
            try:
                await asyncio.to_thread(
                    self._chroma_collection.add,
                    ids=[mid], documents=[summary],
                    metadatas=[{"type": "archive", "importance": 0.6}],
                )
            except Exception:
                pass
        return mid

    # ------------------------------------------------------------------ #
    #  Static Knowledge (Tier 1)                                           #
    # ------------------------------------------------------------------ #

    def _query_static_knowledge_sync(
        self,
        categories: list[str] | None,
        keywords: list[str] | None,
        scopes: list[str] | None,
        limit: int,
    ) -> list[dict]:
        """Sync inner — runs in thread."""
        conditions = []
        params: list[Any] = []
        if categories:
            placeholders = ",".join("?" * len(categories))
            conditions.append(f"category IN ({placeholders})")
            params.extend(categories)
        if scopes:
            placeholders = ",".join("?" * len(scopes))
            conditions.append(f"scope IN ({placeholders})")
            params.extend(scopes)
        if keywords:
            kw_conditions = []
            for kw in keywords:
                kw_conditions.append("(key LIKE ? OR value LIKE ?)")
                params.extend([f"%{kw}%", f"%{kw}%"])
            conditions.append("(" + " OR ".join(kw_conditions) + ")")
        where = " AND ".join(conditions) if conditions else "1=1"
        rows = self._conn.execute(
            f"SELECT * FROM static_knowledge WHERE {where} ORDER BY importance DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [dict(r) for r in rows]

    # Keep backward-compat sync name — StaticKnowledgeStore calls this from sync code
    def query_static_knowledge(
        self,
        categories: list[str] | None = None,
        keywords: list[str] | None = None,
        scopes: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Sync backward-compat shim."""
        return self._query_static_knowledge_sync(categories, keywords, scopes, limit)

    async def query_static_knowledge_async(
        self,
        categories: list[str] | None = None,
        keywords: list[str] | None = None,
        scopes: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Query static knowledge with optional filtering (non-blocking)."""
        return await asyncio.to_thread(
            self._query_static_knowledge_sync,
            categories, keywords, scopes, limit,
        )

    def _upsert_static_knowledge_sync(
        self,
        category: str,
        key: str,
        value: str,
        scope: str,
        node_id: str | None,
        source: str,
        importance: float,
        updated_at: float | None,
    ) -> None:
        """Sync inner — runs in thread."""
        now = updated_at or time.time()
        self._conn.execute(
            "INSERT INTO static_knowledge (category, key, value, source, importance, updated_at, scope, node_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(category, key, scope) DO UPDATE SET "
            "value=excluded.value, source=excluded.source, importance=excluded.importance, "
            "updated_at=excluded.updated_at, node_id=excluded.node_id",
            (category, key, value, source, importance, now, scope, node_id),
        )
        self._conn.commit()

    # Keep backward-compat sync name — StaticKnowledgeStore calls this from sync code
    def upsert_static_knowledge(
        self,
        category: str,
        key: str,
        value: str,
        scope: str = "global",
        node_id: str | None = None,
        source: str = "agent",
        importance: float = 0.5,
        updated_at: float | None = None,
    ) -> None:
        """Sync backward-compat shim."""
        self._upsert_static_knowledge_sync(
            category, key, value, scope, node_id, source, importance, updated_at,
        )

    async def upsert_static_knowledge_async(
        self,
        category: str,
        key: str,
        value: str,
        scope: str = "global",
        node_id: str | None = None,
        source: str = "agent",
        importance: float = 0.5,
        updated_at: float | None = None,
    ) -> None:
        """Insert or update a static knowledge entry (non-blocking)."""
        await asyncio.to_thread(
            self._upsert_static_knowledge_sync,
            category, key, value, scope, node_id, source, importance, updated_at,
        )

    def _delete_static_knowledge_sync(self, category: str, key: str, scope: str) -> bool:
        """Sync inner — runs in thread."""
        cursor = self._conn.execute(
            "DELETE FROM static_knowledge WHERE category=? AND key=? AND scope=?",
            (category, key, scope),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    # Keep backward-compat sync name — StaticKnowledgeStore calls this from sync code
    def delete_static_knowledge(self, category: str, key: str, scope: str = "global") -> bool:
        """Sync backward-compat shim."""
        return self._delete_static_knowledge_sync(category, key, scope)

    async def delete_static_knowledge_async(self, category: str, key: str, scope: str = "global") -> bool:
        """Delete a static knowledge entry (non-blocking)."""
        return await asyncio.to_thread(
            self._delete_static_knowledge_sync, category, key, scope,
        )

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    async def close(self) -> None:
        if self._conn:
            await asyncio.to_thread(self._conn.close)
            self._conn = None
            log.info("Memory store closed.")
