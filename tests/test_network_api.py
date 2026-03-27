"""Tests for network node discovery and remote node chat APIs."""

from __future__ import annotations

import contextlib

import aiohttp
import pytest
from aiohttp import web

from anima.api.router import APIRouter
from anima.dashboard.hub import DashboardHub


async def _start_app(app: web.Application) -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    assert site._server is not None
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


class DummyIdentity:
    def __init__(self, node_id: str) -> None:
        self.node_id = node_id


class DummyPeerState:
    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class DummyGossipMesh:
    def __init__(self) -> None:
        self._identity = DummyIdentity("anima-desktop")
        self._peers = {
            "anima-pidog": DummyPeerState(
                node_id="anima-pidog",
                hostname="pidog",
                agent_name="eva",
                ip="100.88.10.2",
                port=9420,
                status="alive",
                current_load=0.12,
                emotion={"mood": "curious"},
                compute_tier=3,
                runtime_profile="edge-pidog",
                runtime_role="edge_companion",
                platform_class="linux-arm64",
                embodiment="robot_dog",
                labels=["tailscale", "field"],
                uptime_s=240,
                active_sessions=["session-1"],
            ),
        }

    def get_all_states(self) -> dict[str, DummyPeerState]:
        return dict(self._peers)

    def get_alive_count(self) -> int:
        return 1


class DummyTaskDelegate:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str, float]] = []

    async def delegate(self, task_type: str, payload: dict, target_node: str, timeout: float) -> str:
        self.calls.append((task_type, payload, target_node, timeout))
        return "task-remote-1"

    async def wait_result(self, task_id: str, timeout: float) -> dict:
        assert task_id == "task-remote-1"
        return {"result": "PiDog EVA: 已完成动作，正在观察周围。"}


class DummyRoboticsManager:
    def get_snapshot(self) -> dict:
        return {
            "enabled": True,
            "node_count": 1,
            "nodes": [
                {
                    "node_id": "dog1",
                    "name": "PiDog",
                    "role": "robot_dog",
                    "connected": True,
                    "connected_url": "http://192.168.1.174:8888",
                    "base_urls": [
                        "http://100.88.10.2:8888",
                        "http://192.168.1.174:8888",
                    ],
                    "tags": ["pidog", "field"],
                    "metadata": {"anima_node_id": "anima-pidog"},
                },
            ],
        }


@pytest.mark.asyncio
async def test_network_nodes_and_remote_chat_routes():
    hub = DashboardHub()
    hub.gossip_mesh = DummyGossipMesh()
    hub.task_delegate = DummyTaskDelegate()
    hub.robotics_manager = DummyRoboticsManager()

    app = web.Application()
    APIRouter(hub).register(app)
    runner, base_url = await _start_app(app)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/v1/network/nodes") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["enabled"] is True
                assert data["alive_count"] == 1
                node = data["nodes"][0]
                assert node["node_id"] == "anima-pidog"
                assert node["chat_available"] is True
                assert node["reachability"]["transport"] == "tailscale"
                assert node["robotics"]["available"] is True
                assert node["robotics"]["node_id"] == "dog1"
                assert node["robotics"]["current_transport"] == "lan"
                assert {address["transport"] for address in node["robotics"]["addresses"]} == {"tailscale", "lan"}

            async with session.post(
                f"{base_url}/v1/network/nodes/anima-pidog/chat",
                json={"message": "站起来，然后看看周围", "timeout": 30},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"
                assert "观察周围" in data["reply"]

            async with session.get(
                f"{base_url}/v1/network/nodes/anima-pidog/conversation?limit=10",
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert [message["role"] for message in data["messages"]] == ["user", "assistant"]
                assert data["messages"][0]["content"] == "站起来，然后看看周围"
                assert "观察周围" in data["messages"][1]["content"]

            assert hub.task_delegate.calls == [
                ("eva_task", {"task": "站起来，然后看看周围"}, "anima-pidog", 30.0),
            ]
    finally:
        with contextlib.suppress(Exception):
            await runner.cleanup()
