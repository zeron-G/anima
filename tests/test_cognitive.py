"""Tests for the agentic loop cognitive engine."""

import asyncio
import pytest

from anima.config import load_config
from anima.core.cognitive import AgenticLoop
from anima.core.event_queue import EventQueue
from anima.core.event_routing import EventRouter
from anima.core.tool_orchestrator import ToolOrchestrator
from anima.emotion.state import EmotionState
from anima.llm.router import LLMRouter
from anima.memory.store import MemoryStore
from anima.models.event import Event, EventType, EventPriority
from anima.perception.snapshot_cache import SnapshotCache
from anima.llm.prompt_compiler import PromptCompiler
from anima.tools.executor import ToolExecutor
from anima.tools.registry import ToolRegistry


@pytest.fixture
async def cognitive_deps(tmp_path):
    config = load_config()
    eq = EventQueue()
    sc = SnapshotCache()
    ms = await MemoryStore.create(str(tmp_path / "test.db"))
    em = EmotionState()
    lr = LLMRouter("test/model1", "test/model2")
    tr = ToolRegistry()
    tr.register_builtins()
    te = ToolExecutor(tr, max_risk=2)
    al = AgenticLoop(eq, sc, ms, em, lr, te, tr, config)
    al.set_prompt_compiler(PromptCompiler())
    yield al, eq, sc, ms
    await ms.close()


@pytest.mark.asyncio
async def test_handle_event_from_cache(cognitive_deps):
    """Verify _handle_event reads from snapshot cache context."""
    al, eq, sc, ms = cognitive_deps
    # Populate cache
    sc.update({"cpu_percent": 50, "memory_percent": 60}, [])

    # The LLM call will return None (no real LLM configured),
    # so the loop will break on LLM failure — but we verify it
    # reaches the LLM call stage without errors.
    statuses = []
    al.set_status_callback(lambda s: statuses.append(s))

    event = Event(type=EventType.USER_MESSAGE, payload={"text": "What is the meaning of life?"})
    await al._process_event(event)

    # Should have emitted at least thinking + error/idle statuses
    # (rule engine won't match this, so it goes to LLM)
    stages = [s.get("stage") for s in statuses]
    assert "thinking" in stages


@pytest.mark.asyncio
async def test_output_callback(cognitive_deps):
    """Verify output callback is called when emit_output is invoked."""
    al, eq, sc, ms = cognitive_deps
    outputs = []
    al.set_output_callback(lambda text, **kw: outputs.append(text))

    al._ctx.emit_output("Hello!")
    assert "Hello!" in outputs


@pytest.mark.asyncio
async def test_event_to_message_user(cognitive_deps):
    """Verify user message events are converted correctly."""
    al, eq, sc, ms = cognitive_deps
    router = EventRouter()
    event = Event(type=EventType.USER_MESSAGE, payload={"text": "hi there"})
    decision = router.route(event, al._ctx)
    # USER_MESSAGE "hi there" may be handled by rule engine or routed to LLM
    # If not handled, the message should be the user text
    if not decision.handled:
        assert decision.message == "hi there"


@pytest.mark.asyncio
async def test_event_to_message_startup(cognitive_deps):
    """Verify startup events produce startup prompt."""
    al, eq, sc, ms = cognitive_deps
    router = EventRouter()
    event = Event(type=EventType.STARTUP, payload={})
    decision = router.route(event, al._ctx)
    assert not decision.handled
    assert "STARTUP" in decision.message
    assert "INTERNAL" in decision.message
    assert "booted" in decision.message


@pytest.mark.asyncio
async def test_event_to_message_self_thinking(cognitive_deps):
    """Verify self-thinking events produce self-thinking prompt."""
    al, eq, sc, ms = cognitive_deps
    router = EventRouter()
    event = Event(type=EventType.SELF_THINKING, payload={"tick_count": 5})
    decision = router.route(event, al._ctx)
    assert not decision.handled
    assert "SELF_THINKING" in decision.message
    assert "INTERNAL" in decision.message
    assert "#5" in decision.message


@pytest.mark.asyncio
async def test_event_to_message_file_change(cognitive_deps):
    """Verify file change events list changed files."""
    al, eq, sc, ms = cognitive_deps
    router = EventRouter()
    event = Event(
        type=EventType.FILE_CHANGE,
        payload={"changes": [{"path": "a.py", "change": "modified"}]},
    )
    decision = router.route(event, al._ctx)
    # File change with a real .py file should not be filtered as noise
    if not decision.handled:
        assert "a.py" in decision.message
        assert "modified" in decision.message


@pytest.mark.asyncio
async def test_pick_tier_user_message(cognitive_deps):
    """User messages should use tier 1."""
    al, eq, sc, ms = cognitive_deps
    event = Event(type=EventType.USER_MESSAGE, payload={"text": "hi"})
    assert EventRouter._pick_tier(event) == 1


@pytest.mark.asyncio
async def test_pick_tier_self_thinking(cognitive_deps):
    """Self-thinking events should use tier 2."""
    al, eq, sc, ms = cognitive_deps
    event = Event(type=EventType.SELF_THINKING, payload={})
    assert EventRouter._pick_tier(event) == 2


@pytest.mark.asyncio
async def test_silent_event_no_output(cognitive_deps):
    """Verify that when LLM returns None, no output is produced."""
    al, eq, sc, ms = cognitive_deps
    sc.update({"cpu_percent": 30}, [])
    outputs = []
    al.set_output_callback(lambda text, **kw: outputs.append(text))

    # LLM will return None (no real LLM), so no output
    event = Event(type=EventType.SELF_THINKING, payload={"tick_count": 1})
    await al._process_event(event)
    assert len(outputs) == 0


@pytest.mark.asyncio
async def test_conversation_trimming(cognitive_deps):
    """Verify conversation buffer trims correctly."""
    al, eq, sc, ms = cognitive_deps
    # Fill conversation beyond limit
    for i in range(200):
        al._ctx.conversation.append({"role": "user", "content": f"msg {i}"})
    al._ctx.trim_conversation()
    assert len(al._ctx.conversation) == al._ctx.max_conversation_turns * 2


@pytest.mark.asyncio
async def test_format_result_success(cognitive_deps):
    """Verify tool result formatting for successful results."""
    result = {"success": True, "result": {"stdout": "hello world", "returncode": 0}}
    text = ToolOrchestrator.format_result("shell", result)
    assert "hello world" in text


@pytest.mark.asyncio
async def test_format_result_error(cognitive_deps):
    """Verify tool result formatting for errors."""
    result = {"success": False, "error": "command not found"}
    text = ToolOrchestrator.format_result("shell", result)
    assert "Error" in text
    assert "command not found" in text
