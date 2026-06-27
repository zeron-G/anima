-- ANIMA Postgres schema (Neon / local fallback). pgvector for semantic recall.
-- Embeddings: OpenAI text-embedding-3-small → vector(1536).
-- Only episodic_memories carries embeddings; emotion is a numeric time-series;
-- persona prose lives in soul_documents as text (no vectors). Postgres-only
-- (no SQLite dual-dialect); see anima/memory for the backend.

CREATE EXTENSION IF NOT EXISTS vector;

-- ── Episodic memory (the conversation/event log; semantically searchable) ──
CREATE TABLE IF NOT EXISTS episodic_memories (
    id            TEXT PRIMARY KEY,
    type          TEXT,
    content       TEXT,
    importance    REAL,
    access_count  INTEGER DEFAULT 0,
    created_at    DOUBLE PRECISION,
    last_accessed DOUBLE PRECISION,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    tags_json     JSONB DEFAULT '[]'::jsonb,
    sync_seq      BIGINT DEFAULT 0,
    content_hash  TEXT DEFAULT '',
    decay_score   REAL,
    session_id    TEXT DEFAULT 'local',
    embedding     vector(1536)
);
CREATE INDEX IF NOT EXISTS idx_episodic_type        ON episodic_memories(type);
CREATE INDEX IF NOT EXISTS idx_episodic_created     ON episodic_memories(created_at);
CREATE INDEX IF NOT EXISTS idx_episodic_session     ON episodic_memories(session_id);
CREATE INDEX IF NOT EXISTS idx_episodic_type_created ON episodic_memories(type, created_at);
-- HNSW cosine index for semantic recall (pgvector 0.8+)
CREATE INDEX IF NOT EXISTS idx_episodic_embedding
    ON episodic_memories USING hnsw (embedding vector_cosine_ops);

-- ── Emotion: numeric time-series, queried by recency. NO vectors. ──
CREATE TABLE IF NOT EXISTS emotion_log (
    id         TEXT PRIMARY KEY,
    engagement REAL, confidence REAL, curiosity REAL, concern REAL,
    trigger    TEXT,
    timestamp  DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_emotion_ts ON emotion_log(timestamp);

-- NOTE: persona prose (identity/*.md), feelings.md, growth_log.md, lorebook and
-- golden_replies stay as FILES, not DB rows — they're human-authored and edited
-- (vim / Soulscape UI / SSH); a DB would turn "edit a file" into "write SQL".
-- Durability for them = the backup routine (cloud snapshot + local-on-startup),
-- not this schema. Only program-written, queried, or semantically-searched data
-- lives here.

-- ── Tier-1 static knowledge ──
CREATE TABLE IF NOT EXISTS static_knowledge (
    id         SERIAL PRIMARY KEY,
    category   TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    source     TEXT DEFAULT 'user',
    importance REAL DEFAULT 0.5,
    updated_at DOUBLE PRECISION NOT NULL,
    scope      TEXT NOT NULL DEFAULT 'global',
    node_id    TEXT,
    UNIQUE(category, key, scope)
);
CREATE INDEX IF NOT EXISTS idx_sk_scope ON static_knowledge(scope);

-- ── Operational ──
CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY, action TEXT, details TEXT, timestamp DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS state_snapshots (
    id TEXT PRIMARY KEY, state_json JSONB, timestamp DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id TEXT PRIMARY KEY, timestamp DOUBLE PRECISION, model TEXT, provider TEXT,
    auth_mode TEXT, tier TEXT, prompt_tokens INTEGER, completion_tokens INTEGER,
    total_tokens INTEGER, estimated_cost_usd REAL, event_type TEXT, success INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_ts    ON llm_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_llm_usage_model ON llm_usage(model);
