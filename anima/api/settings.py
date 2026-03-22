"""Settings API handlers — /v1/settings/*"""

from __future__ import annotations

import time

from aiohttp import web

from anima.api.auth import check_auth
from anima.config import get, get_config
from anima.utils.logging import get_logger

log = get_logger("api.settings")


async def config_get(request: web.Request) -> web.Response:
    """GET /v1/settings/config — full config (sanitized)."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    cfg = dict(get_config())
    # Remove sensitive fields
    for key in ["secret", "token", "password", "api_key"]:
        _sanitize_dict(cfg, key)

    return web.json_response(cfg)


def _sanitize_dict(d: dict, key: str) -> None:
    """Recursively mask sensitive keys."""
    for k, v in list(d.items()):
        if isinstance(v, dict):
            _sanitize_dict(v, key)
        elif key in k.lower() and isinstance(v, str) and v:
            d[k] = v[:4] + "***"


async def config_update(request: web.Request) -> web.Response:
    """PUT /v1/settings/config — partial config update."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    # Hot-patch config
    key = data.get("key", "")
    value = data.get("value")
    if not key:
        return web.json_response({"error": "key required"}, status=400)

    hub.update_config(key, value)
    return web.json_response({"success": True, "key": key})


async def skills(request: web.Request) -> web.Response:
    """GET /v1/settings/skills — installed skills."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    if hub.skill_loader:
        return web.json_response({"skills": hub.skill_loader.list_skills()})
    return web.json_response({"skills": []})


async def install_skill(request: web.Request) -> web.Response:
    """POST /v1/settings/skills/install."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    if not hub.skill_loader:
        return web.json_response({"error": "skill loader not initialized"}, status=500)

    result = await hub.skill_loader.install(data.get("source", ""), data.get("name", ""))
    return web.json_response(result)


async def uninstall_skill(request: web.Request) -> web.Response:
    """DELETE /v1/settings/skills/:name."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    name = request.match_info.get("name", "")
    if not hub.skill_loader:
        return web.json_response({"error": "skill loader not initialized"}, status=500)
    result = await hub.skill_loader.uninstall(name)
    return web.json_response(result)


async def system_info(request: web.Request) -> web.Response:
    """GET /v1/settings/system — system information."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    snapshot = hub.get_full_snapshot()
    return web.json_response({
        "version": "0.2.0",
        "uptime_s": snapshot.get("uptime_s", 0),
        "agent_name": get("agent.name", "eva"),
        "python_version": __import__("sys").version,
        "chromadb": True,
    })


async def usage(request: web.Request) -> web.Response:
    """GET /v1/settings/usage — token usage."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    return web.json_response(hub.llm_router.get_usage_stats())


async def restart(request: web.Request) -> web.Response:
    """POST /v1/settings/restart."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    from anima.models.event import Event, EventType, EventPriority
    await hub._event_queue.put(Event(
        type=EventType.SHUTDOWN,
        payload={"restart": True, "reason": "API restart request"},
        priority=EventPriority.CRITICAL,
        source="api_settings",
    ))
    return web.json_response({"status": "restarting"})


async def shutdown(request: web.Request) -> web.Response:
    """POST /v1/settings/shutdown."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    from anima.models.event import Event, EventType, EventPriority
    await hub._event_queue.put(Event(
        type=EventType.SHUTDOWN,
        payload={"reason": "API shutdown request"},
        priority=EventPriority.CRITICAL,
        source="api_settings",
    ))
    return web.json_response({"status": "shutting down"})


async def traces(request: web.Request) -> web.Response:
    """GET /v1/settings/traces — cognitive loop traces."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    from anima.observability.tracer import get_tracer
    tracer = get_tracer()
    return web.json_response({"traces": tracer.get_recent(limit=20)})
