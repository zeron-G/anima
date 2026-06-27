"""One-time soul migration: local SQLite → Neon Postgres + pgvector (D4).

Moves Eva's memories upward: episodic_memories, emotion_log, static_knowledge.
Vectorizable memories are RE-embedded via OpenAI text-embedding-3-small (1536-d),
since the old ChromaDB embeddings used a different model/dimension.

Safe:
  * READ-ONLY on the local SQLite (never mutates it).
  * Idempotent / resumable — existing rows are skipped (ON CONFLICT DO NOTHING),
    so you can run it repeatedly or after an interrupted run.

Operational tables (audit_log, state_snapshots, llm_usage) are NOT migrated —
they're history, not soul, and start fresh on Postgres.

Usage:  python scripts/migrate_soul_to_postgres.py
Requires DATABASE_URL + OPENAI_API_KEY in .env.
"""
from __future__ import annotations

import asyncio
import sqlite3

import anima.config  # loads .env (DATABASE_URL, OPENAI_API_KEY)  # noqa: F401
from anima.config import db_path
from anima.memory import embedder
from anima.memory.pg_db import PgDatabaseManager
from anima.memory.pg_store import _VECTORIZE, _vec_literal

EMBED_BATCH = 128

# Soul-worth episodic only. The bulk of episodic is low-value 'observation' rows
# from the env scanner — node-specific filesystem noise that's irrelevant on a
# new (cloud) node and would just bloat Neon. Keep conversations, decisions,
# self-thoughts, archives, and anything the importance scorer flagged.
_SOUL_FILTER = ("type IN ('chat','decision','self_thought','thought','archive',"
                "'reflection','insight','note') OR importance >= 0.5")

_COLS = ("id,type,content,importance,access_count,created_at,last_accessed,"
         "metadata_json,tags_json,sync_seq,content_hash,decay_score,session_id")


def _jsonb(val: str | None, fallback: str) -> str:
    s = (val or "").strip()
    return s if s else fallback


async def _migrate_episodic(src: sqlite3.Connection, db: PgDatabaseManager) -> None:
    rows = [dict(r) for r in src.execute(
        f"SELECT * FROM episodic_memories WHERE {_SOUL_FILTER} ORDER BY created_at").fetchall()]
    print(f"soul-worth episodic in SQLite: {len(rows)} (env-scan observations skipped)")
    existing = {r["id"] for r in await db.fetch("SELECT id FROM episodic_memories")}
    todo = [r for r in rows if r.get("id") not in existing]
    print(f"  to migrate: {len(todo)}", flush=True)
    if not todo:
        return

    # Embed every migrated memory with content (they're all meaningful now).
    embs: list[list[float] | None] = [None] * len(todo)
    to_embed = [(i, (r.get("content") or "").strip()) for i, r in enumerate(todo)
                if (r.get("content") or "").strip()]
    for s in range(0, len(to_embed), EMBED_BATCH):
        chunk = to_embed[s:s + EMBED_BATCH]
        vecs = await embedder.embed_openai_batch([c for _, c in chunk])
        for k, (i, _) in enumerate(chunk):
            embs[i] = vecs[k]
        print(f"  embedded {min(s + EMBED_BATCH, len(to_embed))}/{len(to_embed)}", flush=True)

    # Split with/without embedding, then BATCH insert (psycopg executemany — one
    # round-trip class instead of thousands of single inserts over the network).
    with_emb, without_emb = [], []
    for i, r in enumerate(todo):
        base = (r.get("id"), r.get("type"), r.get("content"), r.get("importance"),
                r.get("access_count") or 0, r.get("created_at"), r.get("last_accessed"),
                _jsonb(r.get("metadata_json"), "{}"), _jsonb(r.get("tags_json"), "[]"),
                r.get("sync_seq") or 0, r.get("content_hash") or "", r.get("decay_score"),
                r.get("session_id") or "local")
        if embs[i]:
            with_emb.append(base + (_vec_literal(embs[i]),))
        else:
            without_emb.append(base)
    if with_emb:
        db.write_many_sync(
            f"INSERT INTO episodic_memories ({_COLS},embedding) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s::vector) "
            "ON CONFLICT (id) DO NOTHING", with_emb)
    if without_emb:
        db.write_many_sync(
            f"INSERT INTO episodic_memories ({_COLS}) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s) "
            "ON CONFLICT (id) DO NOTHING", without_emb)
    print(f"  episodic migrated: {len(todo)} (embedded={len(with_emb)}, no-content={len(without_emb)})", flush=True)


async def _migrate_emotion(src: sqlite3.Connection, db: PgDatabaseManager) -> None:
    try:
        rows = [dict(r) for r in src.execute("SELECT * FROM emotion_log").fetchall()]
    except sqlite3.OperationalError:
        return
    existing = {r["id"] for r in await db.fetch("SELECT id FROM emotion_log")}
    n = 0
    for r in rows:
        if r.get("id") in existing:
            continue
        db.write_sync(
            "INSERT INTO emotion_log (id,engagement,confidence,curiosity,concern,trigger,timestamp) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
            (r.get("id"), r.get("engagement"), r.get("confidence"), r.get("curiosity"),
             r.get("concern"), r.get("trigger"), r.get("timestamp")))
        n += 1
    print(f"emotion_log migrated: {n} (of {len(rows)})")


async def _migrate_static(src: sqlite3.Connection, db: PgDatabaseManager) -> None:
    try:
        rows = [dict(r) for r in src.execute("SELECT * FROM static_knowledge").fetchall()]
    except sqlite3.OperationalError:
        return
    n = 0
    for r in rows:
        db.write_sync(
            "INSERT INTO static_knowledge (category,key,value,source,importance,updated_at,scope,node_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (category,key,scope) DO NOTHING",
            (r.get("category"), r.get("key"), r.get("value"), r.get("source") or "user",
             r.get("importance") or 0.5, r.get("updated_at"), r.get("scope") or "global",
             r.get("node_id")))
        n += 1
    print(f"static_knowledge upserted: {n}")


async def main() -> None:
    sqlite_path = str(db_path())
    print(f"source SQLite: {sqlite_path}")
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    db = PgDatabaseManager()
    await db.init()  # idempotent schema
    print(f"target Postgres: {'LOCAL fallback' if db.using_local else 'Neon primary'}\n")

    await _migrate_episodic(src, db)
    await _migrate_emotion(src, db)
    await _migrate_static(src, db)

    # Verify
    total = (await db.fetch_one("SELECT COUNT(*) AS n FROM episodic_memories"))["n"]
    vec = (await db.fetch_one("SELECT COUNT(*) AS n FROM episodic_memories WHERE embedding IS NOT NULL"))["n"]
    print(f"\nNeon now holds {total} episodic memories ({vec} with embeddings).")
    await db.close()
    src.close()
    print("Soul migration complete.")


if __name__ == "__main__":
    asyncio.run(main())
