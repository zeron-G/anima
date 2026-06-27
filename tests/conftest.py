"""Shared test fixtures."""

import asyncio
import asyncio.runners as _runners
import os
import sys

import pytest

from anima.models.event import Event, EventType, EventPriority
from anima.models.perception_frame import DiffRule


# ── Fix: Windows Python 3.14 event loop teardown hang ──
# Two-layer fix for asyncio cleanup hangs on Windows:
# 1. Patch _cancel_all_tasks with a hard timeout (handles per-test teardown)
# 2. Force os._exit at session end (catches any remaining hangs)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    _orig_cancel = _runners._cancel_all_tasks

    def _cancel_no_wait(loop):
        """Cancel tasks without waiting — brute-force fix for Windows hang."""
        try:
            for task in asyncio.all_tasks(loop):
                task.cancel()
        except Exception:
            pass

    _runners._cancel_all_tasks = _cancel_no_wait

    # Also patch Runner.close to skip shutdown_default_executor (hangs on
    # thread pool workers). Just close the loop directly.
    _orig_runner_close = asyncio.Runner.close

    def _runner_close_fast(self):
        try:
            _cancel_no_wait(self._loop)
            self._loop.close()
        except Exception:
            pass

    asyncio.Runner.close = _runner_close_fast


_exit_code = 0


def pytest_sessionfinish(session, exitstatus):
    global _exit_code
    _exit_code = exitstatus


def pytest_unconfigure(config):
    """Force-exit on Windows AFTER terminal summary is printed."""
    if sys.platform == "win32":
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(_exit_code)


# ── Postgres test backend (E3) ──
# Tests run against the REAL Postgres store (local Docker PG, database
# 'anima_test'), so they exercise production code — not a different SQLite
# dialect. Embeddings are mocked to None (no OpenAI): the PG store then stores
# no vectors and semantic search falls back to deterministic ILIKE — offline,
# free, and stable for assertions.

import anima.config  # noqa: E402  — loads .env (LOCAL_DATABASE_URL) for the fixtures

sys.path.insert(0, os.path.dirname(__file__))  # make sibling pgutil importable
from pgutil import _PG_TABLES, ensure_test_db, test_dsn  # noqa: E402


@pytest.fixture(autouse=True)
def _mock_embeddings(monkeypatch):
    """No OpenAI in tests. embed→None makes the PG store skip vectors and the
    semantic search fall back to deterministic ILIKE (offline, free, stable)."""
    from anima.memory import embedder

    async def _none(_text):
        return None

    async def _none_batch(texts):
        return [None] * len(texts)

    monkeypatch.setattr(embedder, "embed_openai", _none)
    monkeypatch.setattr(embedder, "embed_openai_batch", _none_batch)


@pytest.fixture
async def pg_store():
    """A PgMemoryStore on a freshly-truncated local Postgres test DB."""
    dsn = test_dsn()
    if not dsn or not ensure_test_db():
        pytest.skip("local Postgres (LOCAL_DATABASE_URL) unavailable")
    from anima.memory.pg_store import PgMemoryStore
    store = await PgMemoryStore.create(dsn=dsn)
    store._db.write_sync(f"TRUNCATE {_PG_TABLES}")
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
def sample_event():
    return Event(
        type=EventType.USER_MESSAGE,
        payload={"text": "hello"},
        priority=EventPriority.HIGH,
    )


@pytest.fixture
def default_diff_rules():
    return [
        DiffRule(field="cpu_percent", threshold=15),
        DiffRule(field="memory_percent", threshold=10),
        DiffRule(field="disk_percent", threshold=5),
        DiffRule(field="file_changes", threshold=0),
        DiffRule(field="process_count", threshold=5),
    ]
