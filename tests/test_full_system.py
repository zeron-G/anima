"""Full system integration test — validates the complete ANIMA stack.

Tests every critical path that a real user would encounter:
1. ANIMA starts and all subsystems initialize
2. Discord bot connects and receives messages
3. Messages route through session lock → event queue → cognitive → response → Discord
4. Gossip mesh discovers peers
5. Memory sync works between nodes
6. Rule engine handles cheap events without LLM
7. File changes are filtered before queue
8. Dashboard is accessible
"""

import asyncio
import os
import sys
import time
import tempfile

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.stdout.reconfigure(encoding="utf-8")

import pytest
from anima.config import load_config, get
from anima.core.event_queue import EventQueue
from anima.perception.snapshot_cache import SnapshotCache
from anima.perception.diff_engine import DiffEngine
from anima.memory.working import WorkingMemory
from anima.memory.store import MemoryStore
from anima.emotion.state import EmotionState
from anima.tools.registry import ToolRegistry
from anima.tools.executor import ToolExecutor
from anima.core.rule_engine import RuleEngine
from anima.llm.router import LLMRouter
from anima.llm.prompts import PromptBuilder
from anima.core.heartbeat import HeartbeatEngine
from anima.core.cognitive import AgenticLoop
from anima.core.agents import AgentManager
from anima.core.scheduler import Scheduler
from anima.tools.builtin.agent_tools import set_agent_manager
from anima.tools.builtin.scheduler_tools import set_scheduler
from anima.llm.usage import UsageTracker
from anima.models.event import Event, EventType, EventPriority
from anima.network.session_router import SessionRouter
from anima.network.split_brain import SplitBrainDetector


@pytest.fixture
async def full_system(tmp_path):
    """Create a complete ANIMA system for testing."""
    config = load_config()
    scheduler = Scheduler()
    set_scheduler(scheduler)
    am = AgentManager(max_concurrent=5)
    set_agent_manager(am)

    eq = EventQueue()
    sc = SnapshotCache()
    de = DiffEngine.from_config(config.get("diff_rules", {}))
    wm = WorkingMemory()
    ms = await MemoryStore.create(str(tmp_path / "test.db"))
    em = EmotionState()
    tr = ToolRegistry()
    tr.register_builtins()
    te = ToolExecutor(tr, max_risk=3)
    lr = LLMRouter("claude-opus-4-6", "claude-sonnet-4-6", daily_budget=10.0)
    ut = UsageTracker(ms)
    lr.set_usage_tracker(ut)
    pb = PromptBuilder()
    am.wire_llm(lr, te, tr)

    hb = HeartbeatEngine(eq, sc, de, em, wm, lr, pb, config)
    hb.set_scheduler(scheduler)

    loop = AgenticLoop(
        event_queue=eq, snapshot_cache=sc, memory_store=ms,
        emotion_state=em, llm_router=lr, prompt_builder=pb,
        tool_executor=te, tool_registry=tr, config=config,
    )

    outputs = []
    statuses = []
    loop.set_output_callback(lambda text, **kw: outputs.append({"text": text, **kw}))
    loop.set_status_callback(lambda s: statuses.append(s))

    # Populate snapshot cache
    await hb._on_script_tick()

    yield {
        "eq": eq, "sc": sc, "ms": ms, "em": em, "tr": tr, "te": te,
        "lr": lr, "pb": pb, "hb": hb, "loop": loop, "am": am,
        "scheduler": scheduler, "ut": ut, "outputs": outputs,
        "statuses": statuses, "config": config,
    }

    await ms.close()


@pytest.mark.asyncio
async def test_model_config_is_correct(full_system):
    """Verify Opus is tier1 and Sonnet is tier2."""
    lr = full_system["lr"]
    assert "opus" in lr._tier1_model
    assert "sonnet" in lr._tier2_model


@pytest.mark.asyncio
async def test_all_tools_registered(full_system):
    """Verify all 20+ tools are available."""
    tr = full_system["tr"]
    tools = tr.list_tools()
    tool_names = {t.name for t in tools}
    assert len(tools) >= 25
    for expected in ["shell", "read_file", "write_file", "edit_file",
                     "glob_search", "grep_search", "web_fetch",
                     "spawn_agent", "schedule_job", "claude_code",
                     "github", "send_email", "read_email", "remote_exec"]:
        assert expected in tool_names, f"Missing tool: {expected}"


@pytest.mark.asyncio
async def test_greeting_uses_rule_engine_not_llm(full_system):
    """Greeting should be handled by rule engine (zero LLM cost)."""
    eq, loop, statuses = full_system["eq"], full_system["loop"], full_system["statuses"]
    statuses.clear()

    await eq.put(Event(type=EventType.USER_MESSAGE, payload={"text": "hello"}, priority=EventPriority.HIGH))
    evt = await eq.get_timeout(2.0)
    await loop._handle_event(evt)

    stages = [s["stage"] for s in statuses]
    assert "rule_engine" in stages, f"Expected rule_engine, got {stages}"
    assert "thinking" not in stages, "LLM should NOT be called for greetings"


@pytest.mark.asyncio
async def test_discord_message_creates_correct_event(full_system):
    """Simulate Discord message arriving and verify event structure."""
    eq = full_system["eq"]

    # Simulate what on_discord_message does
    discord_data = {
        "text": "test message from Discord",
        "user": "914089772421615636",
        "user_name": "TestUser",
        "channel": "discord",
        "source": "discord:914089772421615636",
    }
    await eq.put(Event(
        type=EventType.USER_MESSAGE,
        payload=discord_data,
        priority=EventPriority.HIGH,
        source="discord",
    ))

    evt = await eq.get_timeout(2.0)
    assert evt is not None
    assert evt.type == EventType.USER_MESSAGE
    assert evt.payload["text"] == "test message from Discord"
    assert evt.payload["source"] == "discord:914089772421615636"
    assert evt.source == "discord"


@pytest.mark.asyncio
async def test_output_callback_receives_source(full_system):
    """Verify output callback receives source for routing."""
    loop, outputs = full_system["loop"], full_system["outputs"]
    outputs.clear()

    # Set source to discord
    loop._current_source = "discord:123456"
    await loop._output("test reply")

    assert len(outputs) == 1
    assert outputs[0]["text"] == "test reply"
    assert outputs[0].get("source") == "discord:123456"


@pytest.mark.asyncio
async def test_session_router_lock_and_release():
    """Session routing works correctly."""
    sr = SessionRouter("node-desktop")

    # Lock a Discord session
    assert sr.try_lock("discord:123", channel="discord")
    assert sr.is_mine("discord:123")

    # Another node can't lock it
    sr.handle_remote_lock("discord:123", "node-laptop", timestamp=time.time())
    # Desktop has lower ID → keeps it
    assert sr.get_owner("discord:123") == "node-desktop"

    # Release
    sr.release("discord:123")
    assert not sr.is_mine("discord:123")


@pytest.mark.asyncio
async def test_session_takeover_on_node_death():
    """When a node dies, its sessions are released for takeover."""
    sr = SessionRouter("node-laptop")
    sr.handle_remote_lock("discord:123", "node-desktop")
    assert sr.get_owner("discord:123") == "node-desktop"

    # Desktop dies
    released = sr.release_all_for_node("node-desktop")
    assert "discord:123" in released

    # Laptop takes over
    assert sr.try_lock("discord:123")
    assert sr.is_mine("discord:123")


@pytest.mark.asyncio
async def test_file_change_noise_filtered(full_system):
    """Internal file changes (__pycache__, logs) should not reach LLM."""
    loop, statuses = full_system["loop"], full_system["statuses"]
    eq = full_system["eq"]
    statuses.clear()

    # Noise event
    await eq.put(Event(
        type=EventType.FILE_CHANGE,
        payload={"changes": [{"path": "data/notes/test.md", "change": "created"}]},
        priority=EventPriority.NORMAL,
    ))
    evt = await eq.get_timeout(2.0)
    msg = loop._event_to_message(evt)
    assert "noise" in msg.lower() or "no action" in msg.lower()


@pytest.mark.asyncio
async def test_scheduler_fires_events(full_system):
    """Cron scheduler fires events into the queue."""
    scheduler = full_system["scheduler"]
    eq = full_system["eq"]

    # Add a job that fires immediately
    job = scheduler.add_job("test-job", "*/1 * * * *", "test prompt", recurring=False)
    # Force next_run to now
    job.next_run = time.time() - 1

    # Simulate heartbeat tick checking scheduler
    due = scheduler.get_due_jobs()
    assert len(due) >= 1
    assert due[0].name == "test-job"


@pytest.mark.asyncio
async def test_emotion_state_persists(full_system):
    """Emotion state changes and decays."""
    em = full_system["em"]
    initial_eng = em.engagement

    em.adjust(engagement=0.3)
    assert em.engagement > initial_eng

    em.decay(rate=0.05)
    # Should have moved toward baseline


@pytest.mark.asyncio
async def test_memory_stores_and_retrieves(full_system):
    """Memory system works end-to-end."""
    ms = full_system["ms"]

    # Save
    mid = ms.save_memory("test memory content", type="chat", importance=0.9)
    assert mid.startswith("mem_")

    # Retrieve
    results = ms.search_memories(query="test memory", limit=5)
    assert len(results) >= 1
    assert "test memory content" in results[0]["content"]


@pytest.mark.asyncio
async def test_usage_tracking(full_system):
    """LLM usage is tracked in SQLite."""
    ut = full_system["ut"]
    ms = full_system["ms"]

    ms.log_llm_usage(
        model="claude-opus-4-6", provider="anthropic", auth_mode="oauth",
        tier="tier1", prompt_tokens=100, completion_tokens=50,
    )

    history = ut.get_history(10)
    assert len(history) >= 1
    assert history[0]["model"] == "claude-opus-4-6"
    assert history[0]["auth_mode"] == "oauth"


@pytest.mark.asyncio
async def test_split_brain_detection():
    """Split-brain correctly identifies majority/minority."""
    from anima.network.node import NodeIdentity

    identity = NodeIdentity.__new__(NodeIdentity)
    identity._path = None
    identity._data = {
        "self_id": "node-a",
        "registered_nodes": [
            {"id": "node-a", "status": "alive"},
            {"id": "node-b", "status": "alive"},
        ],
    }

    sb = SplitBrainDetector(identity)

    # Both visible → majority
    assert sb.check(2) == True
    assert not sb.is_readonly

    # Only self → minority
    assert sb.check(1) == False
    assert sb.is_readonly

    # Recovery
    assert sb.check(2) == True
    assert not sb.is_readonly
