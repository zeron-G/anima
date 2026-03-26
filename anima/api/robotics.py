"""Robotics API handlers — /v1/robotics/*"""

from __future__ import annotations

from aiohttp import web

from anima.api.auth import check_auth
from anima.api.context import get_hub
from anima.utils.logging import get_logger

log = get_logger("api.robotics")


def _get_manager(request: web.Request):
    hub = get_hub(request)
    manager = getattr(hub, "robotics_manager", None)
    if manager is None:
        raise web.HTTPServiceUnavailable(reason="robotics manager not initialized")
    return manager


async def nodes(request: web.Request) -> web.Response:
    """GET /v1/robotics/nodes — list configured robot dog nodes."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    manager = _get_manager(request)
    if request.query.get("refresh", "1") != "0":
        await manager.refresh_all()
    return web.json_response(manager.get_snapshot())


async def node_detail(request: web.Request) -> web.Response:
    """GET /v1/robotics/nodes/{node_id} — single node detail."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    manager = _get_manager(request)
    node_id = request.match_info["node_id"]
    if request.query.get("refresh", "1") != "0":
        data = await manager.refresh_node(node_id)
    else:
        data = manager.get_node(node_id)
    return web.json_response(data)


async def command(request: web.Request) -> web.Response:
    """POST /v1/robotics/nodes/{node_id}/command — structured command."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    manager = _get_manager(request)
    node_id = request.match_info["node_id"]
    data = await request.json()
    result = await manager.execute_command(
        node_id,
        str(data.get("command", "")),
        dict(data.get("params") or {}),
    )
    return web.json_response(result)


async def nlp(request: web.Request) -> web.Response:
    """POST /v1/robotics/nodes/{node_id}/nlp — natural language command."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    manager = _get_manager(request)
    node_id = request.match_info["node_id"]
    data = await request.json()
    result = await manager.run_nlp(node_id, str(data.get("text", "")))
    return web.json_response(result)


async def speak(request: web.Request) -> web.Response:
    """POST /v1/robotics/nodes/{node_id}/speak — TTS on robot."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    manager = _get_manager(request)
    node_id = request.match_info["node_id"]
    data = await request.json()
    result = await manager.speak(
        node_id,
        str(data.get("text", "")),
        blocking=bool(data.get("blocking", False)),
    )
    return web.json_response(result)


async def start_exploration(request: web.Request) -> web.Response:
    """POST /v1/robotics/nodes/{node_id}/exploration/start."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    manager = _get_manager(request)
    node_id = request.match_info["node_id"]
    data = await request.json() if request.can_read_body else {}
    result = await manager.start_exploration(
        node_id,
        goal=str(data.get("goal", "wander")),
        policy=dict(data.get("policy") or {}),
    )
    return web.json_response(result)


async def stop_exploration(request: web.Request) -> web.Response:
    """POST /v1/robotics/nodes/{node_id}/exploration/stop."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    manager = _get_manager(request)
    node_id = request.match_info["node_id"]
    data = await request.json() if request.can_read_body else {}
    result = await manager.stop_exploration(node_id, reason=str(data.get("reason", "manual_stop")))
    return web.json_response(result)


async def refresh(request: web.Request) -> web.Response:
    """POST /v1/robotics/nodes/{node_id}/refresh."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    manager = _get_manager(request)
    node_id = request.match_info["node_id"]
    data = await manager.refresh_node(node_id)
    return web.json_response(data)
