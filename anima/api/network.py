"""Network API handlers — /v1/network/*"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import time
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

from anima.api.auth import check_auth
from anima.api.context import get_hub
from anima.robotics.nlp_supervisor import match_pidog_command_text
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


def _json_safe(value):
    """Best-effort conversion for gossip state fields that may contain custom objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return _json_safe(vars(value))
        except Exception:
            pass
    return str(value)


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


def _robot_dashboard_addresses(robot: dict) -> list[dict]:
    urls: list[dict] = []
    seen: set[str] = set()
    connected_url = str(robot.get("connected_url", "") or "")
    base_urls = [connected_url, *list(robot.get("base_urls") or [])]
    active_host = _extract_host(connected_url)
    for base_url in base_urls:
        host = _extract_host(base_url)
        if not host:
            continue
        dashboard_url = f"http://{host}:8420"
        if dashboard_url in seen:
            continue
        seen.add(dashboard_url)
        transport = _coerce_transport(host)
        urls.append({
            "url": dashboard_url,
            "host": host,
            "transport": transport,
            "label": _transport_label(transport),
            "active": bool(active_host) and host == active_host,
        })
    return urls


def _serialize_node(
    node_id: str,
    state,
    local_node_id: str,
    has_chat: bool,
    matched_robot: dict | None = None,
) -> dict:
    host = str(getattr(state, "ip", "") or "")
    transport = _coerce_transport(host)
    return {
        "node_id": node_id,
        "hostname": str(getattr(state, "hostname", "") or ""),
        "agent_name": str(getattr(state, "agent_name", "") or ""),
        "ip": host,
        "port": int(getattr(state, "port", 0) or 0),
        "status": str(getattr(state, "status", "unknown") or "unknown"),
        "current_load": float(getattr(state, "current_load", 0.0) or 0.0),
        "emotion": _json_safe(getattr(state, "emotion", {}) or {}),
        "compute_tier": int(getattr(state, "compute_tier", 0) or 0),
        "runtime_profile": str(getattr(state, "runtime_profile", "default") or "default"),
        "runtime_role": str(getattr(state, "runtime_role", "desktop_supervisor") or "desktop_supervisor"),
        "platform_class": str(getattr(state, "platform_class", "") or ""),
        "embodiment": str(getattr(state, "embodiment", "virtual") or "virtual"),
        "labels": _json_safe(list(getattr(state, "labels", []) or [])),
        "uptime_s": int(getattr(state, "uptime_s", 0) or 0),
        "active_sessions": _json_safe(list(getattr(state, "active_sessions", []) or [])),
        "is_self": node_id == local_node_id,
        "chat_available": has_chat and str(getattr(state, "status", "") or "").lower() != "dead",
        "bridge_mode": "gossip",
        "reachability": {
            "host": host,
            "port": int(getattr(state, "port", 0) or 0),
            "transport": transport,
            "label": _transport_label(transport),
            "address": f"{host}:{int(getattr(state, 'port', 0) or 0)}" if host else "",
        },
        "robotics": _serialize_robotics_link(matched_robot),
    }


def _serialize_direct_robot_node(robot: dict) -> dict:
    dashboard_addresses = _robot_dashboard_addresses(robot)
    dashboard_address = next((item for item in dashboard_addresses if item["active"]), None) or (dashboard_addresses[0] if dashboard_addresses else None)
    metadata = dict(robot.get("metadata") or {})
    connected = bool(robot.get("connected", False))
    status = "alive" if connected else "dead"
    dashboard_host = dashboard_address["host"] if dashboard_address else ""
    transport = dashboard_address["transport"] if dashboard_address else "unknown"
    name = str(robot.get("name", "") or robot.get("node_id", "") or "robot")
    emotion = str(robot.get("emotion", "") or "")
    return {
        "node_id": f"direct:{str(robot.get('node_id', '') or '').strip()}",
        "hostname": name,
        "agent_name": "eva",
        "ip": dashboard_host,
        "port": 8420 if dashboard_address else 0,
        "status": status,
        "current_load": 0.0,
        "emotion": {"mood_label": emotion} if emotion else {},
        "compute_tier": 3,
        "runtime_profile": str(metadata.get("anima_mode", "direct_http")),
        "runtime_role": str(robot.get("role", "robot_dog") or "robot_dog"),
        "platform_class": str(metadata.get("platform", "linux") or "linux"),
        "embodiment": str(robot.get("role", "robot_dog") or "robot_dog"),
        "labels": _json_safe(list(robot.get("tags") or []) + ["direct_http"]),
        "uptime_s": 0,
        "active_sessions": [],
        "is_self": False,
        "chat_available": connected and bool(dashboard_address),
        "bridge_mode": "direct_http",
        "dashboard_url": dashboard_address["url"] if dashboard_address else "",
        "dashboard_addresses": dashboard_addresses,
        "reachability": {
            "host": dashboard_host,
            "port": 8420 if dashboard_address else 0,
            "transport": transport,
            "label": _transport_label(transport),
            "address": f"{dashboard_host}:8420" if dashboard_host else "",
        },
        "robotics": _serialize_robotics_link(robot),
    }


def _conversation_hub_methods(hub) -> tuple:
    add_fn = getattr(hub, "add_node_conversation_message", None)
    get_fn = getattr(hub, "get_node_conversation", None)
    if add_fn is None or get_fn is None:
        raise web.HTTPServiceUnavailable(reason="network conversation store unavailable")
    return add_fn, get_fn


def _get_mesh_states(hub) -> dict[str, object]:
    mesh = getattr(hub, "gossip_mesh", None)
    if mesh is None:
        return {}

    if hasattr(mesh, "get_all_states"):
        return dict(mesh.get_all_states())

    peers = dict(mesh.get_peers()) if hasattr(mesh, "get_peers") else {}
    local_state = getattr(mesh, "_local_state", None)
    local_node_id = getattr(getattr(mesh, "_identity", None), "node_id", "")
    if local_state is not None and local_node_id and local_node_id not in peers:
        peers[local_node_id] = local_state
    return peers


def _coerce_reply_text(result) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        if isinstance(result.get("result"), str):
            return result["result"]
        return json.dumps(result, ensure_ascii=False)
    return str(result)


def _direct_robot_lookup(hub, peers: dict[str, object]) -> dict[str, dict]:
    robotics_nodes = _robotics_nodes(hub)
    if not robotics_nodes:
        return {}

    matched_robot_ids: set[str] = set()
    for state in peers.values():
        matched_robot = _match_robotics_node(state, robotics_nodes)
        if matched_robot:
            matched_robot_ids.add(str(matched_robot.get("node_id", "") or ""))

    direct_nodes: dict[str, dict] = {}
    for robot in robotics_nodes:
        robot_id = str(robot.get("node_id", "") or "")
        if not robot_id or robot_id in matched_robot_ids:
            continue
        direct_nodes[f"direct:{robot_id}"] = robot
    return direct_nodes


def _looks_like_scan_request(text: str) -> bool:
    lowered = str(text or "").lower()
    keywords = (
        "看看周围",
        "看周围",
        "观察周围",
        "环顾",
        "look around",
        "scan around",
        "check surroundings",
        "看看附近",
    )
    return any(keyword in lowered for keyword in keywords)


def _looks_like_status_request(text: str) -> bool:
    lowered = str(text or "").lower()
    keywords = ("状态", "现在怎么样", "status", "how are you", "what do you see")
    return any(keyword in lowered for keyword in keywords)


def _should_prefer_robotics_fallback(text: str) -> bool:
    if _looks_like_scan_request(text) or _looks_like_status_request(text):
        return True
    return match_pidog_command_text(text) is not None


def _format_robot_snapshot_reply(snapshot: dict, *, prefix: str = "") -> str:
    state = str(snapshot.get("state", "UNKNOWN") or "UNKNOWN")
    emotion = str(snapshot.get("emotion", "unknown") or "unknown")
    perception = dict(snapshot.get("perception") or {})
    distance = float(perception.get("distance_cm", 0.0) or 0.0)
    touch = str(perception.get("touch", "N") or "N")
    obstacle = "有近距离障碍" if perception.get("is_obstacle_near") else "前方暂时没有近距离障碍"
    summary = f"当前状态是 {state}，情绪 {emotion}，前方距离约 {distance:.1f} cm，触摸传感器 {touch}，{obstacle}。"
    return f"{prefix}{summary}".strip()


async def _chat_via_robotics_fallback(hub, robot: dict, message: str) -> dict:
    manager = getattr(hub, "robotics_manager", None)
    robot_id = str(robot.get("node_id", "") or "")
    if manager is None or not robot_id:
        raise RuntimeError("robotics fallback unavailable")

    if _looks_like_scan_request(message):
        await manager.execute_command(robot_id, "look_left", {})
        await manager.execute_command(robot_id, "look_right", {})
        await manager.execute_command(robot_id, "center_head", {})
        snapshot = await manager.refresh_node(robot_id)
        return {
            "reply": _format_robot_snapshot_reply(snapshot, prefix="我刚刚环顾了一圈。"),
            "source": "robotics_scan_fallback",
        }

    if _looks_like_status_request(message):
        snapshot = await manager.refresh_node(robot_id)
        return {
            "reply": _format_robot_snapshot_reply(snapshot, prefix="我汇报一下现在的情况。"),
            "source": "robotics_status_fallback",
        }

    result = await manager.run_nlp(robot_id, message)
    parsed = str(result.get("parsed", "") or result.get("command", "") or "").strip()
    if parsed:
        snapshot = await manager.refresh_node(robot_id)
        return {
            "reply": _format_robot_snapshot_reply(snapshot, prefix=f"我已经执行了 {parsed}。"),
            "source": "robotics_nlp_fallback",
        }

    snapshot = await manager.refresh_node(robot_id)
    return {
        "reply": _format_robot_snapshot_reply(snapshot, prefix="我没有完全听懂，但我先回报当前状态。"),
        "source": "robotics_status_fallback",
    }


async def _chat_direct_robot_node(robot: dict, message: str, timeout: float) -> dict:
    dashboard_addresses = _robot_dashboard_addresses(robot)
    if not dashboard_addresses:
        raise RuntimeError("robot node has no reachable dashboard address")

    client_timeout = aiohttp.ClientTimeout(total=min(timeout + 10, 120))
    last_error = "robot dashboard not reachable"
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        for address in dashboard_addresses:
            base_url = address["url"].rstrip("/")
            try:
                history_before_resp = await session.get(f"{base_url}/v1/chat/history", params={"limit": 12})
                history_before_resp.raise_for_status()
                history_before = await history_before_resp.json()
                baseline_ts = max(
                    (float(item.get("timestamp", 0) or 0) for item in history_before.get("messages", []) if item.get("role") == "assistant"),
                    default=0.0,
                )

                send_resp = await session.post(f"{base_url}/v1/chat/send", json={"message": message, "session_id": "desktop_node_workbench"})
                send_resp.raise_for_status()

                deadline = time.time() + timeout
                while time.time() < deadline:
                    await asyncio.sleep(1.0)
                    history_resp = await session.get(f"{base_url}/v1/chat/history", params={"limit": 16})
                    history_resp.raise_for_status()
                    history = await history_resp.json()
                    for item in history.get("messages", []):
                        if item.get("role") == "assistant" and float(item.get("timestamp", 0) or 0) > baseline_ts:
                            return {
                                "reply": str(item.get("content", "") or ""),
                                "dashboard_url": base_url,
                                "history": history.get("messages", []),
                            }
                last_error = f"timed out waiting for assistant reply from {base_url}"
            except Exception as exc:
                last_error = f"{base_url}: {exc}"
                continue

    raise TimeoutError(last_error)


async def nodes(request: web.Request) -> web.Response:
    """GET /v1/network/nodes — node list with topology and chat metadata."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = get_hub(request)
    if not hub.gossip_mesh:
        return web.json_response({"enabled": False, "nodes": []})

    peers = _get_mesh_states(hub)
    local_node_id = getattr(getattr(hub.gossip_mesh, "_identity", None), "node_id", "")
    robotics_nodes = _robotics_nodes(hub)
    has_chat = getattr(hub, "task_delegate", None) is not None
    direct_nodes = _direct_robot_lookup(hub, peers)

    nodes_list = []
    for node_id, state in peers.items():
        matched_robot = _match_robotics_node(state, robotics_nodes)
        nodes_list.append(_serialize_node(node_id, state, local_node_id, has_chat, matched_robot=matched_robot))
    nodes_list.extend(_serialize_direct_robot_node(robot) for robot in direct_nodes.values())
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

    peers = _get_mesh_states(hub)
    peer_state = peers.get(node_id)
    direct_nodes = _direct_robot_lookup(hub, peers)
    bridge_mode = "gossip"
    node_name = ""
    direct_chat_timeout = min(timeout, 12.0)

    if peer_state is None:
        direct_robot = direct_nodes.get(node_id)
        if direct_robot is None:
            return web.json_response({"error": f"unknown node '{node_id}'"}, status=404)
        bridge_mode = "direct_http"
        node_name = str(direct_robot.get("name", "") or direct_robot.get("node_id", "") or "")
        if not bool(direct_robot.get("connected", False)):
            return web.json_response({"error": f"node '{node_id}' is offline"}, status=409)
        add_fn(node_id, "user", message, transport=bridge_mode, node_name=node_name)
        try:
            if _should_prefer_robotics_fallback(message):
                fallback_result = await _chat_via_robotics_fallback(hub, direct_robot, message)
                reply = str(fallback_result.get("reply", "") or "").strip() or "(robotics fallback returned no text)"
                assistant_message = add_fn(
                    node_id,
                    "assistant",
                    reply,
                    transport="robotics_fallback",
                    node_name=node_name,
                    source=str(fallback_result.get("source", "") or "robotics_fallback"),
                )
                return web.json_response({
                    "status": "ok",
                    "node_id": node_id,
                    "reply": reply,
                    "message": assistant_message,
                    "conversation": get_fn(node_id, limit=50),
                    "bridge_mode": "robotics_fallback",
                })

            direct_result = await _chat_direct_robot_node(direct_robot, message, direct_chat_timeout)
            reply = str(direct_result.get("reply", "") or "").strip() or "(remote node returned no text)"
            assistant_message = add_fn(
                node_id,
                "assistant",
                reply,
                transport=bridge_mode,
                node_name=node_name,
                dashboard_url=direct_result.get("dashboard_url", ""),
            )
            return web.json_response({
                "status": "ok",
                "node_id": node_id,
                "reply": reply,
                "message": assistant_message,
                "conversation": get_fn(node_id, limit=50),
                "bridge_mode": bridge_mode,
            })
        except TimeoutError as exc:
            try:
                fallback_result = await _chat_via_robotics_fallback(hub, direct_robot, message)
                reply = str(fallback_result.get("reply", "") or "").strip() or "(robotics fallback returned no text)"
                assistant_message = add_fn(
                    node_id,
                    "assistant",
                    reply,
                    transport="robotics_fallback",
                    node_name=node_name,
                    source=str(fallback_result.get("source", "") or "robotics_fallback"),
                )
                return web.json_response({
                    "status": "ok",
                    "node_id": node_id,
                    "reply": reply,
                    "message": assistant_message,
                    "conversation": get_fn(node_id, limit=50),
                    "bridge_mode": "robotics_fallback",
                })
            except Exception as fallback_exc:
                error_text = f"Timed out waiting for reply from {node_id}: {exc}; robotics fallback failed: {fallback_exc}"
                status = 504
        except Exception as exc:
            error_text = str(exc)
            status = 500
    else:
        if str(getattr(peer_state, "status", "") or "").lower() == "dead":
            return web.json_response({"error": f"node '{node_id}' is offline"}, status=409)

        node_name = str(getattr(peer_state, "hostname", "") or "")
        add_fn(node_id, "user", message, transport=bridge_mode, node_name=node_name)

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
                transport=bridge_mode,
                task_id=task_id,
                node_name=node_name,
            )
            return web.json_response({
                "status": "ok",
                "node_id": node_id,
                "task_id": task_id,
                "reply": reply,
                "message": assistant_message,
                "conversation": get_fn(node_id, limit=50),
                "bridge_mode": bridge_mode,
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
        transport=bridge_mode,
        node_name=node_name,
        error=True,
    )
    return web.json_response({"error": error_text, "node_id": node_id}, status=status)
