"""Network API handlers — /v1/network/*"""

from __future__ import annotations

from aiohttp import web

from anima.api.auth import check_auth
from anima.api.context import get_hub
from anima.utils.logging import get_logger

log = get_logger("api.network")


async def nodes(request: web.Request) -> web.Response:
    """GET /v1/network/nodes — node list with topology."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = get_hub(request)
    if not hub.gossip_mesh:
        return web.json_response({"enabled": False, "nodes": []})

    peers = hub.gossip_mesh.get_all_states()
    nodes_list = []
    for node_id, state in peers.items():
        nodes_list.append({
            "node_id": node_id,
            "hostname": getattr(state, "hostname", ""),
            "ip": getattr(state, "ip", ""),
            "port": getattr(state, "port", 0),
            "status": getattr(state, "status", "unknown"),
            "current_load": getattr(state, "current_load", 0),
            "emotion": getattr(state, "emotion", {}),
        })

    return web.json_response({
        "enabled": True,
        "alive_count": hub.gossip_mesh.get_alive_count(),
        "nodes": nodes_list,
    })


async def channels(request: web.Request) -> web.Response:
    """GET /v1/network/channels — channel status list."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = get_hub(request)

    channel_list = []
    active = getattr(hub, '_active_channels', {})
    for name, ch in active.items():
        channel_list.append({
            "name": name,
            "connected": ch.is_connected if hasattr(ch, 'is_connected') else False,
            "type": type(ch).__name__,
        })

    return web.json_response({"channels": channel_list})
