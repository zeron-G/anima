"""Evolution API handlers — /v1/evolution/*"""

from __future__ import annotations

from aiohttp import web

from anima.api.auth import check_auth
from anima.utils.logging import get_logger

log = get_logger("api.evolution")


async def status(request: web.Request) -> web.Response:
    """GET /v1/evolution/status."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    if not hub.evolution_engine:
        return web.json_response({"enabled": False})
    return web.json_response(hub.evolution_engine.get_status())


async def history(request: web.Request) -> web.Response:
    """GET /v1/evolution/history — evolution history."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    if not hub.evolution_engine:
        return web.json_response({"successes": [], "failures": []})
    mem = hub.evolution_engine.memory
    return web.json_response({
        "successes": mem.successes[-20:],
        "failures": mem.failures[-20:],
        "goals": mem.goals,
    })


async def governance(request: web.Request) -> web.Response:
    """GET /v1/evolution/governance — governance status."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    from anima.core.governance import get_governance
    gov = get_governance()
    return web.json_response({
        "activity_level": gov.get_activity_level(),
        "drift_scores": gov._drift_scores[-10:],
        "recent_self_thinking": gov._recent_self_thinking_actions[-5:],
    })


async def update_governance_mode(request: web.Request) -> web.Response:
    """PUT /v1/evolution/governance/mode — switch activity level."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    mode = data.get("mode", "")
    if mode not in ("active", "cautious", "minimal"):
        return web.json_response({"error": "mode must be active/cautious/minimal"}, status=400)

    # Update config at runtime
    from anima.config import get_config
    cfg = get_config()
    cfg.setdefault("governance", {})["default_mode"] = mode

    return web.json_response({"success": True, "mode": mode})
