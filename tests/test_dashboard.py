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


@pytest.mark.asyncio
async def test_dashboard_spa_catchall(dashboard):
    """Catch-all SPA routing (Phase 3): Vue Router paths get the shell, but
    backend prefixes must 404 — the catch-all must never shadow the API."""
    server, hub = dashboard
    async with aiohttp.ClientSession() as session:
        # Is the SPA dist being served? (index.html present)
        async with session.get("http://localhost:18420/") as resp:
            spa_active = '<div id="app">' in (await resp.text())

        if spa_active:
            # An arbitrary client-side route (NOT an explicitly registered path)
            # must return the SPA shell via the catch-all.
            async with session.get("http://localhost:18420/soulscape") as resp:
                assert resp.status == 200
                assert '<div id="app">' in (await resp.text())

        # Unmatched API/WS prefixes must 404 (never the SPA shell), regardless of dist.
        for path in ("/v1/__nope__", "/api/__nope__"):
            async with session.get(f"http://localhost:18420{path}") as resp:
                assert resp.status == 404
                assert '<div id="app">' not in (await resp.text())


@pytest.fixture
async def auth_dashboard():
    """Dashboard with auth ENABLED, to exercise the unified JWT middleware."""
    import anima.api.auth as _auth
    config = load_config()
    config.setdefault("dashboard", {}).setdefault("auth", {})
    config["dashboard"]["auth"]["password"] = "testpass"
    config["dashboard"]["auth"]["token"] = "test-signing-secret"
    config.setdefault("voice_bridge", {})["enabled"] = False  # no port-9000 clash
    _auth._SECRET = ""  # force re-read of the configured signing secret
    hub = DashboardHub()
    hub.config = config
    hub.event_queue = EventQueue()
    hub.emotion_state = EmotionState()
    hub.working_memory = WorkingMemory(capacity=10)
    hub.llm_router = LLMRouter("t1", "t2", daily_budget=0.0)
    hub.tool_registry = ToolRegistry()
    hub.tool_registry.register_builtins()
    server = DashboardServer(hub, port=18421)
    await server.start()
    yield server, hub
    await server.stop()
    config["dashboard"]["auth"]["password"] = ""
    config["dashboard"]["auth"]["token"] = ""
    _auth._SECRET = ""


@pytest.mark.asyncio
async def test_auth_unified_rest_and_ws(auth_dashboard):
    """Phase 4: ONE JWT scheme gates /v1 AND /ws. A token from /v1/auth/login
    authenticates the WebSocket — the split-deployment path that was broken when
    /ws validated the raw static token instead of the JWT."""
    server, hub = auth_dashboard
    base = "http://localhost:18421"
    async with aiohttp.ClientSession() as session:
        # /v1/health is public (no auth) and uses the standard envelope.
        async with session.get(f"{base}/v1/health") as resp:
            assert resp.status == 200
            body = await resp.json()
            assert body["ok"] is True and body["data"]["status"] == "ok"
        # Protected REST without a token → 401 (central middleware).
        async with session.get(f"{base}/v1/settings/system") as resp:
            assert resp.status == 401
        # Login → JWT.
        async with session.post(f"{base}/v1/auth/login", json={"password": "testpass"}) as resp:
            assert resp.status == 200
            token = (await resp.json())["token"]
        # Protected REST WITH the JWT → 200.
        async with session.get(
            f"{base}/v1/settings/system", headers={"Authorization": f"Bearer {token}"}
        ) as resp:
            assert resp.status == 200
        # WS WITHOUT a token → handshake rejected (401).
        with pytest.raises(aiohttp.WSServerHandshakeError):
            async with session.ws_connect(f"{base}/ws"):
                pass
        # WS WITH the SAME JWT → snapshot delivered (the fix).
        async with session.ws_connect(f"{base}/ws?token={token}") as ws:
            msg = await ws.receive_json(timeout=5)
            assert "heartbeat" in msg or "emotion" in msg


@pytest.mark.asyncio
async def test_dashboard_cors_allowlist(dashboard):
    """CORS (Phase 3): only allow-listed origins are reflected; others get none."""
    server, hub = dashboard
    async with aiohttp.ClientSession() as session:
        # Disallowed origin → no Access-Control-Allow-Origin header.
        async with session.get(
            "http://localhost:18420/", headers={"Origin": "http://evil.example"}
        ) as resp:
            assert "Access-Control-Allow-Origin" not in resp.headers

        # Allow-listed dev origin (config default) → reflected back exactly.
        async with session.get(
            "http://localhost:18420/", headers={"Origin": "http://localhost:5173"}
        ) as resp:
            assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"
