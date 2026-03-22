"""Sprint 4 Architecture Tests — CognitiveContext, EventRouter, ToolOrchestrator, ResponseHandler.

Tests the decomposed cognitive loop components:
  - H-24: God class decomposition verification
  - CognitiveContext construction validation
  - EventRouter routing decisions
  - ToolOrchestrator tool selection
  - ResponseHandler output dispatch
  - Backward compatibility (AgenticLoop still works)
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ── CognitiveContext tests ──


class TestCognitiveContext:
    """Test CognitiveContext creation and validation."""

    def test_requires_all_core_deps(self):
        from anima.core.context import CognitiveContext
        with pytest.raises(ValueError, match="missing required"):
            CognitiveContext(
                event_queue=MagicMock(),
                snapshot_cache=MagicMock(),
                memory_store=None,  # Missing core dep!
                emotion=MagicMock(),
                llm_router=MagicMock(),
                tool_executor=MagicMock(),
                tool_registry=MagicMock(),
                prompt_compiler=MagicMock(),
            )

    def test_prompt_compiler_deferred_validation(self):
        """prompt_compiler can be None at init, validated at run time."""
        from anima.core.context import CognitiveContext
        ctx = CognitiveContext(
            event_queue=MagicMock(),
            snapshot_cache=MagicMock(),
            memory_store=MagicMock(),
            emotion=MagicMock(),
            llm_router=MagicMock(),
            tool_executor=MagicMock(),
            tool_registry=MagicMock(),
            prompt_compiler=None,  # OK at init
        )
        with pytest.raises(ValueError, match="prompt_compiler"):
            ctx.validate_ready()  # NOT OK at run time

    def test_accepts_all_deps(self):
        from anima.core.context import CognitiveContext
        ctx = CognitiveContext(
            event_queue=MagicMock(),
            snapshot_cache=MagicMock(),
            memory_store=MagicMock(),
            emotion=MagicMock(),
            llm_router=MagicMock(),
            tool_executor=MagicMock(),
            tool_registry=MagicMock(),
            prompt_compiler=MagicMock(),
        )
        assert ctx.gossip_mesh is None  # Optional defaults to None
        assert ctx.conversation == []

    def test_optional_deps_default_none(self):
        from anima.core.context import CognitiveContext
        ctx = CognitiveContext(
            event_queue=MagicMock(),
            snapshot_cache=MagicMock(),
            memory_store=MagicMock(),
            emotion=MagicMock(),
            llm_router=MagicMock(),
            tool_executor=MagicMock(),
            tool_registry=MagicMock(),
            prompt_compiler=MagicMock(),
        )
        assert ctx.memory_retriever is None
        assert ctx.summarizer is None
        assert ctx.importance_scorer is None

    def test_trim_conversation(self):
        from anima.core.context import CognitiveContext
        ctx = CognitiveContext(
            event_queue=MagicMock(),
            snapshot_cache=MagicMock(),
            memory_store=MagicMock(),
            emotion=MagicMock(),
            llm_router=MagicMock(),
            tool_executor=MagicMock(),
            tool_registry=MagicMock(),
            prompt_compiler=MagicMock(),
            max_conversation_turns=5,
        )
        for i in range(20):
            ctx.conversation.append({"role": "user", "content": f"msg {i}"})
        ctx.trim_conversation()
        assert len(ctx.conversation) == 10  # 5 turns * 2


# ── EventRouter tests ──


class TestEventRouter:
    """Test EventRouter routing decisions."""

    def _make_ctx(self):
        from anima.core.context import CognitiveContext
        mock_snapshot = MagicMock()
        mock_snapshot.get_latest.return_value = {"system_state": {"cpu_percent": 20}}
        return CognitiveContext(
            event_queue=MagicMock(),
            snapshot_cache=mock_snapshot,
            memory_store=MagicMock(),
            emotion=MagicMock(),
            llm_router=MagicMock(),
            tool_executor=MagicMock(),
            tool_registry=MagicMock(),
            prompt_compiler=MagicMock(),
        )

    def test_user_message_route(self):
        from anima.core.event_routing import EventRouter
        from anima.models.event import Event, EventType
        router = EventRouter()
        event = Event(type=EventType.USER_MESSAGE, payload={"text": "帮我看代码"})
        ctx = self._make_ctx()
        decision = router.route(event, ctx)
        assert not decision.handled
        assert decision.message == "帮我看代码"
        assert decision.tier == 1  # User messages → Opus
        assert decision.needs_tools
        assert not decision.is_self

    def test_self_thinking_route(self):
        from anima.core.event_routing import EventRouter
        from anima.models.event import Event, EventType
        router = EventRouter()
        event = Event(type=EventType.SELF_THINKING, payload={"tick_count": 5})
        ctx = self._make_ctx()
        decision = router.route(event, ctx)
        assert not decision.handled
        assert decision.is_self
        assert decision.tier == 2
        assert "[INTERNAL: SELF_THINKING" in decision.message

    def test_startup_route(self):
        from anima.core.event_routing import EventRouter
        from anima.models.event import Event, EventType
        router = EventRouter()
        event = Event(type=EventType.STARTUP, payload={})
        ctx = self._make_ctx()
        decision = router.route(event, ctx)
        assert not decision.handled
        assert decision.is_self
        assert "[INTERNAL: STARTUP]" in decision.message


# ── ToolOrchestrator tests ──


class TestToolOrchestrator:
    """Test ToolOrchestrator tool selection and execution."""

    def _make_orchestrator(self):
        from anima.core.tool_orchestrator import ToolOrchestrator
        from anima.tools.executor import ToolExecutor
        from anima.tools.registry import ToolRegistry
        from anima.models.tool_spec import ToolSpec, RiskLevel

        registry = ToolRegistry()
        # Register some test tools
        for name in ["shell", "read_file", "write_file", "system_info",
                     "search", "github", "email", "spawn_agent",
                     "evolution_propose", "remote_exec"]:
            registry.register(ToolSpec(
                name=name, description=f"Test {name}",
                parameters={"type": "object", "properties": {}},
                risk_level=RiskLevel.SAFE,
                handler=AsyncMock(return_value={"result": "ok"}),
            ))
        executor = ToolExecutor(registry)
        return ToolOrchestrator(executor, registry)

    def test_self_thinking_gets_fewer_tools(self):
        orch = self._make_orchestrator()
        self_tools = orch.get_tool_schemas("SELF_THINKING")
        all_tools = orch.get_tool_schemas("USER_MESSAGE", "帮我写代码")
        # Self-thinking should have fewer tools
        assert len(self_tools) < len(all_tools)
        # Self-thinking should NOT include spawn_agent
        tool_names = {t["name"] for t in self_tools}
        assert "spawn_agent" not in tool_names

    def test_startup_gets_minimal_tools(self):
        orch = self._make_orchestrator()
        startup_tools = orch.get_tool_schemas("STARTUP")
        tool_names = {t["name"] for t in startup_tools}
        assert "system_info" in tool_names
        assert "email" not in tool_names
        assert "github" not in tool_names


# ── ResponseHandler tests ──


class TestResponseHandler:
    """Test ResponseHandler output dispatch."""

    def _make_ctx(self, **overrides):
        from anima.core.context import CognitiveContext
        defaults = {
            "event_queue": MagicMock(),
            "snapshot_cache": MagicMock(),
            "memory_store": MagicMock(),
            "emotion": MagicMock(),
            "llm_router": MagicMock(),
            "tool_executor": MagicMock(),
            "tool_registry": MagicMock(),
            "prompt_compiler": MagicMock(),
        }
        defaults["memory_store"].save_memory_async = AsyncMock()
        defaults["memory_store"].audit_async = AsyncMock()
        defaults.update(overrides)
        return CognitiveContext(**defaults)

    @pytest.mark.asyncio
    async def test_user_response_calls_output(self):
        from anima.core.response_handler import ResponseHandler
        from anima.models.event import Event, EventType
        handler = ResponseHandler()
        output_calls = []
        ctx = self._make_ctx(
            output_callback=lambda text, source="": output_calls.append(text),
        )
        ctx.prompt_compiler.post_process = MagicMock(side_effect=lambda x, **kw: x)
        event = Event(type=EventType.USER_MESSAGE, payload={"text": "hi"})
        await handler.handle(ctx, "Hello!", event=event, user_message="hi")
        assert len(output_calls) == 1
        assert "Hello!" in output_calls[0]

    @pytest.mark.asyncio
    async def test_self_thought_not_output(self):
        from anima.core.response_handler import ResponseHandler
        from anima.models.event import Event, EventType
        handler = ResponseHandler()
        output_calls = []
        ctx = self._make_ctx(
            output_callback=lambda text, source="": output_calls.append(text),
        )
        event = Event(type=EventType.SELF_THINKING, payload={"tick_count": 1})
        await handler.handle(
            ctx, "System is normal", is_self=True,
            event=event, user_message="[INTERNAL]",
        )
        assert len(output_calls) == 0  # Self-thoughts not output


# ── Backward compatibility tests ──


class TestBackwardCompat:
    """Verify AgenticLoop still has the expected interface."""

    def test_agentloop_has_setters(self):
        from anima.core.cognitive import AgenticLoop
        loop = AgenticLoop(
            event_queue=MagicMock(),
            snapshot_cache=MagicMock(),
            memory_store=MagicMock(),
            emotion_state=MagicMock(to_dict=MagicMock(return_value={})),
            llm_router=MagicMock(),
            tool_executor=MagicMock(),
            tool_registry=MagicMock(list_tools=MagicMock(return_value=[])),
            config={},
        )
        # Prompt compiler is set via setter (backward compat)
        loop.set_prompt_compiler(MagicMock())
        # All setters should exist
        assert hasattr(loop, "set_gossip_mesh")
        assert hasattr(loop, "set_prompt_compiler")
        assert hasattr(loop, "set_memory_retriever")
        assert hasattr(loop, "set_conversation_summarizer")
        assert hasattr(loop, "set_stream_callback")
        assert hasattr(loop, "reload_manager")
        assert hasattr(loop, "run")
        assert hasattr(loop, "load_conversation_from_db")

    def test_cognitive_cycle_alias(self):
        from anima.core.cognitive import CognitiveCycle, AgenticLoop
        assert CognitiveCycle is AgenticLoop


# ── Dead code cleanup verification ──


class TestDeadCodeCleanup:
    """Verify dead code has been removed."""

    def test_old_prompts_deleted(self):
        from pathlib import Path
        old = Path(__file__).parent.parent / "anima" / "llm" / "prompts.py"
        assert not old.exists(), "anima/llm/prompts.py should be deleted"

    def test_old_event_router_deleted(self):
        from pathlib import Path
        old = Path(__file__).parent.parent / "anima" / "core" / "event_router.py"
        assert not old.exists(), "anima/core/event_router.py should be deleted"
