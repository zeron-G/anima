"""Offline smoke for PgMemoryStore (no network). Full roundtrip is verified
separately against real Neon."""

from __future__ import annotations

import json

from anima.memory.pg_store import PgMemoryStore, _vec_literal


def test_vec_literal():
    assert _vec_literal([0.1, 0.2, 0.3]) == "[0.1,0.2,0.3]"


def test_normalize_jsonb_rows_to_strings():
    r = PgMemoryStore._normalize(
        {"id": "x", "content": "hi", "metadata_json": {"role": "user"},
         "tags_json": ["a", "b"], "embedding": [1.0, 2.0]})
    # JSONB dict/list → JSON string (matches the old SQLite row contract)
    assert isinstance(r["metadata_json"], str)
    assert json.loads(r["metadata_json"])["role"] == "user"
    assert isinstance(r["tags_json"], str)
    # raw embedding is dropped from returned rows
    assert "embedding" not in r


def test_content_hash_stable_and_16():
    h1 = PgMemoryStore._content_hash("hello", "chat")
    h2 = PgMemoryStore._content_hash("hello", "chat")
    assert h1 == h2 and len(h1) == 16


def test_core_api_surface_present():
    for m in [
        "create", "close", "save_memory_async", "save_memory",
        "get_session_conversation", "get_session_conversation_async",
        "get_recent_memories", "get_recent_memories_async",
        "search_memories_async", "touch_memories_async",
        "log_emotion_async", "get_latest_emotion",
        "audit_async", "save_snapshot_async",
        "log_llm_usage_async", "get_usage_history_async", "get_usage_summary_async",
        "query_static_knowledge_async", "upsert_static_knowledge_async",
        "delete_static_knowledge_async",
    ]:
        assert hasattr(PgMemoryStore, m), f"missing {m}"
