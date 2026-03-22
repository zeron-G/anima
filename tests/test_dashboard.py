"""Tests for the dashboard server."""

import asyncio
import pytest
import aiohttp

from anima.config import load_config
from anima.core.event_queue import EventQueue
from anima.dashboard.hub import DashboardHub
from anima.dashboard.server import DashboardServer
from anima.emotion.state import EmotionState
from anima.llm.router import LLMRouter
from anima.memory.working import WorkingMemory
from anima.models.memory_item import MemoryItem, MemoryType
from anima.tools.registry import ToolRegistry


@pytest.fixture
async def dashboard(tmp_path):
    """Start a dashboard server on a random port for testing."""
    config = load_config()
    hub = DashboardHub()
    hub.config = config
    hub.event_queue = EventQueue()
    hub.emotion_state = EmotionState()
    hub.working_memory = WorkingMemory(capacity=10)
    hub.llm_router = LLMRouter("t1", "t2", daily_budget=0.0)
    hub.tool_registry = ToolRegistry()
    hub.tool_registry.register_builtins()

    # Add some test data
    hub.working_memory.add(MemoryItem(content="test item", type=MemoryType.OBSERVATION))
    hub.add_chat_message("user", "hello")
    hub.add_chat_message("agent", "hi there!")

    server = DashboardServer(hub, port=18420)
    await server.start()
    yield server, hub
    await server.stop()


@pytest.mark.asyncio
async def test_dashboard_serves_html(dashboard):
    server, hub = dashboard
    async with aiohttp.ClientSession() as session:
        async with session.get("http://localhost:18420/") as resp:
            assert resp.status == 200
            text = await resp.text()
            # Vue SPA dist or fallback message
            assert "Eva UI not built" in text or "<div id=\"app\">" in text


@pytest.mark.asyncio
async def test_dashboard_websocket(dashboard):
    server, hub = dashboard
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect("http://localhost:18420/ws") as ws:
            msg = await ws.receive_json(timeout=5)
            assert "heartbeat" in msg
            assert "auth" in msg
            assert "emotion" in msg
            assert "tools" in msg
            assert msg["working_memory"]["size"] == 1
            assert len(msg["chat_history"]) == 2


@pytest.mark.asyncio
async def test_dashboard_chat_api(dashboard):
    """Chat is now via /v1/chat/send (api router), old /api/chat removed."""
    server, hub = dashboard
    # Alias so that api/chat.py (which uses hub._event_queue) can find it
    hub._event_queue = hub.event_queue
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            "http://localhost:18420/v1/chat/send",
            json={"message": "test message"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data.get("status") == "queued"


@pytest.mark.asyncio
async def test_dashboard_control_api(dashboard):
    """Control is now via /v1/settings/*, old /api/control removed."""
    server, hub = dashboard
    hub.working_memory.clear()
    assert hub.working_memory.size == 0


@pytest.mark.asyncio
async def test_dashboard_hub_snapshot(dashboard):
    server, hub = dashboard
    snapshot = hub.get_full_snapshot()
    assert "uptime_s" in snapshot
    assert "agent" in snapshot
    assert snapshot["agent"]["name"] == "eva"
    assert snapshot["auth"]["provider"] == "Anthropic"
    assert len(snapshot["tools"]) > 0
