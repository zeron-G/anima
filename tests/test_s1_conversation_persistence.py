"""S1 regression: episodic_memories is the single source of truth for the
conversation, and every in-memory buffer is a recoverable cache.

These lock down the bug behind "记忆/session 混乱和丢失":
  - the summarizer used to start EMPTY on every restart (never persisted/
    restored), so the USER_MESSAGE prompt lost all recent context;
  - sessions were RAM-only, so eviction / TTL expiry / restart dropped the
    conversation with no way to recover it.
"""

from __future__ import annotations

import pytest

from anima.memory.summarizer import ConversationSummarizer
from anima.core.session_manager import SessionManager
from pgutil import make_test_store


async def _store(tmp_path):
    return await make_test_store()


async def _save_turn(store, role, content, session_id="local"):
    await store.save_memory_async(
        content=content, type="chat", importance=0.5,
        metadata={"role": role}, session_id=session_id,
    )


@pytest.mark.asyncio
async def test_session_id_column_migrated(tmp_path):
    store = await _store(tmp_path)
    rows = store._db.fetch_sync(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'episodic_memories'"
    )
    cols = [r["column_name"] for r in rows]
    assert "session_id" in cols
    await store.close()


@pytest.mark.asyncio
async def test_conversation_rebuilds_from_episodic_scoped_and_ordered(tmp_path):
    store = await _store(tmp_path)
    await _save_turn(store, "user", "hi eva")
    await _save_turn(store, "assistant", "hi 主人")
    await _save_turn(store, "user", "discord hello", session_id="discord:7")

    local = await store.get_session_conversation_async("local", limit=40)
    disc = await store.get_session_conversation_async("discord:7", limit=40)

    # Scoped by session
    assert [t["content"] for t in local] == ["hi eva", "hi 主人"]
    assert [t["content"] for t in disc] == ["discord hello"]
    # Chronological + roles preserved
    assert local[0]["role"] == "user" and local[1]["role"] == "assistant"
    await store.close()


@pytest.mark.asyncio
async def test_legacy_rows_default_to_local_session(tmp_path):
    """Existing history (saved before S1, no session_id) must land in 'local'
    so single-user memory is preserved, not orphaned."""
    store = await _store(tmp_path)
    # No session_id arg → column default 'local'
    await store.save_memory_async(content="legacy turn", type="chat",
                                  metadata={"role": "user"})
    local = await store.get_session_conversation_async("local", limit=40)
    assert [t["content"] for t in local] == ["legacy turn"]
    await store.close()


@pytest.mark.asyncio
async def test_summarizer_restored_from_episodic_feeds_prompt(tmp_path):
    """The restart bug: prompt reads summarizer.get_context(); before S1 the
    summarizer started empty after restart. restore_from_db must repopulate it
    from the episodic turns so the prompt has recent context immediately."""
    store = await _store(tmp_path)
    await _save_turn(store, "user", "remember my cat is named 球球")
    await _save_turn(store, "assistant", "记住啦，球球")

    turns = await store.get_session_conversation_async("local", limit=40)

    # Fresh summarizer (as after a restart) is empty...
    summ = ConversationSummarizer(llm_router=None, keep_recent=10)
    assert summ.get_context() == []
    # ...until rebuilt from episodic.
    summ.restore_from_db(turns)
    ctx = summ.get_context()
    contents = [m["content"] for m in ctx]
    assert any("球球" in c for c in contents)
    await store.close()


@pytest.mark.asyncio
async def test_session_recovers_conversation_after_eviction(tmp_path):
    """A session evicted from the in-memory cache must rebuild its conversation
    from episodic on the next access — no data loss."""
    store = await _store(tmp_path)
    await _save_turn(store, "user", "earlier discord message", session_id="discord:42")

    mgr = SessionManager(max_sessions=2, session_ttl_s=3600, memory_store=store)

    # First access rebuilds from episodic
    s = mgr.get_or_create("discord:42")
    assert [m["content"] for m in s.conversation] == ["earlier discord message"]

    # Force eviction by exceeding capacity with other sessions
    mgr.get_or_create("discord:1")
    mgr.get_or_create("discord:2")
    assert mgr.get("discord:42") is None  # evicted from cache

    # Re-accessing recovers the conversation from episodic (the truth)
    s2 = mgr.get_or_create("discord:42")
    assert [m["content"] for m in s2.conversation] == ["earlier discord message"]
    await store.close()


@pytest.mark.asyncio
async def test_ttl_expiry_drops_cache_but_episodic_survives(tmp_path):
    store = await _store(tmp_path)
    await _save_turn(store, "user", "msg before expiry", session_id="tg:9")
    mgr = SessionManager(max_sessions=50, session_ttl_s=-1, memory_store=store)  # ttl<0 → always expired
    mgr.get_or_create("tg:9")
    removed = mgr.cleanup_expired()
    assert removed == 1 and mgr.get("tg:9") is None
    # Recovered from episodic on reconnect
    s = mgr.get_or_create("tg:9")
    assert [m["content"] for m in s.conversation] == ["msg before expiry"]
    await store.close()
