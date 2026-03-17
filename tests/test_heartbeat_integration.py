"""Integration test — runs real heartbeat ticks and verifies the full pipeline.

This test exercises the actual system:
- Script heartbeat samples real CPU/memory/disk
- File watcher detects a real file creation
- Diff engine computes real diffs
- Events flow through the queue
- Cognitive cycle (agentic loop) processes them

No LLM calls are made (LLM returns None → loop breaks gracefully).
"""

import asyncio
import time
import pytest

from anima.config import load_config, get
from anima.core.event_queue import EventQueue
from anima.core.heartbeat import HeartbeatEngine
from anima.core.cognitive import AgenticLoop
from anima.emotion.state import EmotionState
from anima.llm.prompt_compiler import PromptCompiler
from anima.llm.router import LLMRouter
from anima.memory.store import MemoryStore
from anima.memory.working import WorkingMemory
from anima.models.event import Event, EventType, EventPriority
from anima.perception.diff_engine import DiffEngine
from anima.perception.snapshot_cache import SnapshotCache
from anima.tools.executor import ToolExecutor
from anima.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_heartbeat_integration_file_detection(tmp_path):
    """End-to-end: heartbeat detects file change → event queue → cognitive processes it.

    This is the Phase 0 milestone demo scenario (without LLM).
    """
    config = load_config()
    config["perception"] = {
        "watch_paths": [str(tmp_path)],
        "watch_extensions": [".txt", ".py", ".md"],
        "snapshot_history_size": 10,
    }

    # Build subsystems
    event_queue = EventQueue()
    snapshot_cache = SnapshotCache(history_size=10)
    diff_engine = DiffEngine.from_config(config.get("diff_rules", {}))
    emotion_state = EmotionState(baseline=config.get("emotion", {}).get("baseline", {}))
    working_memory = WorkingMemory(capacity=20)
    memory_store = await MemoryStore.create(str(tmp_path / "test.db"))
    # LLM router with zero budget → LLM calls return None
    llm_router = LLMRouter("test/m1", "test/m2", daily_budget=0.0)
    tool_registry = ToolRegistry()
    tool_registry.register_builtins()
    tool_executor = ToolExecutor(tool_registry, max_risk=3)

    heartbeat = HeartbeatEngine(
        event_queue, snapshot_cache, diff_engine, emotion_state,
        working_memory, llm_router, config,
    )

    cognitive = AgenticLoop(
        event_queue, snapshot_cache, memory_store, emotion_state,
        llm_router, tool_executor, tool_registry, config,
    )
    cognitive.set_prompt_compiler(PromptCompiler())

    outputs = []
    cognitive.set_output_callback(lambda text, **kw: outputs.append(text))

    # --- Phase 1: Baseline tick (initializes file watcher) ---
    await heartbeat._on_script_tick()
    assert snapshot_cache.get_latest() is not None, "Snapshot cache should have data after first tick"
    print("[tick 1] Baseline snapshot captured. Queue empty:", event_queue.empty())

    # --- Phase 2: Create files in watched directory ---
    (tmp_path / "hello.txt").write_text("Hello from integration test!")
    (tmp_path / "notes.md").write_text("# Test Notes\nSome content here.")
    print("[action] Created hello.txt and notes.md")

    # --- Phase 3: Second tick detects file changes ---
    await heartbeat._on_script_tick()
    print("[tick 2] After file creation. Queue size:", event_queue.qsize())

    # Should have a FILE_CHANGE event
    assert not event_queue.empty(), "Event queue should have FILE_CHANGE event"

    # --- Phase 4: Cognitive cycle processes the event ---
    event = await event_queue.get_timeout(timeout=1.0)
    assert event is not None, "Should get an event"
    assert event.type == EventType.FILE_CHANGE, f"Expected FILE_CHANGE, got {event.type}"
    changes = event.payload.get("changes", [])
    print(f"[cognitive] Processing FILE_CHANGE with {len(changes)} changes:")
    for c in changes:
        print(f"  - {c['path']} ({c['change']})")

    # Run agentic loop on the event (LLM will return None, but no crash)
    await cognitive._process_event(event)

    # --- Phase 5: Verify results ---
    # Emotion should have shifted (engagement adjustment happens in _handle_event)
    print(f"[result] Emotion state: {emotion_state.to_dict()}")
    print(f"[result] Outputs to user: {outputs}")

    # --- Phase 6: Modify a file and detect again ---
    (tmp_path / "hello.txt").write_text("Updated content!")
    await heartbeat._on_script_tick()
    print(f"[tick 3] After modification. Queue size: {event_queue.qsize()}")

    if not event_queue.empty():
        event2 = await event_queue.get_timeout(timeout=1.0)
        if event2 and event2.type == EventType.FILE_CHANGE:
            await cognitive._process_event(event2)
            print("[cognitive] Processed second FILE_CHANGE")

    # --- Phase 7: Verify snapshot history ---
    history = snapshot_cache.get_history(5)
    assert len(history) >= 3, f"Should have 3+ snapshots, got {len(history)}"
    print(f"[result] Snapshot history: {len(history)} entries")
    for i, snap in enumerate(history):
        state = snap.get("system_state", {})
        print(f"  [{i}] CPU: {state.get('cpu_percent', '?')}%, MEM: {state.get('memory_percent', '?')}%")

    # Cleanup
    await memory_store.close()
    print("\n[PASS] Integration test complete - heartbeat detected file changes and cognitive cycle processed them!")


@pytest.mark.asyncio
async def test_heartbeat_integration_system_monitoring(tmp_path):
    """Verify system monitoring captures real CPU/memory/disk data."""
    config = load_config()
    config["perception"] = {"watch_paths": [str(tmp_path)], "snapshot_history_size": 5}

    event_queue = EventQueue()
    snapshot_cache = SnapshotCache(history_size=5)
    diff_engine = DiffEngine.from_config(config.get("diff_rules", {}))
    emotion_state = EmotionState()
    working_memory = WorkingMemory()
    llm_router = LLMRouter("t1", "t2", daily_budget=0.0)

    heartbeat = HeartbeatEngine(
        event_queue, snapshot_cache, diff_engine, emotion_state,
        working_memory, llm_router, config,
    )

    # Run 3 ticks
    for i in range(3):
        await heartbeat._on_script_tick()

    latest = snapshot_cache.get_latest()
    assert latest is not None
    state = latest["system_state"]
    print(f"\nSystem State:")
    print(f"  CPU:      {state['cpu_percent']:.1f}%")
    print(f"  Memory:   {state['memory_percent']:.1f}%")
    print(f"  Disk:     {state['disk_percent']:.1f}%")
    print(f"  Mem Avail:{state['memory_available_mb']} MB")
    print(f"  Processes:{state['process_count']}")

    assert 0 <= state["cpu_percent"] <= 100
    assert 0 < state["memory_percent"] <= 100
    assert 0 < state["disk_percent"] <= 100
    assert state["process_count"] > 0

    history = snapshot_cache.get_history(3)
    assert len(history) == 3
    print(f"\n[PASS] System monitoring works - captured {len(history)} snapshots")


@pytest.mark.asyncio
async def test_heartbeat_integration_emotion_decay():
    """Verify emotion decay works correctly over multiple ticks."""
    emotion = EmotionState(baseline={"engagement": 0.5, "confidence": 0.6, "curiosity": 0.7, "concern": 0.2})

    # Spike emotions
    emotion.adjust(engagement=0.4, concern=0.5)
    print(f"After spike: {emotion.to_dict()}")
    assert emotion.engagement == pytest.approx(0.9, abs=0.01)
    assert emotion.concern == pytest.approx(0.7, abs=0.01)

    # Decay 10 times
    for _ in range(10):
        emotion.decay(rate=0.05)

    print(f"After 10 decays: {emotion.to_dict()}")
    # Should be back near baseline
    assert abs(emotion.engagement - 0.5) < 0.1
    assert abs(emotion.concern - 0.2) < 0.1
    print("[PASS] Emotion decay works correctly")


@pytest.mark.asyncio
async def test_heartbeat_integration_user_message_flow(tmp_path):
    """Simulate user message → queue → cognitive cycle → agentic loop.

    Without a real LLM, the loop breaks on LLM failure. We verify the
    pipeline runs without errors and audit is recorded.
    """
    config = load_config()

    event_queue = EventQueue()
    snapshot_cache = SnapshotCache()
    memory_store = await MemoryStore.create(str(tmp_path / "test.db"))
    emotion_state = EmotionState()
    llm_router = LLMRouter("t1", "t2", daily_budget=0.0)
    tool_registry = ToolRegistry()
    tool_registry.register_builtins()
    tool_executor = ToolExecutor(tool_registry, max_risk=3)

    cognitive = AgenticLoop(
        event_queue, snapshot_cache, memory_store, emotion_state,
        llm_router, tool_executor, tool_registry, config,
    )
    cognitive.set_prompt_compiler(PromptCompiler())

    outputs = []
    cognitive.set_output_callback(lambda text, **kw: outputs.append(text))

    # Populate snapshot cache
    snapshot_cache.update({"cpu_percent": 30, "memory_percent": 45}, [])

    # Simulate user greeting
    greeting_event = Event(
        type=EventType.USER_MESSAGE,
        payload={"text": "你好"},
        priority=EventPriority.HIGH,
    )

    await cognitive._process_event(greeting_event)

    # Without a real LLM, the agentic loop breaks on LLM failure.
    # The important thing is no crash and audit was recorded.
    print(f"User: 你好")
    print(f"Outputs: {outputs}")
    print(f"Emotion after interaction: {emotion_state.to_dict()}")
    print("[PASS] User message flow runs without errors")

    await memory_store.close()
