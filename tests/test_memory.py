"""Tests for working memory and memory store."""

import pytest

from anima.memory.working import WorkingMemory
from anima.models.memory_item import MemoryItem, MemoryType


def test_working_memory_add():
    wm = WorkingMemory(capacity=3)
    wm.add(MemoryItem(content="a", type=MemoryType.OBSERVATION, importance=0.3))
    wm.add(MemoryItem(content="b", type=MemoryType.CHAT, importance=0.9))
    assert wm.size == 2


def test_working_memory_evicts_lowest_importance():
    wm = WorkingMemory(capacity=3)
    wm.add(MemoryItem(content="low", type=MemoryType.OBSERVATION, importance=0.1))
    wm.add(MemoryItem(content="mid", type=MemoryType.DECISION, importance=0.5))
    wm.add(MemoryItem(content="high", type=MemoryType.CHAT, importance=0.9))
    # Full — adding one more should evict "low"
    evicted = wm.add(MemoryItem(content="new", type=MemoryType.CHAT, importance=0.8))
    assert evicted is not None
    assert evicted.content == "low"
    assert wm.size == 3


def test_working_memory_get_by_importance():
    wm = WorkingMemory(capacity=10)
    wm.add(MemoryItem(content="a", type=MemoryType.OBSERVATION, importance=0.2))
    wm.add(MemoryItem(content="b", type=MemoryType.CHAT, importance=0.9))
    wm.add(MemoryItem(content="c", type=MemoryType.DECISION, importance=0.5))
    top = wm.get_by_importance(2)
    assert len(top) == 2
    assert top[0].importance >= top[1].importance


def test_working_memory_get_summary():
    wm = WorkingMemory(capacity=10)
    wm.add(MemoryItem(content="test observation", type=MemoryType.OBSERVATION))
    summary = wm.get_summary()
    assert "test observation" in summary


def test_working_memory_empty_summary():
    wm = WorkingMemory(capacity=10)
    assert "empty" in wm.get_summary()


@pytest.mark.asyncio
async def test_memory_store_save_and_search(tmp_path):
    from anima.memory.store import MemoryStore
    db_path = str(tmp_path / "test.db")
    store = await MemoryStore.create(db_path)
    mid = store.save_memory("hello world", type="chat", importance=0.9)
    assert mid.startswith("mem_")
    results = store.search_memories(query="hello", limit=5)
    assert len(results) == 1
    assert results[0]["content"] == "hello world"
    await store.close()


@pytest.mark.asyncio
async def test_memory_store_recent(tmp_path):
    from anima.memory.store import MemoryStore
    db_path = str(tmp_path / "test.db")
    store = await MemoryStore.create(db_path)
    store.save_memory("first", type="chat", importance=0.5)
    store.save_memory("second", type="chat", importance=0.7)
    recent = store.get_recent_memories(limit=1)
    assert len(recent) == 1
    assert recent[0]["content"] == "second"
    await store.close()


@pytest.mark.asyncio
async def test_memory_store_audit(tmp_path):
    from anima.memory.store import MemoryStore
    db_path = str(tmp_path / "test.db")
    store = await MemoryStore.create(db_path)
    store.audit("test_action", "test details")
    # Should not raise
    await store.close()
