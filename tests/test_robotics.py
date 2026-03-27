"""Tests for the robotics manager, tools, and REST API."""

from __future__ import annotations

import contextlib

import aiohttp
import pytest
from aiohttp import web

from anima.api.router import APIRouter
from anima.dashboard.hub import DashboardHub
from anima.robotics.exploration import ExplorationController
from anima.robotics.manager import RoboticsManager
from anima.robotics.nlp_supervisor import match_pidog_command_text
from anima.tools.builtin.robotics import get_robotics_tools, set_robotics_manager
from anima.tools.executor import ToolExecutor
from anima.tools.registry import ToolRegistry


def _sample_status(**overrides):
    status = {
        "emotion": "curious",
        "state": "STANDING",
        "distance": 84.0,
        "touch": "N",
        "pitch": 1.5,
        "roll": -0.5,
        "battery": 6.9,
        "is_lifted": False,
        "is_obstacle_near": False,
        "is_obstacle_warn": False,
        "queue_size": 0,
        "timestamp": 123.45,
    }
    status.update(overrides)
    return status


async def _start_app(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


@pytest.fixture
async def pidog_service():
    state = {
        "status": _sample_status(),
        "commands": [],
        "nlp": [],
        "speak": [],
    }

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def status(_request: web.Request) -> web.Response:
        return web.json_response(state["status"])

    async def command(request: web.Request) -> web.Response:
        data = await request.json()
        state["commands"].append(data)
        cmd = data.get("command", "")
        if cmd == "stand":
            state["status"]["state"] = "STANDING"
        elif cmd == "sit":
            state["status"]["state"] = "SITTING"
        elif cmd == "lie":
            state["status"]["state"] = "LYING"
        elif cmd == "emergency_stop":
            state["status"]["state"] = "EMERGENCY"
        elif cmd == "resume":
            state["status"]["state"] = "IDLE"
        elif cmd in {"walk_forward", "walk_backward", "turn_left", "turn_right", "trot"}:
            state["status"]["state"] = "MOVING"
        elif cmd == "stop":
            state["status"]["state"] = "IDLE"
        return web.json_response({"ok": True, "accepted": cmd, "params": data.get("params", {})})

    async def nlp(request: web.Request) -> web.Response:
        data = await request.json()
        state["nlp"].append(data)
        text = str(data.get("text", "") or "").strip().lower()
        if text in {"sit down", "stand up", "lower yourself into a seated pose"}:
            return web.json_response({"parsed": None, "message": "未识别指令"})
        return web.json_response({"ok": True, "text": data.get("text", "")})

    async def speak(request: web.Request) -> web.Response:
        data = await request.json()
        state["speak"].append(data)
        return web.json_response({"ok": True, "text": data.get("text", ""), "blocking": bool(data.get("blocking"))})

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/status", status)
    app.router.add_post("/command", command)
    app.router.add_post("/nlp", nlp)
    app.router.add_post("/speak", speak)

    runner, base_url = await _start_app(app)
    try:
        yield {"state": state, "base_url": base_url}
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_robotics_manager_refresh_command_and_config_merge(pidog_service):
    manager = RoboticsManager.from_config(
        {
            "enabled": True,
            "poll_interval_s": 999.0,
            "exploration": {
                "walk_speed": 61,
                "avoid_distance_cm": 28.0,
            },
            "nodes": [
                {
                    "id": "pidog-eva",
                    "name": "PiDog Eva",
                    "base_urls": ["http://127.0.0.1:9", pidog_service["base_url"]],
                    "exploration": {"turn_speed": 77},
                    "tags": ["lab", "dog"],
                }
            ],
        }
    )

    await manager.start()
    try:
        snapshot = manager.get_snapshot()
        assert snapshot["enabled"] is True
        assert snapshot["node_count"] == 1

        node = manager.get_node("pidog-eva")
        assert node["connected"] is True
        assert node["connected_url"] == pidog_service["base_url"]
        assert node["perception"]["distance_cm"] == 84.0
        assert node["tags"] == ["lab", "dog"]

        explorer = manager._explorers["pidog-eva"]
        assert explorer._config["walk_speed"] == 61
        assert explorer._config["avoid_distance_cm"] == 28.0
        assert explorer._config["turn_speed"] == 77

        result = await manager.execute_command("pidog-eva", "stand", {"speed": 40})
        assert result["ok"] is True
        assert result["base_url"] == pidog_service["base_url"]
        assert pidog_service["state"]["commands"][-1]["command"] == "stand"

        nlp_result = await manager.run_nlp("pidog-eva", "向前走一步")
        speak_result = await manager.speak("pidog-eva", "你好，主人", blocking=True)
        assert nlp_result["text"] == "向前走一步"
        assert speak_result["blocking"] is True
    finally:
        await manager.stop()


def test_supervisor_rule_parser_matches_english_motion():
    parsed = match_pidog_command_text("Please sit down right now.")
    assert parsed is not None
    assert parsed["command"] == "sit"
    assert parsed["confidence"] == 1.0


@pytest.mark.asyncio
async def test_robotics_manager_uses_supervisor_rule_for_english_nlp(pidog_service):
    manager = RoboticsManager.from_config(
        {
            "enabled": True,
            "nodes": [
                {
                    "id": "pidog-eva",
                    "name": "PiDog Eva",
                    "base_urls": [pidog_service["base_url"]],
                }
            ],
        }
    )

    await manager.start()
    try:
        result = await manager.run_nlp("pidog-eva", "sit down")
        assert result["source"] == "supervisor_rule"
        assert result["parsed"] == "sit"
        assert pidog_service["state"]["commands"][-1]["command"] == "sit"
        assert manager.get_node("pidog-eva")["state"] == "SITTING"
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_robotics_manager_uses_supervisor_llm_when_needed(pidog_service, monkeypatch):
    async def _fake_plan(self, text: str, *, node_id: str = "", capabilities: list[str] | None = None):
        return {
            "command": "sit",
            "params": {},
            "confidence": 0.92,
            "reason": f"planned for {text}",
            "model": "codex/gpt-5.3-codex",
        }

    monkeypatch.setattr("anima.robotics.manager.RobotNlpSupervisor.plan", _fake_plan)

    manager = RoboticsManager.from_config(
        {
            "enabled": True,
            "nlp_supervisor": {
                "enabled": True,
                "model": "5.3codex",
                "max_tokens": 320,
                "min_confidence": 0.55,
            },
            "nodes": [
                {
                    "id": "pidog-eva",
                    "name": "PiDog Eva",
                    "base_urls": [pidog_service["base_url"]],
                }
            ],
        }
    )

    await manager.start()
    try:
        result = await manager.run_nlp("pidog-eva", "lower yourself into a seated pose")
        assert result["source"] == "supervisor_llm"
        assert result["parsed"] == "sit"
        assert result["planner"]["model"] == "codex/gpt-5.3-codex"
        assert pidog_service["state"]["commands"][-1]["command"] == "sit"
    finally:
        await manager.stop()


def test_exploration_controller_decision_policy():
    async def _status_getter():
        return _sample_status()

    async def _command_sender(command: str, params: dict | None = None):
        return {"ok": True, "command": command, "params": params or {}}

    controller = ExplorationController(
        "pidog-eva",
        status_getter=_status_getter,
        command_sender=_command_sender,
        config={"avoid_distance_cm": 32.0, "critical_battery_v": 6.0},
    )

    controller._state.tick_count = 1
    command, params, _duration, reason = controller._decide(_sample_status(distance=18.0, is_obstacle_near=True))
    assert command in {"turn_left", "turn_right"}
    assert params["speed"] == 55
    assert "obstacle" in reason

    controller._state.tick_count = 3
    command, params, _duration, reason = controller._decide(_sample_status(distance=18.0, is_obstacle_near=True))
    assert command == "walk_backward"
    assert params["speed"] == 35
    assert "backing off" in reason

    command, _params, _duration, reason = controller._decide(_sample_status(battery=5.8))
    assert command == "sleep_mode"
    assert controller._state.mode == "blocked_low_battery"
    assert "critical battery" in reason


@pytest.mark.asyncio
async def test_robotics_tools_execute_against_manager():
    class DummyManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple, dict]] = []

        async def refresh_node(self, node_id: str) -> dict:
            self.calls.append(("refresh_node", (node_id,), {}))
            return {"node_id": node_id, "connected": True}

        async def refresh_all(self) -> list[dict]:
            self.calls.append(("refresh_all", (), {}))
            return [{"node_id": "dog1"}]

        def get_snapshot(self) -> dict:
            return {"enabled": True, "node_count": 1, "nodes": [{"node_id": "dog1"}]}

        async def execute_command(self, node_id: str, command: str, params: dict) -> dict:
            self.calls.append(("execute_command", (node_id, command, params), {}))
            return {"ok": True, "command": command, "params": params}

        async def run_nlp(self, node_id: str, text: str) -> dict:
            self.calls.append(("run_nlp", (node_id, text), {}))
            return {"ok": True, "text": text}

        async def speak(self, node_id: str, text: str, blocking: bool = False) -> dict:
            self.calls.append(("speak", (node_id, text, blocking), {}))
            return {"ok": True, "text": text, "blocking": blocking}

        async def start_exploration(self, node_id: str, goal: str = "wander", policy: dict | None = None) -> dict:
            self.calls.append(("start_exploration", (node_id, goal, policy or {}), {}))
            return {"running": True, "goal": goal}

        async def stop_exploration(self, node_id: str, reason: str = "manual_stop") -> dict:
            self.calls.append(("stop_exploration", (node_id, reason), {}))
            return {"running": False, "reason": reason}

        def get_node(self, node_id: str) -> dict:
            return {"node_id": node_id, "exploration": {"running": False}}

    manager = DummyManager()
    registry = ToolRegistry()
    for tool in get_robotics_tools():
        registry.register(tool)
    executor = ToolExecutor(registry, max_risk=3)

    set_robotics_manager(manager)
    try:
        result = await executor.execute("robot_dog_status", {"node_id": "dog1"})
        assert result["success"] is True
        assert result["result"]["connected"] is True

        result = await executor.execute(
            "robot_dog_command",
            {"node_id": "dog1", "command": "stand", "params": {"speed": 40}},
        )
        assert result["success"] is True
        assert result["result"]["command"] == "stand"

        result = await executor.execute(
            "robot_dog_exploration",
            {"node_id": "dog1", "action": "start", "goal": "patrol", "policy": {"walk_speed": 50}},
        )
        assert result["success"] is True
        assert result["result"]["goal"] == "patrol"
    finally:
        set_robotics_manager(None)


@pytest.mark.asyncio
async def test_robotics_api_routes():
    class DummyRoboticsManager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple, dict]] = []

        async def refresh_all(self) -> list[dict]:
            self.calls.append(("refresh_all", (), {}))
            return []

        async def refresh_node(self, node_id: str) -> dict:
            self.calls.append(("refresh_node", (node_id,), {}))
            return {"node_id": node_id, "connected": True}

        async def execute_command(self, node_id: str, command: str, params: dict) -> dict:
            self.calls.append(("execute_command", (node_id, command, params), {}))
            return {"ok": True, "command": command, "params": params}

        async def run_nlp(self, node_id: str, text: str) -> dict:
            self.calls.append(("run_nlp", (node_id, text), {}))
            return {"ok": True, "text": text}

        async def speak(self, node_id: str, text: str, blocking: bool = False) -> dict:
            self.calls.append(("speak", (node_id, text, blocking), {}))
            return {"ok": True, "text": text, "blocking": blocking}

        async def start_exploration(self, node_id: str, goal: str = "wander", policy: dict | None = None) -> dict:
            self.calls.append(("start_exploration", (node_id, goal, policy or {}), {}))
            return {"running": True, "goal": goal, "policy": policy or {}}

        async def stop_exploration(self, node_id: str, reason: str = "manual_stop") -> dict:
            self.calls.append(("stop_exploration", (node_id, reason), {}))
            return {"running": False, "reason": reason}

        def get_snapshot(self) -> dict:
            return {"enabled": True, "node_count": 1, "nodes": [{"node_id": "dog1"}]}

        def get_node(self, node_id: str) -> dict:
            return {"node_id": node_id, "connected": True}

    hub = DashboardHub()
    hub.robotics_manager = DummyRoboticsManager()
    app = web.Application()
    APIRouter(hub).register(app)
    runner, base_url = await _start_app(app)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/v1/robotics/nodes?refresh=0") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["enabled"] is True
                assert data["nodes"][0]["node_id"] == "dog1"

            async with session.post(
                f"{base_url}/v1/robotics/nodes/dog1/command",
                json={"command": "stand", "params": {"speed": 40}},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["command"] == "stand"

            async with session.post(
                f"{base_url}/v1/robotics/nodes/dog1/exploration/start",
                json={"goal": "patrol", "policy": {"walk_speed": 55}},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["goal"] == "patrol"
                assert data["policy"]["walk_speed"] == 55

            async with session.post(
                f"{base_url}/v1/robotics/nodes/dog1/exploration/stop",
                json={"reason": "desktop_stop"},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["reason"] == "desktop_stop"
    finally:
        with contextlib.suppress(Exception):
            await runner.cleanup()
