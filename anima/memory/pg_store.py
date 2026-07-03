"""Postgres-backed MemoryStore (Neon + pgvector) — the cloud memory backend.

Mirrors the public API of memory.store.MemoryStore so it's a drop-in via the
factory in store.py. CORE methods (episodic save+embed, session/recent/semantic
retrieval, emotion, audit/snapshot/usage, static knowledge, touch) are
implemented and verified against Neon. Operational maintenance methods
(env_catalog scanner, decay/consolidation) are stubbed (logged no-ops) and are
ported next; they are not on Eva's hot path.

Compat: episodic rows are normalized so metadata_json/tags_json come back as
JSON *strings* (as SQLite returned them) and the raw embedding is dropped, so
existing callers (which json.loads the metadata) keep working unchanged.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from anima.memory import embedder
from anima.memory.pg_db import PgDatabaseManager
from anima.utils.ids import gen_id
from anima.utils.logging import get_logger

log = get_logger("pg_store")

# Episodic types whose content is worth a semantic embedding.
_VECTORIZE = {"chat", "observation", "self_thought", "thought", "archive",
              "note", "reflection", "insight"}


def _vec_literal(vec: list[float]) -> str:
    """pgvector text literal: '[0.1,0.2,...]' for %s::vector binding."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


class PgMemoryStore:
    _sync_seq_counter: int = 0
    _sync_seq_lock = threading.Lock()

    def __init__(self, db: PgDatabaseManager) -> None:
        self._db = db
        # Which node/locus produced a memory. Set by main from the node identity;
        # stamped into every memory's metadata so the distributed self can later
        # reconcile origin-tagged rows and recall "what I did AS pidog / at desktop"
        # (DISTRIBUTED_DESIGN §3, Phase-1 no-migration: lives in metadata_json).
        self.origin_node: str = ""
        self.locus: str = ""

    def _tag_origin(self, metadata: dict | None) -> dict:
        """Stamp origin_node + locus into a memory's metadata (non-destructive)."""
        m = dict(metadata or {})
        if self.origin_node:
            m.setdefault("origin_node", self.origin_node)
        if self.locus:
            m.setdefault("locus", self.locus)
        return m

    @classmethod
    async def create(cls, db_path: str = "", dsn: str = "") -> "PgMemoryStore":
        """Connect and apply the schema. dsn overrides DATABASE_URL (tests / a
        specific instance); default = Neon primary + local fallback."""
        db = PgDatabaseManager(dsn=dsn) if dsn else PgDatabaseManager()
        schema = (Path(__file__).parent / "pg_schema.sql").read_text(encoding="utf-8")
        await db.init(schema)
        log.info("PgMemoryStore ready (backend=Postgres%s)",
                 " [local fallback]" if db.using_local else "")
        return cls(db)

    async def close(self) -> None:
        await self._db.close()

    # ── helpers ──

    def _next_sync_seq(self) -> int:
        with PgMemoryStore._sync_seq_lock:
            PgMemoryStore._sync_seq_counter += 1
            return PgMemoryStore._sync_seq_counter

    @staticmethod
    def _content_hash(content: str, type_: str) -> str:
        import hashlib
        return hashlib.sha256(f"{content}:{type_}".encode()).hexdigest()[:16]

    @staticmethod
    def _normalize(row: dict | None) -> dict | None:
        """Make a PG episodic row shape-compatible with the old SQLite rows:
        metadata_json/tags_json as JSON strings, drop the raw embedding."""
        if not row:
            return row
        r = dict(row)
        r.pop("embedding", None)
        mj = r.get("metadata_json")
        if isinstance(mj, (dict, list)):
            r["metadata_json"] = json.dumps(mj, ensure_ascii=False)
        tj = r.get("tags_json")
        if isinstance(tj, (dict, list)):
            r["tags_json"] = json.dumps(tj, ensure_ascii=False)
        return r

    async def _embed(self, content: str, type_: str) -> str | None:
        if not content or type_ not in _VECTORIZE:
            return None
        vec = await embedder.embed_openai(content)
        return _vec_literal(vec) if vec else None

    # ── Episodic memory ──

    async def save_memory_async(self, content: str, type: str, importance: float = 0.5,
                                metadata: dict | None = None, tags: list[str] | None = None,
                                session_id: str = "") -> str:
        mid = gen_id("mem")
        now = time.time()
        emb = await self._embed(content, type)
        metadata = self._tag_origin(metadata)
        self._db.write_sync(
            "INSERT INTO episodic_memories "
            "(id,type,content,importance,access_count,created_at,last_accessed,"
            "metadata_json,tags_json,sync_seq,content_hash,decay_score,session_id,embedding) "
            "VALUES (%s,%s,%s,%s,0,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,"
            + ("%s::vector" if emb else "NULL") + ")",
            (mid, type, content, importance, now, now,
             json.dumps(metadata, ensure_ascii=False),
             json.dumps(tags or [], ensure_ascii=False),
             self._next_sync_seq(), self._content_hash(content, type),
             importance, session_id or "local", *( (emb,) if emb else () )),
        )
        return mid

    def save_memory(self, content: str, type: str, importance: float = 0.5,
                    metadata: dict | None = None, tags: list[str] | None = None,
                    session_id: str = "") -> str:
        """Sync save (no embedding — sync path can't await OpenAI)."""
        mid = gen_id("mem")
        now = time.time()
        metadata = self._tag_origin(metadata)
        self._db.write_sync(
            "INSERT INTO episodic_memories "
            "(id,type,content,importance,access_count,created_at,last_accessed,"
            "metadata_json,tags_json,sync_seq,content_hash,decay_score,session_id) "
            "VALUES (%s,%s,%s,%s,0,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s)",
            (mid, type, content, importance, now, now,
             json.dumps(metadata, ensure_ascii=False),
             json.dumps(tags or [], ensure_ascii=False),
             self._next_sync_seq(), self._content_hash(content, type),
             importance, session_id or "local"),
        )
        return mid

    def _get_recent_sync(self, limit: int, type: str | None) -> list[dict]:
        if type:
            rows = self._db.fetch_sync(
                "SELECT * FROM episodic_memories WHERE type=%s "
                "ORDER BY created_at DESC LIMIT %s", (type, limit))
        else:
            rows = self._db.fetch_sync(
                "SELECT * FROM episodic_memories ORDER BY created_at DESC LIMIT %s", (limit,))
        return [self._normalize(r) for r in rows]

    def get_recent_memories(self, limit: int = 10, type: str | None = None,
                            source: str | None = None) -> list[dict]:
        return self._get_recent_sync(limit, type)

    get_recent_memories_sync = get_recent_memories

    async def get_recent_memories_async(self, limit: int = 10, type: str | None = None,
                                        source: str | None = None) -> list[dict]:
        import asyncio
        return await asyncio.to_thread(self._get_recent_sync, limit, type)

    def _get_session_conversation_sync(self, session_id: str, limit: int) -> list[dict]:
        rows = self._db.fetch_sync(
            "SELECT content, metadata_json, created_at FROM episodic_memories "
            "WHERE type='chat' AND session_id=%s ORDER BY created_at DESC, sync_seq DESC LIMIT %s",
            (session_id or "local", limit))
        turns = []
        for r in rows:
            meta = r.get("metadata_json") or {}
            if isinstance(meta, str):
                try: meta = json.loads(meta)
                except Exception: meta = {}
            turns.append({"role": meta.get("role", "assistant"),
                          "content": r.get("content") or "",
                          "is_self_thought": bool(meta.get("is_self_thought", False)),
                          "created_at": r.get("created_at")})
        turns.reverse()
        return turns

    def get_session_conversation(self, session_id: str = "local", limit: int = 40) -> list[dict]:
        return self._get_session_conversation_sync(session_id, limit)

    async def get_session_conversation_async(self, session_id: str = "local", limit: int = 40) -> list[dict]:
        import asyncio
        return await asyncio.to_thread(self._get_session_conversation_sync, session_id, limit)

    async def search_memories_async(self, query: str | None = None, type: str | None = None,
                                    limit: int = 10) -> list[dict]:
        """Semantic search via pgvector cosine; falls back to recency/ILIKE."""
        if query:
            emb = await embedder.embed_openai(query)
            if emb:
                vlit = _vec_literal(emb)
                clause = " AND type=%s" if type else ""
                params: tuple = (vlit, *( (type,) if type else () ), vlit, limit)
                rows = await self._db.fetch(
                    "SELECT *, 1-(embedding <=> %s::vector) AS similarity FROM episodic_memories "
                    "WHERE embedding IS NOT NULL" + clause +
                    " ORDER BY embedding <=> %s::vector LIMIT %s", params)
                return [self._normalize(r) for r in rows]
        # No query / no embedding → recent (optionally ILIKE)
        if query:
            clause = " AND type=%s" if type else ""
            rows = await self._db.fetch(
                "SELECT * FROM episodic_memories WHERE content ILIKE %s" + clause +
                " ORDER BY importance DESC, created_at DESC LIMIT %s",
                (f"%{query}%", *( (type,) if type else () ), limit))
        else:
            rows = await self._db.fetch(
                "SELECT * FROM episodic_memories" + (" WHERE type=%s" if type else "") +
                " ORDER BY importance DESC, created_at DESC LIMIT %s",
                (*( (type,) if type else () ), limit))
        return [self._normalize(r) for r in rows]

    def search_memories(self, query: str | None = None, type: str | None = None,
                        limit: int = 10) -> list[dict]:
        rows = self._db.fetch_sync(
            "SELECT * FROM episodic_memories" + (" WHERE content ILIKE %s" if query else "") +
            " ORDER BY importance DESC, created_at DESC LIMIT %s",
            ((f"%{query}%", limit) if query else (limit,)))
        return [self._normalize(r) for r in rows]

    def _touch_sync(self, ids: list[str]) -> None:
        if not ids:
            return
        self._db.write_sync(
            "UPDATE episodic_memories SET access_count=access_count+1, last_accessed=%s "
            "WHERE id = ANY(%s)", (time.time(), list(ids)))

    def touch_memories(self, ids: list[str]) -> None:
        self._touch_sync(ids)

    async def touch_memories_async(self, ids: list[str]) -> None:
        import asyncio
        await asyncio.to_thread(self._touch_sync, ids)

    # ── Emotion ──

    async def log_emotion_async(self, engagement: float, confidence: float, curiosity: float,
                                concern: float, trigger: str = "") -> None:
        self._db.write_sync(
            "INSERT INTO emotion_log (id,engagement,confidence,curiosity,concern,trigger,timestamp) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (gen_id("emo"), engagement, confidence, curiosity, concern, trigger, time.time()))

    def log_emotion(self, engagement: float, confidence: float, curiosity: float,
                    concern: float, trigger: str = "") -> None:
        self._db.write_sync(
            "INSERT INTO emotion_log (id,engagement,confidence,curiosity,concern,trigger,timestamp) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (gen_id("emo"), engagement, confidence, curiosity, concern, trigger, time.time()))

    def get_latest_emotion(self) -> dict | None:
        return self._db.fetch_one_sync(
            "SELECT engagement,confidence,curiosity,concern,timestamp FROM emotion_log "
            "ORDER BY timestamp DESC LIMIT 1")

    async def get_latest_emotion_async(self) -> dict | None:
        import asyncio
        return await asyncio.to_thread(self.get_latest_emotion)

    # ── Audit / snapshot ──

    async def audit_async(self, action: str, details: str = "") -> None:
        self._db.write_sync(
            "INSERT INTO audit_log (id,action,details,timestamp) VALUES (%s,%s,%s,%s)",
            (gen_id("audit"), action, details, time.time()))

    def audit(self, action: str, details: str = "") -> None:
        self._db.write_sync(
            "INSERT INTO audit_log (id,action,details,timestamp) VALUES (%s,%s,%s,%s)",
            (gen_id("audit"), action, details, time.time()))

    async def save_snapshot_async(self, state: dict) -> None:
        self._db.write_sync(
            "INSERT INTO state_snapshots (id,state_json,timestamp) VALUES (%s,%s::jsonb,%s)",
            (gen_id("snap"), json.dumps(state, ensure_ascii=False), time.time()))

    def save_snapshot(self, state: dict) -> None:
        self._db.write_sync(
            "INSERT INTO state_snapshots (id,state_json,timestamp) VALUES (%s,%s::jsonb,%s)",
            (gen_id("snap"), json.dumps(state, ensure_ascii=False), time.time()))

    # ── LLM usage ──

    _COST_PER_1M = {"opus": (15.0, 75.0), "sonnet": (3.0, 15.0), "haiku": (0.25, 1.25),
                    "gpt-4o": (2.5, 10.0), "deepseek": (0.3, 1.2), "codex": (0.0, 0.0),
                    "local": (0.0, 0.0)}

    def _est_cost(self, model: str, pt: int, ct: int) -> float:
        m = model.lower()
        for k, (i, o) in self._COST_PER_1M.items():
            if k in m:
                return (pt * i + ct * o) / 1_000_000
        return (pt * 3.0 + ct * 15.0) / 1_000_000

    async def log_llm_usage_async(self, model: str, provider: str, auth_mode: str, tier: str,
                                  prompt_tokens: int, completion_tokens: int,
                                  event_type: str = "", success: bool = True) -> None:
        self._db.write_sync(
            "INSERT INTO llm_usage (id,timestamp,model,provider,auth_mode,tier,prompt_tokens,"
            "completion_tokens,total_tokens,estimated_cost_usd,event_type,success) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (gen_id("llm"), time.time(), model, provider, auth_mode, tier, prompt_tokens,
             completion_tokens, prompt_tokens + completion_tokens,
             self._est_cost(model, prompt_tokens, completion_tokens), event_type, int(success)))

    def log_llm_usage(self, model: str, provider: str, auth_mode: str, tier: str,
                      prompt_tokens: int, completion_tokens: int,
                      event_type: str = "", success: bool = True) -> None:
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        self._db.write_sync(
            "INSERT INTO llm_usage (id,timestamp,model,provider,auth_mode,tier,prompt_tokens,"
            "completion_tokens,total_tokens,estimated_cost_usd,event_type,success) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (gen_id("llm"), time.time(), model, provider, auth_mode, tier, prompt_tokens,
             completion_tokens, prompt_tokens + completion_tokens,
             self._est_cost(model, prompt_tokens, completion_tokens), event_type, int(success)))

    async def get_usage_history_async(self, limit: int = 100) -> list[dict]:
        return await self._db.fetch(
            "SELECT * FROM llm_usage ORDER BY timestamp DESC LIMIT %s", (limit,))

    def get_usage_history(self, limit: int = 100) -> list[dict]:
        return self._db.fetch_sync(
            "SELECT * FROM llm_usage ORDER BY timestamp DESC LIMIT %s", (limit,))

    async def get_usage_summary_async(self) -> dict:
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(total_tokens),0) AS tokens, "
            "COALESCE(SUM(estimated_cost_usd),0) AS cost FROM llm_usage")
        return dict(row) if row else {"calls": 0, "tokens": 0, "cost": 0}

    def get_usage_summary(self) -> dict:
        row = self._db.fetch_one_sync(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(total_tokens),0) AS tokens, "
            "COALESCE(SUM(estimated_cost_usd),0) AS cost FROM llm_usage")
        return dict(row) if row else {"calls": 0, "tokens": 0, "cost": 0}

    # ── Static knowledge (Tier 1) ──

    def _query_sk_sync(self, categories, keywords, scopes, limit) -> list[dict]:
        conds, params = [], []
        if categories:
            conds.append("category = ANY(%s)"); params.append(list(categories))
        if scopes:
            conds.append("scope = ANY(%s)"); params.append(list(scopes))
        if keywords:
            kw = []
            for k in keywords:
                kw.append("(key ILIKE %s OR value ILIKE %s)"); params += [f"%{k}%", f"%{k}%"]
            conds.append("(" + " OR ".join(kw) + ")")
        conds.append("is_deleted = 0")   # tombstones never surface to callers
        where = " AND ".join(conds)
        return self._db.fetch_sync(
            f"SELECT * FROM static_knowledge WHERE {where} ORDER BY importance DESC LIMIT %s",
            (*params, limit))

    def query_static_knowledge(self, categories=None, keywords=None, scopes=None, limit=20) -> list[dict]:
        return self._query_sk_sync(categories, keywords, scopes, limit)

    async def query_static_knowledge_async(self, categories=None, keywords=None, scopes=None, limit=20) -> list[dict]:
        import asyncio
        return await asyncio.to_thread(self._query_sk_sync, categories, keywords, scopes, limit)

    def _upsert_sk_sync(self, category, key, value, scope, node_id, source, importance, updated_at) -> None:
        # version bumps on every write (new row → 1, update → old+1) so a merge
        # can pick the winner deterministically without trusting wall clocks.
        # is_deleted=0 un-deletes a key that's re-added after a tombstone.
        self._db.write_sync(
            "INSERT INTO static_knowledge (category,key,value,source,importance,updated_at,scope,node_id,version,is_deleted) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,0) "
            "ON CONFLICT (category,key,scope) DO UPDATE SET value=EXCLUDED.value, "
            "importance=EXCLUDED.importance, updated_at=EXCLUDED.updated_at, source=EXCLUDED.source, "
            "version=static_knowledge.version+1, is_deleted=0",
            (category, key, value, source, importance, updated_at or time.time(), scope, node_id))

    def upsert_static_knowledge(self, category, key, value, scope="global", node_id=None,
                                source="user", importance=0.5, updated_at=None) -> None:
        self._upsert_sk_sync(category, key, value, scope, node_id, source, importance, updated_at)

    async def upsert_static_knowledge_async(self, category, key, value, scope="global", node_id=None,
                                            source="user", importance=0.5, updated_at=None) -> None:
        import asyncio
        await asyncio.to_thread(self._upsert_sk_sync, category, key, value, scope, node_id, source, importance, updated_at)

    def delete_static_knowledge(self, category, key, scope="global") -> bool:
        # Soft-delete (tombstone): bump version so the deletion beats prior edits
        # and survives a merge (a hard DELETE would let the row "resurrect" from
        # a replica that still has it). Queries already filter is_deleted=1.
        n = self._db.write_sync(
            "UPDATE static_knowledge SET is_deleted=1, version=version+1, updated_at=%s "
            "WHERE category=%s AND key=%s AND scope=%s AND is_deleted=0",
            (time.time(), category, key, scope))
        return n > 0

    async def delete_static_knowledge_async(self, category, key, scope="global") -> bool:
        import asyncio
        return await asyncio.to_thread(self.delete_static_knowledge, category, key, scope)

    # ── Environment catalog (filesystem scan cache) — real PG ──

    def _upsert_env_sync(self, entries: list[dict]) -> None:
        if not entries:
            return
        rows = [(e["id"], e["path"], e["type"], e.get("size_bytes", 0), e.get("modified_at", 0),
                 e.get("scanned_at", 0), e.get("scan_layer", 1), e.get("category", "other"),
                 e.get("extension", ""), e.get("parent_dir", ""), e.get("is_important", 0))
                for e in entries]
        self._db.write_many_sync(
            "INSERT INTO env_catalog (id,path,type,size_bytes,modified_at,scanned_at,scan_layer,"
            "category,extension,parent_dir,is_important) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (path) DO UPDATE SET type=EXCLUDED.type,size_bytes=EXCLUDED.size_bytes,"
            "modified_at=EXCLUDED.modified_at,scanned_at=EXCLUDED.scanned_at,scan_layer=EXCLUDED.scan_layer,"
            "category=EXCLUDED.category,extension=EXCLUDED.extension,parent_dir=EXCLUDED.parent_dir,"
            "is_important=EXCLUDED.is_important,is_deleted=0", rows)

    def upsert_env_catalog_batch(self, entries): self._upsert_env_sync(entries)

    async def upsert_env_catalog_batch_async(self, entries):
        import asyncio; await asyncio.to_thread(self._upsert_env_sync, entries)

    def _scan_progress_upsert(self, layer_id: str, status: str, **kwargs) -> None:
        now = time.time()
        cols = ["status", "updated_at"] + list(kwargs.keys())
        vals = [status, now] + list(kwargs.values())
        if status == "completed":
            cols.append("completed_at"); vals.append(now)
        insert_cols = ["id", "started_at"] + cols
        insert_vals = [layer_id, now] + vals
        ph = ",".join(["%s"] * len(insert_cols))
        updates = ",".join(f"{c}=EXCLUDED.{c}" for c in cols)
        self._db.write_sync(
            f"INSERT INTO env_scan_progress ({','.join(insert_cols)}) VALUES ({ph}) "
            f"ON CONFLICT (id) DO UPDATE SET {updates}", tuple(insert_vals))

    def update_scan_progress(self, layer_id, status, **kwargs):
        self._scan_progress_upsert(layer_id, status, **kwargs)

    async def update_scan_progress_async(self, layer_id, status, **kwargs):
        import asyncio; await asyncio.to_thread(lambda: self._scan_progress_upsert(layer_id, status, **kwargs))

    def get_scan_progress(self, layer_id):
        return self._db.fetch_one_sync("SELECT * FROM env_scan_progress WHERE id=%s", (layer_id,))

    async def get_scan_progress_async(self, layer_id):
        import asyncio; return await asyncio.to_thread(self.get_scan_progress, layer_id)

    def _search_env_sync(self, query, category, extension, file_type, limit):
        conds, params = ["is_deleted=0"], []
        if query:
            conds.append("(path ILIKE %s OR summary ILIKE %s)"); params += [f"%{query}%", f"%{query}%"]
        if category:
            conds.append("category=%s"); params.append(category)
        if extension:
            conds.append("extension=%s"); params.append(extension)
        if file_type:
            conds.append("type=%s"); params.append(file_type)
        where = " AND ".join(conds)
        return self._db.fetch_sync(
            f"SELECT path,type,size_bytes,category,extension,summary,is_important,scan_layer,modified_at "
            f"FROM env_catalog WHERE {where} ORDER BY is_important DESC, modified_at DESC LIMIT %s",
            (*params, limit))

    def search_env_catalog(self, query="", category="", extension="", file_type="", limit=20):
        return self._search_env_sync(query, category, extension, file_type, limit)

    async def search_env_catalog_async(self, query="", category="", extension="", file_type="", limit=20):
        import asyncio
        return await asyncio.to_thread(self._search_env_sync, query, category, extension, file_type, limit)

    def _env_stats_sync(self):
        def n(sql): return self._db.fetch_one_sync(sql)["n"]
        by_cat = {r["category"]: r["c"] for r in self._db.fetch_sync(
            "SELECT category, COUNT(*) AS c FROM env_catalog WHERE is_deleted=0 GROUP BY category")}
        prog = {}
        for lid in ("layer1", "layer2", "layer3"):
            p = self.get_scan_progress(lid); prog[lid] = p if p else {"status": "pending"}
        return {
            "total_files": n("SELECT COUNT(*) AS n FROM env_catalog WHERE type='file' AND is_deleted=0"),
            "total_dirs": n("SELECT COUNT(*) AS n FROM env_catalog WHERE type='directory' AND is_deleted=0"),
            "important_files": n("SELECT COUNT(*) AS n FROM env_catalog WHERE is_important=1 AND is_deleted=0"),
            "summarized": n("SELECT COUNT(*) AS n FROM env_catalog WHERE summary<>'' AND is_deleted=0"),
            "by_category": by_cat, "scan_progress": prog,
        }

    def get_env_stats(self): return self._env_stats_sync()

    async def get_env_stats_async(self):
        import asyncio; return await asyncio.to_thread(self._env_stats_sync)

    def _scanned_dirs_sync(self, max_layer):
        return self._db.fetch_sync(
            "SELECT path, scan_layer FROM env_catalog WHERE type='directory' "
            "AND scan_layer <= %s AND is_deleted=0", (max_layer,))

    def get_scanned_dirs(self, max_layer=2): return self._scanned_dirs_sync(max_layer)

    async def get_scanned_dirs_async(self, max_layer=2):
        import asyncio; return await asyncio.to_thread(self._scanned_dirs_sync, max_layer)

    def _files_in_dir_sync(self, dir_path):
        return self._db.fetch_sync(
            "SELECT path, modified_at, size_bytes FROM env_catalog WHERE parent_dir=%s AND is_deleted=0",
            (dir_path.replace("\\", "/"),))

    def get_env_files_in_dir(self, dir_path): return self._files_in_dir_sync(dir_path)

    async def get_env_files_in_dir_async(self, dir_path):
        import asyncio; return await asyncio.to_thread(self._files_in_dir_sync, dir_path)

    def _unscanned_dirs_sync(self):
        rows = self._db.fetch_sync(
            "SELECT path FROM env_catalog WHERE type='directory' AND scan_layer=1 AND is_deleted=0 "
            "AND path NOT IN (SELECT DISTINCT parent_dir FROM env_catalog WHERE scan_layer >= 2)")
        return [r["path"] for r in rows]

    def get_unscanned_dirs(self): return self._unscanned_dirs_sync()

    async def get_unscanned_dirs_async(self):
        import asyncio; return await asyncio.to_thread(self._unscanned_dirs_sync)

    def _mark_env_deleted_sync(self, path):
        self._db.write_sync("UPDATE env_catalog SET is_deleted=1 WHERE path=%s", (path.replace("\\", "/"),))

    def mark_env_deleted(self, path): self._mark_env_deleted_sync(path)

    async def mark_env_deleted_async(self, path):
        import asyncio; await asyncio.to_thread(self._mark_env_deleted_sync, path)

    def _update_env_entry_sync(self, path, updates):
        if not updates:
            return
        sets = ", ".join(f"{k}=%s" for k in updates)  # keys are scanner-controlled column names
        self._db.write_sync(f"UPDATE env_catalog SET {sets} WHERE path=%s",
                            (*updates.values(), path.replace("\\", "/")))

    def update_env_entry(self, path, updates): self._update_env_entry_sync(path, updates)

    async def update_env_entry_async(self, path, updates):
        import asyncio; await asyncio.to_thread(self._update_env_entry_sync, path, updates)

    def _unsummarized_sync(self, limit):
        return self._db.fetch_sync(
            "SELECT path, category, extension FROM env_catalog WHERE is_important=1 "
            "AND summary='' AND is_deleted=0 ORDER BY modified_at DESC LIMIT %s", (limit,))

    def get_unsummarized_important_files(self, limit=10): return self._unsummarized_sync(limit)

    async def get_unsummarized_important_files_async(self, limit=10):
        import asyncio; return await asyncio.to_thread(self._unsummarized_sync, limit)
    # ── Memory decay / consolidation / archive (real PG; soul maintenance) ──

    def _below_threshold_sync(self, threshold: float) -> list[dict]:
        rows = self._db.fetch_sync(
            "SELECT * FROM episodic_memories WHERE decay_score IS NOT NULL "
            "AND decay_score < %s AND (metadata_json->>'consolidated') IS DISTINCT FROM 'true' "
            "ORDER BY decay_score ASC LIMIT 200", (threshold,))
        return [self._normalize(r) for r in rows]

    def get_memories_below_threshold(self, threshold: float) -> list[dict]:
        return self._below_threshold_sync(threshold)

    async def get_memories_below_threshold_async(self, threshold: float) -> list[dict]:
        import asyncio
        return await asyncio.to_thread(self._below_threshold_sync, threshold)

    def _unconsolidated_sync(self, limit: int) -> list[dict]:
        return self._db.fetch_sync(
            "SELECT id,type,importance,created_at,last_accessed,access_count,decay_score "
            "FROM episodic_memories WHERE (metadata_json->>'consolidated') IS DISTINCT FROM 'true' "
            "ORDER BY created_at DESC LIMIT %s", (limit,))

    def get_unconsolidated_memories(self, limit: int = 500) -> list[dict]:
        return self._unconsolidated_sync(limit)

    async def get_unconsolidated_memories_async(self, limit: int = 500) -> list[dict]:
        import asyncio
        return await asyncio.to_thread(self._unconsolidated_sync, limit)

    def _salient_unconsolidated_sync(self, min_importance: float, limit: int) -> list[dict]:
        # Salient (importance encodes salience-at-save = base * emotion multiplier),
        # not-yet-promoted, non-archive rows — candidates to promote to cloud long-term.
        rows = self._db.fetch_sync(
            "SELECT * FROM episodic_memories WHERE importance >= %s "
            "AND (metadata_json->>'consolidated') IS DISTINCT FROM 'true' "
            "AND type <> 'archive' ORDER BY created_at ASC LIMIT %s",
            (min_importance, limit))
        return [self._normalize(r) for r in rows]

    def get_salient_unconsolidated(self, min_importance: float = 0.6, limit: int = 200) -> list[dict]:
        return self._salient_unconsolidated_sync(min_importance, limit)

    async def get_salient_unconsolidated_async(self, min_importance: float = 0.6, limit: int = 200) -> list[dict]:
        import asyncio
        return await asyncio.to_thread(self._salient_unconsolidated_sync, min_importance, limit)

    def batch_update_decay_scores(self, updates: list[tuple[str, float]]) -> None:
        if not updates:
            return
        self._db.write_many_sync(
            "UPDATE episodic_memories SET decay_score=%s WHERE id=%s",
            [(score, mid) for mid, score in updates])

    async def batch_update_decay_scores_async(self, updates: list[tuple[str, float]]) -> None:
        import asyncio
        await asyncio.to_thread(self.batch_update_decay_scores, updates)

    def mark_consolidated(self, ids: list[str]) -> None:
        if not ids:
            return
        # Set a metadata flag rather than rewriting content_hash — content_hash is
        # the stable G-Set dedup key for cross-node sync (DISTRIBUTED_DESIGN §3);
        # rewriting it would break dedup. The retriever already reads this flag.
        self._db.write_sync(
            "UPDATE episodic_memories SET metadata_json = "
            "jsonb_set(COALESCE(metadata_json, '{}'::jsonb), '{consolidated}', 'true') "
            "WHERE id = ANY(%s)", (list(ids),))

    async def mark_consolidated_async(self, ids: list[str]) -> None:
        import asyncio
        await asyncio.to_thread(self.mark_consolidated, ids)

    async def archive_to_knowledge_async(self, summary: str, source_ids: list[str],
                                         metadata: dict | None = None) -> str:
        meta = dict(metadata or {}); meta["source_ids"] = source_ids; meta["archived"] = True
        return await self.save_memory_async(summary, "archive", importance=0.6, metadata=meta)

    def archive_to_knowledge(self, summary: str, source_ids: list[str],
                             metadata: dict | None = None) -> str:
        meta = dict(metadata or {}); meta["source_ids"] = source_ids; meta["archived"] = True
        return self.save_memory(summary, "archive", importance=0.6, metadata=meta)
