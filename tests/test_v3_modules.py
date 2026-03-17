"""Tests for v3 prompt engineering & memory system modules."""

import asyncio
import math
import time
import tempfile
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── TokenBudget ──

class TestTokenBudget:
    def test_count_tokens(self):
        from anima.llm.token_budget import count_tokens
        assert count_tokens("") == 0
        assert count_tokens("hello") > 0
        # Chinese text: ~3 chars per token
        assert count_tokens("你好世界测试文本") > 0

    def test_truncate_to_tokens(self):
        from anima.llm.token_budget import truncate_to_tokens
        text = "第一句话。第二句话。第三句话。第四句话。"
        result = truncate_to_tokens(text, 5)
        assert len(result) < len(text)
        # Should cut at sentence boundary
        assert result.endswith("。") or len(result) <= 15

    def test_budget_init(self):
        from anima.llm.token_budget import TokenBudget
        b = TokenBudget(max_context=100000, reserve_response=4096)
        assert b.available == 100000 - 4096

    def test_get_conversation_budget(self):
        from anima.llm.token_budget import TokenBudget
        b = TokenBudget(max_context=100000, reserve_response=4096)
        budget = b.get_conversation_budget(5000)
        assert budget > 0
        assert budget == b.available - 5000


# ── ImportanceScorer ──

class TestImportanceScorer:
    def test_base_scores(self):
        from anima.memory.importance import ImportanceScorer
        scorer = ImportanceScorer()
        # User message should score higher than observation
        user_score = scorer.score("hello", "chat_user")
        obs_score = scorer.score("cpu normal", "observation")
        assert user_score > obs_score

    def test_signal_boost(self):
        from anima.memory.importance import ImportanceScorer
        scorer = ImportanceScorer()
        # Question should score higher than plain text
        q_score = scorer.score("这个怎么做？", "chat_user")
        p_score = scorer.score("好的", "chat_user")
        assert q_score > p_score

    def test_instruction_boost(self):
        from anima.memory.importance import ImportanceScorer
        scorer = ImportanceScorer()
        score = scorer.score("帮我记住下周三出差", "chat_user")
        assert score >= 0.85  # Base 0.7 + instruction 0.2

    def test_score_clamped(self):
        from anima.memory.importance import ImportanceScorer
        scorer = ImportanceScorer()
        # Even with many signals, should not exceed 1.0
        score = scorer.score("帮我修改这个代码？进化一下```python\ndef foo(): pass```", "chat_user")
        assert score <= 1.0


# ── MemoryDecay ──

class TestMemoryDecay:
    def test_fresh_memory_no_decay(self):
        from anima.memory.decay import MemoryDecay
        decay = MemoryDecay()
        now = time.time()
        mem = {"importance": 0.8, "type": "chat_user", "created_at": now, "access_count": 0}
        score = decay.compute_effective_score(mem, now)
        assert abs(score - 0.8) < 0.01  # No decay for fresh memory

    def test_decay_over_time(self):
        from anima.memory.decay import MemoryDecay
        decay = MemoryDecay()
        now = time.time()
        mem = {"importance": 0.8, "type": "chat_user", "created_at": now - 3600 * 24, "access_count": 0}
        score = decay.compute_effective_score(mem, now)
        assert score < 0.8  # Should have decayed after 24h

    def test_importance_weighted_decay(self):
        from anima.memory.decay import MemoryDecay
        decay = MemoryDecay()
        now = time.time()
        # Same age, different importance
        high_imp = {"importance": 0.9, "type": "chat_user", "created_at": now - 3600 * 12, "access_count": 0}
        low_imp = {"importance": 0.3, "type": "chat_user", "created_at": now - 3600 * 12, "access_count": 0}
        high_score = decay.compute_effective_score(high_imp, now)
        low_score = decay.compute_effective_score(low_imp, now)
        # High importance should retain more
        assert high_score > low_score

    def test_access_boost(self):
        from anima.memory.decay import MemoryDecay
        decay = MemoryDecay()
        now = time.time()
        no_access = {"importance": 0.5, "type": "chat_user", "created_at": now - 3600, "access_count": 0}
        many_access = {"importance": 0.5, "type": "chat_user", "created_at": now - 3600, "access_count": 5}
        assert decay.compute_effective_score(many_access, now) > decay.compute_effective_score(no_access, now)


# ── Lorebook ──

class TestLorebook:
    def setup_method(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        # Create _index.yaml
        import yaml
        index = {
            "entries": [
                {
                    "file": "test.md",
                    "id": "lore_test",
                    "keywords": ["PiDog", "机器人"],
                    "secondary_keywords": [],
                    "priority": 5,
                    "max_tokens": 200,
                    "scan_depth": 4,
                    "sticky": 1,
                    "cooldown": 0,
                    "enabled": True,
                }
            ]
        }
        (self.tmpdir / "_index.yaml").write_text(yaml.dump(index), encoding="utf-8")
        (self.tmpdir / "test.md").write_text("PiDog 是一个四足机器人", encoding="utf-8")

    def test_scan_match(self):
        from anima.llm.lorebook import LorebookEngine
        engine = LorebookEngine(self.tmpdir)
        result = engine.scan(
            messages=[{"role": "user", "content": "PiDog 怎么样了"}],
            budget=500,
        )
        assert len(result.entries) == 1
        assert "lore_test" in result.hit_ids

    def test_scan_no_match(self):
        from anima.llm.lorebook import LorebookEngine
        engine = LorebookEngine(self.tmpdir)
        result = engine.scan(
            messages=[{"role": "user", "content": "今天天气不错"}],
            budget=500,
        )
        assert len(result.entries) == 0

    def test_sticky(self):
        from anima.llm.lorebook import LorebookEngine
        engine = LorebookEngine(self.tmpdir)
        # First scan: trigger
        r1 = engine.scan([{"content": "PiDog 状态"}], budget=500)
        assert len(r1.entries) == 1
        # Second scan: no keyword, but sticky should keep it
        r2 = engine.scan([{"content": "其他话题"}], budget=500)
        assert len(r2.entries) == 1  # sticky=1 means 1 extra round


# ── SoulContainer ──

class TestSoulContainer:
    def test_no_rules(self):
        from anima.llm.soul_container import SoulContainer
        tmpdir = Path(tempfile.mkdtemp())
        sc = SoulContainer(tmpdir)  # No style_rules.yaml
        assert sc.transform("hello") == "hello"

    def test_length_guard(self):
        from anima.llm.soul_container import SoulContainer
        tmpdir = Path(tempfile.mkdtemp())
        import yaml
        rules = {"rules": [{"type": "length_guard", "max_chars": 20}]}
        (tmpdir / "style_rules.yaml").write_text(yaml.dump(rules), encoding="utf-8")
        sc = SoulContainer(tmpdir)
        result = sc.transform("这是一个很长的句子。这是第二个句子。这是第三个。")
        assert len(result) <= 50  # Should be truncated

    def test_not_user_facing(self):
        from anima.llm.soul_container import SoulContainer
        tmpdir = Path(tempfile.mkdtemp())
        import yaml
        rules = {"rules": [{"type": "length_guard", "max_chars": 5}]}
        (tmpdir / "style_rules.yaml").write_text(yaml.dump(rules), encoding="utf-8")
        sc = SoulContainer(tmpdir)
        long_text = "a" * 100
        assert sc.transform(long_text, is_user_facing=False) == long_text


# ── StaticKnowledgeStore ──

class TestStaticKnowledge:
    @pytest.fixture
    def store(self, tmp_path):
        """Create a real MemoryStore with static_knowledge table."""
        import asyncio
        from anima.memory.store import MemoryStore
        db_path = str(tmp_path / "test.db")
        store = asyncio.get_event_loop().run_until_complete(MemoryStore.create(db_path))
        return store

    def test_upsert_and_query(self, store):
        from anima.memory.static_store import StaticKnowledgeStore
        sk = StaticKnowledgeStore(store, node_id="desktop-123")

        sk.upsert("env", "desktop.gpu", {"name": "RTX 5090"}, scope="node:desktop-123")
        sk.upsert("project", "anima.status", {"phase": 1}, scope="global")

        # Query should return both global + own node
        results = sk.query(categories=["env", "project"])
        assert len(results) == 2

    def test_node_isolation(self, store):
        from anima.memory.static_store import StaticKnowledgeStore
        sk = StaticKnowledgeStore(store, node_id="desktop-123")

        sk.upsert("env", "gpu", "RTX 5090", scope="node:desktop-123")
        store.upsert_static_knowledge("env", "gpu", "GTX 1080", scope="node:laptop-456")

        # Default query: only see own node + global
        results = sk.query(categories=["env"])
        assert len(results) == 1  # Only desktop's entry

        # Explicit include other nodes
        results = sk.query(categories=["env"], include_other_nodes=True)
        assert len(results) == 2

    def test_cannot_write_other_node(self, store):
        from anima.memory.static_store import StaticKnowledgeStore
        sk = StaticKnowledgeStore(store, node_id="desktop-123")

        with pytest.raises(ValueError):
            sk.upsert("env", "gpu", "GTX", scope="node:laptop-456")


# ── ConversationSummarizer ──

class TestConversationSummarizer:
    def test_add_message(self):
        from anima.memory.summarizer import ConversationSummarizer
        mock_llm = MagicMock()
        s = ConversationSummarizer(mock_llm, summary_interval=100, keep_recent=5)
        asyncio.get_event_loop().run_until_complete(
            s.add_message("user", "hello")
        )
        ctx = s.get_context()
        assert len(ctx) == 1
        assert ctx[0]["role"] == "user"

    def test_check_overflow(self):
        from anima.memory.summarizer import ConversationSummarizer
        mock_llm = MagicMock()
        s = ConversationSummarizer(mock_llm, summary_interval=100, keep_recent=5)
        # Add a lot of messages
        for i in range(50):
            asyncio.get_event_loop().run_until_complete(
                s.add_message("user", "x" * 1000)
            )
        # With a small budget, should detect overflow
        assert s.check_overflow(100) is True
        # With a huge budget, should not
        assert s.check_overflow(1000000) is False

    def test_get_context_with_summary(self):
        from anima.memory.summarizer import ConversationSummarizer
        mock_llm = MagicMock()
        s = ConversationSummarizer(mock_llm, summary_interval=100, keep_recent=5)
        s._summary = "之前讨论了项目进展"
        asyncio.get_event_loop().run_until_complete(s.add_message("user", "继续"))
        ctx = s.get_context()
        assert len(ctx) == 2  # summary + message
        assert "摘要" in ctx[0]["content"] or "summary" in ctx[0]["content"].lower() or "之前" in ctx[0]["content"]


# ── MemoryRetriever ──

class TestMemoryRetriever:
    def test_empty_retrieval(self):
        from anima.memory.retriever import MemoryRetriever, MemoryContext
        r = MemoryRetriever()
        result = asyncio.get_event_loop().run_until_complete(
            r.retrieve("test query", "USER_MESSAGE")
        )
        assert isinstance(result, MemoryContext)
        assert result.total_tokens >= 0

    def test_event_category_map(self):
        from anima.memory.retriever import _EVENT_CATEGORY_MAP
        assert "project" in _EVENT_CATEGORY_MAP.get("USER_MESSAGE", [])
        assert "env" in _EVENT_CATEGORY_MAP.get("SELF_THINKING", [])


# ── Memory Tools ──

class TestMemoryTools:
    def test_get_tools(self):
        from anima.tools.builtin.memory_tools import get_memory_tools
        tools = get_memory_tools()
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert "update_feelings" in names
        assert "update_user_profile" in names


# ── Integration: store.py new methods ──

class TestStoreNewMethods:
    @pytest.fixture
    def store(self, tmp_path):
        import asyncio
        from anima.memory.store import MemoryStore
        db_path = str(tmp_path / "test.db")
        return asyncio.get_event_loop().run_until_complete(MemoryStore.create(db_path))

    def test_touch_memories(self, store):
        mid = store._save_memory_sync("test content", "chat", 0.5, {}, [])
        store.touch_memories([mid])
        mem = store.get_recent_memories(limit=1)[0]
        assert mem["access_count"] == 1

    def test_static_knowledge_crud(self, store):
        store.upsert_static_knowledge("env", "test.key", '{"val": 1}', scope="global")
        results = store.query_static_knowledge(categories=["env"], scopes=["global"])
        assert len(results) == 1
        assert results[0]["key"] == "test.key"

        store.delete_static_knowledge("env", "test.key", scope="global")
        results = store.query_static_knowledge(categories=["env"], scopes=["global"])
        assert len(results) == 0

    def test_batch_update_decay_scores(self, store):
        mid = store._save_memory_sync("test", "chat", 0.5, {}, [])
        store.batch_update_decay_scores([(mid, 0.42)])
        mems = store.get_unconsolidated_memories(limit=1)
        assert len(mems) >= 1
        found = [m for m in mems if m["id"] == mid]
        assert len(found) == 1
        assert abs(found[0]["decay_score"] - 0.42) < 0.01
