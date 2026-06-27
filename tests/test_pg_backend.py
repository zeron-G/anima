"""Offline smoke for the Postgres backend foundation (no network).

End-to-end PG + pgvector + OpenAI is proven separately against real Neon; these
guard the wiring without requiring network/secrets.
"""

from __future__ import annotations

import pytest

from anima.memory.pg_db import PgDatabaseManager
from anima.memory import embedder


def test_pg_manager_constructs_without_connecting():
    db = PgDatabaseManager(dsn="postgresql://example/db", local_dsn="")
    assert db.is_open is False  # not open until init()
    assert db.using_local is False


def test_pg_manager_use_before_init_raises():
    db = PgDatabaseManager(dsn="postgresql://example/db")
    with pytest.raises(RuntimeError):
        db._check_open()


def test_openai_embed_surface():
    assert embedder.OPENAI_EMBED_MODEL == "text-embedding-3-small"
    assert embedder.OPENAI_EMBED_DIM == 1536
    assert callable(embedder.embed_openai)
    assert callable(embedder.embed_openai_batch)


@pytest.mark.asyncio
async def test_embed_openai_degrades_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from anima import secret_store
    monkeypatch.setattr(secret_store, "_provider", secret_store.EnvSecretProvider())
    # No key → returns None (no network call), never raises.
    assert await embedder.embed_openai("hello") is None
    assert await embedder.embed_openai_batch(["a", "b"]) == [None, None]
