"""Tests for the dashboard server."""

import asyncio
import base64
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
    config.setdefault("voice_bridge", {})
    config["voice_bridge"].update({
        "enabled": True,
        "host": "127.0.0.1",
        "port": 19000,
    })
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


@pytest.mark.asyncio
async def test_dashboard_voice_bridge_routes(dashboard, monkeypatch):
    server, hub = dashboard

    async def _fake_synthesize(text: str, *, voice: str = "", use_cache: bool = True) -> bytes:
        assert text == "你好，PiDog"
        assert use_cache is True
        return b"fake-mp3"

    async def _fake_transcribe(audio_path: str, language: str | None = None) -> str:
        assert language == "zh"
        return "坐下"

    monkeypatch.setattr("anima.voice.bridge.synthesize_mp3", _fake_synthesize)
    monkeypatch.setattr("anima.voice.bridge.stt_module.is_available", lambda: True)
    monkeypatch.setattr("anima.voice.bridge.stt_module.transcribe", _fake_transcribe)

    async with aiohttp.ClientSession() as session:
        async with session.get("http://127.0.0.1:19000/health") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "ok"
            assert data["stt_available"] is True

        async with session.post(
            "http://127.0.0.1:19000/tts",
            json={"text": "你好，PiDog", "use_cache": True},
        ) as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("audio/mpeg")
            assert await resp.read() == b"fake-mp3"

        audio_b64 = base64.b64encode(b"RIFFfake-audio-payload" * 20).decode("ascii")
        async with session.post(
            "http://127.0.0.1:19000/stt",
            json={"audio_b64": audio_b64, "language": "zh"},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["text"] == "坐下"

        async with session.post(
            "http://127.0.0.1:19000/task",
            json={"text": "帮我看看周围"},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "ok"

    event = await hub.event_queue.get_timeout(1.0)
    assert event is not None
    assert event.payload["text"] == "[PiDog Voice] 帮我看看周围"
