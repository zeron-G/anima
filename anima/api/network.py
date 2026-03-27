"""Network API handlers — /v1/network/*"""

from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlparse

from aiohttp import web

from anima.api.auth import check_auth
from anima.api.context import get_hub
from anima.utils.logging import get_logger

log = get_logger("api.network")


def _coerce_transport(value: str) -> str:
    host = str(value or "").strip()
    if not host:
        return "unknown"

    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return "mdns" if "." in host else "hostname"

    if addr.is_loopback:
        return "loopback"
    if addr in ipaddress.ip_network("100.64.0.0/10"):
        return "tailscale"
    if addr.is_private:
        return "lan"
    if addr.is_global:
        return "public"
    return "unknown"


def _transport_label(transport: str) -> str:
    labels = {
        "tailscale": "Tailscale",
        "lan": "LAN",
        "loopback": "Loopback",
        "public": "Public",
        "mdns": "mDNS",
        "hostname": "Hostname",
        "unknown": "Unknown",
    }
    return labels.get(transport, transport.title())


def _extract_host(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    return parsed.hostname or raw.split(":")[0]


def _serialize_robotics_address(url: str, *, active_url: str = "") -> dict:
    host = _extract_host(url)
    transport = _coerce_transport(host)
    return {
        "url": url,
        "host": host,
        "transport": transport,
        "label": _transport_label(transport),
        "active": bool(active_url) and url == active_url,
    }


def _robotics_nodes(hub) -> list[dict]:
    manager = getattr(hub, "robotics_manager", None)
    if not manager:
        return []
    try:
        snapshot = manager.get_snapshot()
    except Exception as exc:
        log.debug("Robotics snapshot unavailable while listing network nodes: %s", exc)
        return []
    return list(snapshot.get("nodes") or [])


def _match_robotics_node(peer_state, robotics_nodes: list[dict]) -> dict | None:
    if not robotics_nodes:
        return None

    peer_node_id = str(getattr(peer_state, "node_id", "") or "")
    peer_host = str(getattr(peer_state, "hostname", "") or "").lower()
    peer_ip = str(getattr(peer_state, "ip", "") or "")
    peer_role = str(getattr(peer_state, "embodiment", "") or "")

    best: tuple[int, dict] | None = None
    for robot in robotics_nodes:
        score = 0
        robot_id = str(robot.get("node_id", "") or "")
        robot_name = str(robot.get("name", "") or "").lower()
        robot_role = str(robot.get("role", "") or "")
        robot_meta = dict(robot.get("metadata") or {})
        robot_tags = {str(tag).lower() for tag in robot.get("tags", [])}
        robot_hosts = {_extract_host(url) for url in robot.get("base_urls", [])}
        robot_hosts = {host for host in robot_hosts if host}

        if peer_node_id and peer_node_id == str(robot_meta.get("anima_node_id", "") or ""):
            score += 8
        if peer_node_id and peer_node_id == robot_id:
            score += 7
        if peer_ip and peer_ip in robot_hosts:
            score += 6
        if peer_host and peer_host in robot_hosts:
            score += 4
        if peer_host and (peer_host == robot_name or peer_host in robot_tags):
            score += 3
        if peer_role == "robot_dog" and robot_role == "robot_dog":
            score += 1

        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, robot)

    if best:
        return best[1]

    if len(robotics_nodes) == 1 and peer_role == "robot_dog":
        return robotics_nodes[0]
    return None


def _serialize_robotics_link(robot: dict | None) -> dict:
    if not robot:
        return {"available": False}

    connected_url = str(robot.get("connected_url", "") or "")
    active_host = _extract_host(connected_url)
    active_transport = _coerce_transport(active_host)
    addresses = [
        _serialize_robotics_address(str(url), active_url=connected_url)
        for url in robot.get("base_urls", [])
    ]
    if connected_url and not any(address["url"] == connected_url for address in addresses):
        addresses.insert(0, _serialize_robotics_address(connected_url, active_url=connected_url))

    return {
        "available": True,
        "node_id": str(robot.get("node_id", "") or ""),
        "name": str(robot.get("name", "") or ""),
        "role": str(robot.get("role", "") or ""),
        "connected": bool(robot.get("connected", False)),
        "connected_url": connected_url,
        "current_transport": active_transport,
        "current_transport_label": _transport_label(active_transport),
        "addresses": addresses,
        "tags": list(robot.get("tags") or []),
        "metadata": dict(robot.get("metadata") or {}),
    }


def _serialize_node(node_id: str, state, robotics_nodes: list[dict], local_node_id: str, has_chat: bool) -> dict:
    host = str(getattr(state, "ip", "") or "")
    transport = _coerce_transport(host)
    matched_robot = _match_robotics_node(state, robotics_nodes)
    return {
        "node_id": node_id,
        "hostname": str(getattr(state, "hostname", "") or ""),
        "agent_name": str(getattr(state, "agent_name", "") or ""),
        "ip": host,
        "port": int(getattr(state, "port", 0) or 0),
        "status": str(getattr(state, "status", "unknown") or "unknown"),
        "current_load": float(getattr(state, "current_load", 0.0) or 0.0),
        "emotion": getattr(state, "emotion", {}) or {},
        "compute_tier": int(getattr(state, "compute_tier", 0) or 0),
        "runtime_profile": str(getattr(state, "runtime_profile", "default") or "default"),
        "runtime_role": str(getattr(state, "runtime_role", "desktop_supervisor") or "desktop_supervisor"),
        "platform_class": str(getattr(state, "platform_class", "") or ""),
        "embodiment": str(getattr(state, "embodiment", "virtual") or "virtual"),
        "labels": list(getattr(state, "labels", []) or []),
        "uptime_s": int(getattr(state, "uptime_s", 0) or 0),
        "active_sessions": list(getattr(state, "active_sessions", []) or []),
        "is_self": node_id == local_node_id,
        "chat_available": has_chat and str(getattr(state, "status", "") or "").lower() != "dead",
        "reachability": {
            "host": host,
            "port": int(getattr(state, "port", 0) or 0),
            "transport": transport,
            "label": _transport_label(transport),
            "address": f"{host}:{int(getattr(state, 'port', 0) or 0)}" if host else "",
        },
        "robotics": _serialize_robotics_link(matched_robot),
    }


def _conversation_hub_methods(hub) -> tuple:
    add_fn = getattr(hub, "add_node_conversation_message", None)
    get_fn = getattr(hub, "get_node_conversation", None)
    if add_fn is None or get_fn is None:
        raise web.HTTPServiceUnavailable(reason="network conversation store unavailable")
    return add_fn, get_fn


def _coerce_reply_text(result) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        if isinstance(result.get("result"), str):
            return result["result"]
        return json.dumps(result, ensure_ascii=False)
    return str(result)


async def nodes(request: web.Request) -> web.Response:
    """GET /v1/network/nodes — node list with topology and chat metadata."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = get_hub(request)
    if not hub.gossip_mesh:
        return web.json_response({"enabled": False, "nodes": []})

    peers = hub.gossip_mesh.get_all_states()
    local_node_id = getattr(getattr(hub.gossip_mesh, "_identity", None), "node_id", "")
    robotics_nodes = _robotics_nodes(hub)
    has_chat = getattr(hub, "task_delegate", None) is not None

    nodes_list = [
        _serialize_node(node_id, state, robotics_nodes, local_node_id, has_chat)
        for node_id, state in peers.items()
    ]
    nodes_list.sort(
        key=lambda item: (
            item["status"] != "alive",
            item["status"] == "dead",
            not item["robotics"].get("available", False),
            item["hostname"] or item["node_id"],
        ),
    )

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
    active = getattr(hub, "_active_channels", {})
    for name, ch in active.items():
        channel_list.append({
            "name": name,
            "connected": ch.is_connected if hasattr(ch, "is_connected") else False,
            "type": type(ch).__name__,
        })

    return web.json_response({"channels": channel_list})


async def conversation(request: web.Request) -> web.Response:
    """GET /v1/network/nodes/{node_id}/conversation — remote chat history."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = get_hub(request)
    _, get_fn = _conversation_hub_methods(hub)

    node_id = request.match_info["node_id"]
    limit = max(1, min(int(request.query.get("limit", "50")), 100))
    return web.json_response({
        "node_id": node_id,
        "messages": get_fn(node_id, limit=limit),
    })


async def chat_node(request: web.Request) -> web.Response:
    """POST /v1/network/nodes/{node_id}/chat — send a message to a remote EVA."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = get_hub(request)
    add_fn, get_fn = _conversation_hub_methods(hub)
    task_delegate = getattr(hub, "task_delegate", None)
    gossip_mesh = getattr(hub, "gossip_mesh", None)
    if task_delegate is None or gossip_mesh is None:
        return web.json_response({"error": "network chat unavailable"}, status=503)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    node_id = request.match_info["node_id"]
    message = str(data.get("message", "") or "").strip()
    timeout = max(5.0, min(float(data.get("timeout", 90) or 90), 300.0))
    if not message:
        return web.json_response({"error": "message required"}, status=400)

    peers = gossip_mesh.get_all_states()
    peer_state = peers.get(node_id)
    if peer_state is None:
        return web.json_response({"error": f"unknown node '{node_id}'"}, status=404)
    if str(getattr(peer_state, "status", "") or "").lower() == "dead":
        return web.json_response({"error": f"node '{node_id}' is offline"}, status=409)

    add_fn(
        node_id,
        "user",
        message,
        transport="gossip",
        node_name=str(getattr(peer_state, "hostname", "") or ""),
    )

    try:
        task_id = await task_delegate.delegate(
            task_type="eva_task",
            payload={"task": message},
            target_node=node_id,
            timeout=timeout,
        )
        result = await task_delegate.wait_result(task_id, timeout=timeout)
        reply = _coerce_reply_text(result.get("result", result) if isinstance(result, dict) else result).strip()
        if not reply:
            reply = "(remote node returned no text)"
        assistant_message = add_fn(
            node_id,
            "assistant",
            reply,
            transport="gossip",
            task_id=task_id,
            node_name=str(getattr(peer_state, "hostname", "") or ""),
        )
        return web.json_response({
            "status": "ok",
            "node_id": node_id,
            "task_id": task_id,
            "reply": reply,
            "message": assistant_message,
            "conversation": get_fn(node_id, limit=50),
        })
    except TimeoutError as exc:
        error_text = f"Timed out waiting for reply from {node_id}: {exc}"
        status = 504
    except Exception as exc:
        error_text = str(exc)
        status = 500

    add_fn(
        node_id,
        "system",
        error_text,
        transport="gossip",
        node_name=str(getattr(peer_state, "hostname", "") or ""),
        error=True,
    )
    return web.json_response({"error": error_text, "node_id": node_id}, status=status)
