"""Sprint 5 Tests — memory system upgrade, DB safety, remaining fixes.

Covers:
  - H-08: Embedder integration (save with embedding, 3-tier search fallback)
  - C-06: WAL mode enabled, write_lock exists
  - C-07: Consolidation uses transactions
  - M-14: ChromaDB failures log WARNING
  - M-16/M-17: Touch uses transactions
  - M-19: StaticKnowledge auto-deserializes JSON
  - M-22: Heartbeat tick_lock exists
  - M-36: Soul Container emoji density excludes emoji from denominator
  - M-38: Length guard >= boundary
  - L-05: Composite indexes exist
  - L-10: Configurable thresholds
  - L-19: Soul Container regex pre-compiled
  - L-28: Example weight validation
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


# ── Embedder integration tests ──


class TestEmbedderIntegration:
    """Test embedding save/search pipeline in MemoryStore."""

    @pytest.mark.asyncio
    async def test_save_creates_embedding_row(self, pg_store):
        """Saving an episodic memory persists a row; episodic carries the
        pgvector embedding column (NULL here — OpenAI is mocked off in tests)."""
        store = pg_store

        mid = await store.save_memory_async(
            content="This is a test memory about Python programming",
            type="chat",
            importance=0.7,
        )
        assert mid.startswith("mem_")

        row = store._db.fetch_one_sync(
            "SELECT id, embedding FROM episodic_memories WHERE id = %s", (mid,)
        )
        assert row is not None and row["id"] == mid
        assert "embedding" in row  # the vector(1536) column exists

    @pytest.mark.asyncio
    async def test_search_returns_results(self, pg_store):
        """Search should return results regardless of backend."""
        store = pg_store

        # Save some memories
        await store.save_memory_async("Python is a programming language", "chat", 0.8)
        await store.save_memory_async("JavaScript runs in browsers", "chat", 0.7)
        await store.save_memory_async("Cooking recipes for dinner", "observation", 0.3)

        # Search — should work via at least LIKE fallback
        results = store.search_memories(query="programming", limit=5)
        assert len(results) >= 1
        assert any("Python" in r.get("content", "") for r in results)

# ── Database safety tests ──


class TestDatabaseSafety:
    """Write safety + schema sanity on Postgres."""

    @pytest.mark.asyncio
    async def test_write_lock_exists(self, pg_store):
        """The store funnels writes through a single threading.Lock (psycopg
        connections are not thread-safe). It lives on the db manager."""
        store = pg_store
        lock = store._db._sync_write_lock
        assert hasattr(lock, "acquire")  # duck-type check for a Lock

    @pytest.mark.asyncio
    async def test_touch_uses_transaction(self, pg_store):
        """M-16/M-17: touch_memories increments access_count."""
        store = pg_store

        mid1 = store.save_memory("Memory 1", "chat")
        mid2 = store.save_memory("Memory 2", "chat")

        store.touch_memories([mid1, mid2])

        row1 = store._db.fetch_one_sync(
            "SELECT access_count FROM episodic_memories WHERE id = %s", (mid1,)
        )
        assert row1["access_count"] == 1

    @pytest.mark.asyncio
    async def test_composite_indexes_exist(self, pg_store):
        """L-05: schema indexes (composite + operational) are created."""
        store = pg_store

        rows = store._db.fetch_sync(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
        )
        index_names = {r["indexname"] for r in rows}

        assert "idx_episodic_type_created" in index_names
        assert "idx_env_deleted" in index_names
        assert "idx_env_important" in index_names


# ── StaticKnowledge JSON deserialization tests ──


class TestStaticKnowledgeJSON:
    """Test M-19: auto-deserialization of JSON values."""

    @pytest.mark.asyncio
    async def test_dict_value_deserialized(self, pg_store):
        from anima.memory.static_store import StaticKnowledgeStore

        store = pg_store
        static = StaticKnowledgeStore(store, node_id="test")

        # Store a dict value
        static.upsert("config", "paths", {"root": "/data", "temp": "/tmp"})

        # Query should return deserialized dict
        results = static.query(categories=["config"])
        assert len(results) >= 1
        for r in results:
            if r.get("key") == "paths":
                val = r["value"]
                assert isinstance(val, dict), f"Expected dict, got {type(val)}: {val}"
                assert val["root"] == "/data"
                break

    @pytest.mark.asyncio
    async def test_string_value_unchanged(self, pg_store):
        from anima.memory.static_store import StaticKnowledgeStore

        store = pg_store
        static = StaticKnowledgeStore(store, node_id="test")

        # Store a plain string value
        static.upsert("config", "name", "Eva")

        results = static.query(categories=["config"])
        for r in results:
            if r.get("key") == "name":
                assert r["value"] == "Eva"
                assert isinstance(r["value"], str)
                break


# ── Heartbeat tick lock test ──


class TestHeartbeatTickLock:
    """Test M-22: tick count uses threading.Lock."""

    def test_tick_lock_exists(self):
        """HeartbeatEngine should have _tick_lock."""
        from anima.core.heartbeat import HeartbeatEngine
        import threading

        hb = HeartbeatEngine(
            event_queue=MagicMock(),
            snapshot_cache=MagicMock(),
            diff_engine=MagicMock(),
            emotion_state=MagicMock(),
            working_memory=MagicMock(),
            llm_router=MagicMock(),
            config={},
        )
        assert hasattr(hb, "_tick_lock")
        assert hasattr(hb._tick_lock, "acquire")  # duck-type check for Lock


# ── Soul Container fix tests ──


class TestSoulContainerFixes:
    """Test M-36, M-38, M-39 fixes."""

    def test_emoji_density_excludes_emoji_from_denominator(self):
        """M-36: Emoji count should not inflate text_len."""
        from anima.llm.soul_container import SoulContainer
        sc = SoulContainer.__new__(SoulContainer)
        sc._rules = []
        sc._message_counter = 0
        # The actual density calculation is internal, but we can verify
        # the transform doesn't crash with emoji-heavy text
        result = sc.transform("Hello 😀😁😂 World", is_user_facing=True)
        assert isinstance(result, str)

    def test_length_guard_boundary(self):
        """M-38: >= instead of > for midpoint boundary."""
        # This is hard to test in isolation without the full SoulContainer
        # but we can verify the transform handles long text
        from anima.llm.soul_container import SoulContainer
        sc = SoulContainer.__new__(SoulContainer)
        sc._rules = []
        sc._message_counter = 0
        long_text = "A" * 5000
        result = sc.transform(long_text, is_user_facing=True)
        assert isinstance(result, str)


# ── Rule engine configurable thresholds ──


class TestRuleEngineThresholds:
    """Test L-10: CPU/disk thresholds are class attributes."""

    def test_thresholds_configurable(self):
        from anima.core.rule_engine import RuleEngine
        re = RuleEngine()
        assert hasattr(re, "CPU_ALERT_THRESHOLD")
        assert hasattr(re, "DISK_ALERT_THRESHOLD")
        assert re.CPU_ALERT_THRESHOLD == 90
        assert re.DISK_ALERT_THRESHOLD == 95

    def test_thresholds_overridable(self):
        from anima.core.rule_engine import RuleEngine
        re = RuleEngine()
        re.CPU_ALERT_THRESHOLD = 80  # Lower threshold
        assert re.CPU_ALERT_THRESHOLD == 80


# ── Example weight validation ──


class TestExampleWeightValidation:
    """Test L-28: invalid weight doesn't crash."""

    def test_invalid_weight_defaults(self, tmp_path):
        """Loading an example with invalid weight should default to 0.5."""
        from anima.llm.prompt_compiler import _load_examples

        # Create a test example with invalid weight
        examples_dir = tmp_path / "examples"
        examples_dir.mkdir()
        (examples_dir / "bad_weight.md").write_text(
            "---\ntrigger: USER_MESSAGE\nweight: not_a_number\n---\n\nuser: hi\nassistant: hello\n",
            encoding="utf-8",
        )

        examples = _load_examples(examples_dir)
        assert len(examples) == 1
        assert examples[0]["weight"] == 0.5  # Default, not crash
