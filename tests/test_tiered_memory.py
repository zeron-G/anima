"""TieredMemoryStore composite + retriever cloud-union (P3b), with fake stores
(CI-safe, no DB). Live two-Postgres verification is done separately on docker."""
from __future__ import annotations

import asyncio

from anima.memory.store import TieredMemoryStore


class FakeStore:
    def __init__(self, name):
        self.name = name
        self._db = f"db::{name}"
        self.calls = []
        self.origin_node = ""
        self.locus = ""
        self.closed = False

    def save_memory(self, **kw):
        self.calls.append(("save_memory", kw))
        return f"{self.name}-mid"

    async def search_memories_async(self, query, limit=5):
        return self._hits

    async def close(self):
        self.closed = True


def test_tiered_flag_and_db():
    w, lt = FakeStore("working"), FakeStore("cloud")
    ts = TieredMemoryStore(w, lt)
    assert ts.tiered is True
    assert ts.working is w and ts.long_term is lt
    assert ts._db == "db::working"          # back-compat ._db → working


def test_non_tiered_when_same():
    s = FakeStore("solo")
    ts = TieredMemoryStore(s, s)
    assert ts.tiered is False


def test_delegates_ops_to_working():
    w, lt = FakeStore("working"), FakeStore("cloud")
    ts = TieredMemoryStore(w, lt)
    mid = ts.save_memory(content="x", type="chat")   # not defined on TieredMemoryStore
    assert mid == "working-mid"
    assert w.calls and not lt.calls                  # hit working only


def test_close_closes_both():
    w, lt = FakeStore("working"), FakeStore("cloud")
    asyncio.run(TieredMemoryStore(w, lt).close())
    assert w.closed and lt.closed


def _run_semantic(store, long_term, wh, ch):
    from anima.memory.retriever import MemoryRetriever
    store._hits = wh
    if long_term is not None:
        long_term._hits = ch
    r = MemoryRetriever(memory_store=store, long_term=long_term)
    cands = []
    asyncio.run(r._stage_semantic(cands, set(), "hello", "USER_MESSAGE"))
    return {c["id"] for c in cands}


def test_retriever_unions_working_and_cloud():
    w, lt = FakeStore("working"), FakeStore("cloud")
    ids = _run_semantic(w, lt, [{"id": "w1", "content": "local"}], [{"id": "c1", "content": "cloud"}])
    assert ids == {"w1", "c1"}                       # recall spans both tiers


def test_retriever_dedups_across_tiers():
    w, lt = FakeStore("working"), FakeStore("cloud")
    ids = _run_semantic(w, lt, [{"id": "m1", "content": "local"}], [{"id": "m1", "content": "cloud"}])
    assert ids == {"m1"}                             # same id not double-counted


def test_retriever_single_tier_when_no_long_term():
    w = FakeStore("working")
    ids = _run_semantic(w, None, [{"id": "w1", "content": "local"}], [])
    assert ids == {"w1"}                             # unchanged single-tier behavior


def test_retriever_cloud_down_degrades_to_local():
    w, lt = FakeStore("working"), FakeStore("cloud")

    async def boom(query, limit=5):
        raise RuntimeError("cloud unreachable")
    lt.search_memories_async = boom
    w._hits = [{"id": "w1", "content": "local"}]
    from anima.memory.retriever import MemoryRetriever
    r = MemoryRetriever(memory_store=w, long_term=lt)
    cands = []
    asyncio.run(r._stage_semantic(cands, set(), "hello", "USER_MESSAGE"))
    assert {c["id"] for c in cands} == {"w1"}        # graceful local-only fallback
