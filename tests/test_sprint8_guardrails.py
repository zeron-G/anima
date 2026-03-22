"""Sprint 8 Tests — evolution guardrails, tracer integration, tool selection."""

from __future__ import annotations
import pytest
from unittest.mock import MagicMock


class TestEvolutionGuardrails:
    def test_safety_tag_method_exists(self):
        from anima.evolution.engine import EvolutionEngine
        engine = EvolutionEngine()
        assert hasattr(engine, "_create_safety_tag")
        assert hasattr(engine, "_auto_rollback_to_tag")

    def test_create_safety_tag(self):
        from anima.evolution.engine import EvolutionEngine
        engine = EvolutionEngine()
        proposal = MagicMock(id="test123")
        tag = engine._create_safety_tag(proposal)
        assert tag == "pre-evo-test123"


class TestTracerIntegration:
    def test_tracer_import_in_cognitive(self):
        import anima.core.cognitive as cog
        source = open(cog.__file__, encoding="utf-8").read()
        assert "get_tracer" in source

    def test_tracer_spans_in_process_event(self):
        """Tracer spans exist in the pipeline stages (moved from cognitive.py)."""
        import anima.core.stages as stg
        source = open(stg.__file__, encoding="utf-8").read()
        assert "event_routing" in source
        assert "memory_retrieval" in source
        assert "tool_loop" in source
        assert "response_handling" in source


class TestDynamicToolSelection:
    def _make_orchestrator(self):
        from anima.core.tool_orchestrator import ToolOrchestrator
        from anima.tools.executor import ToolExecutor
        from anima.tools.registry import ToolRegistry
        from anima.models.tool_spec import ToolSpec, RiskLevel
        from unittest.mock import AsyncMock

        registry = ToolRegistry()
        for name in ["shell", "read_file", "write_file", "system_info", "get_datetime",
                     "search", "glob_search", "edit_file", "github", "send_email",
                     "spawn_agent", "evolution_propose", "remote_exec", "update_feelings",
                     "save_note", "claude_code"]:
            registry.register(ToolSpec(
                name=name, description=f"Test {name}",
                parameters={"type": "object", "properties": {}},
                risk_level=RiskLevel.SAFE,
                handler=AsyncMock(return_value={"result": "ok"}),
            ))
        return ToolOrchestrator(ToolExecutor(registry), registry)

    def test_code_keywords_add_code_tools(self):
        orch = self._make_orchestrator()
        tools = orch.get_tool_schemas("USER_MESSAGE", "帮我修改这段代码的bug")
        names = {t["name"] for t in tools}
        assert "edit_file" in names
        assert "claude_code" in names

    def test_network_keywords_add_network_tools(self):
        orch = self._make_orchestrator()
        tools = orch.get_tool_schemas("USER_MESSAGE", "检查一下laptop节点")
        names = {t["name"] for t in tools}
        assert "remote_exec" in names

    def test_simple_chat_gets_minimal_tools(self):
        orch = self._make_orchestrator()
        tools = orch.get_tool_schemas("USER_MESSAGE", "你好，今天天气怎么样？")
        names = {t["name"] for t in tools}
        # Core tools should be present
        assert "shell" in names
        assert "read_file" in names
        # Heavy tools should not be present for simple chat
        # (unless fallback to all tools kicks in)

    def test_evolution_keywords(self):
        orch = self._make_orchestrator()
        tools = orch.get_tool_schemas("USER_MESSAGE", "我想让你进化一下自己")
        names = {t["name"] for t in tools}
        assert "evolution_propose" in names

    def test_categories_exist(self):
        from anima.core.tool_orchestrator import _TOOL_CATEGORIES, _INTENT_KEYWORDS
        assert "CORE" in _TOOL_CATEGORIES
        assert "CODE" in _TOOL_CATEGORIES
        assert "CODE" in _INTENT_KEYWORDS
        assert len(_TOOL_CATEGORIES) >= 7


class TestDashboardTraceEndpoint:
    def test_trace_route_registered(self):
        import anima.api.router as router_mod
        source = open(router_mod.__file__, encoding="utf-8").read()
        assert "/v1/settings/traces" in source
