"""Chat API handlers — /v1/chat/*"""

from __future__ import annotations

import asyncio
import time

from aiohttp import web

from anima.api.auth import check_auth
from anima.models.event import Event, EventType, EventPriority
from anima.utils.ids import gen_id
from anima.utils.logging import get_logger

log = get_logger("api.chat")


async def send(request: web.Request) -> web.Response:
    """POST /v1/chat/send — queue a message, return correlation_id."""
    import traceback as _tb
    try:
        return await _send_impl(request)
    except Exception as e:
        log.error("Chat send error: %s\n%s", e, _tb.format_exc())
        return web.json_response({"error": str(e)}, status=500)

async def _send_impl(request: web.Request) -> web.Response:
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    message = data.get("message", "").strip()
    session_id = data.get("session_id", "api_v1")
    if not message:
        return web.json_response({"error": "message required"}, status=400)

    correlation_id = gen_id("msg")
    if not hub.event_queue:
        return web.json_response({"error": "backend not ready"}, status=503)
    try:
        await hub.event_queue.put(Event(
            type=EventType.USER_MESSAGE,
            payload={"text": message, "correlation_id": correlation_id},
            priority=EventPriority.NORMAL,
            source=session_id,
            id=correlation_id,
        ))
        hub.add_chat_message("user", message)
    except Exception as e:
        log.error("Chat send failed: %s", e)
        return web.json_response({"error": str(e)}, status=500)

    return web.json_response({
        "status": "queued",
        "correlation_id": correlation_id,
    })


async def stream(request: web.Request) -> web.StreamResponse:
    """POST /v1/chat/stream — SSE streaming response."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    message = data.get("message", "").strip()
    if not message:
        return web.json_response({"error": "message required"}, status=400)

    correlation_id = gen_id("msg")
    stream_id = gen_id("stream")
    q: asyncio.Queue = asyncio.Queue()
    hub.register_stream(stream_id, q)

    hub.add_chat_message("user", message)
    await hub.event_queue.put(Event(
        type=EventType.USER_MESSAGE,
        payload={"text": message, "stream_id": stream_id, "correlation_id": correlation_id},
        priority=EventPriority.NORMAL,
        source="api_v1_stream",
        id=correlation_id,
    ))

    resp = web.StreamResponse(
        status=200,
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                 "X-Accel-Buffering": "no"},
    )
    await resp.prepare(request)

    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=120)
            except asyncio.TimeoutError:
                break
            if event is None:
                break
            import json
            await resp.write(f"event: {event.get('type', 'message')}\ndata: {json.dumps(event.get('data', {}), ensure_ascii=False)}\n\n".encode("utf-8"))
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        hub.unregister_stream(stream_id)

    return resp


async def history(request: web.Request) -> web.Response:
    """GET /v1/chat/history — chat history with pagination."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]

    page = int(request.query.get("page", "1"))
    limit = min(int(request.query.get("limit", "50")), 100)
    session_id = request.query.get("session_id", "")

    # Get from memory store
    memories = hub.memory_store.get_recent_memories(limit=limit * page, type="chat")
    start = (page - 1) * limit
    page_items = memories[start:start + limit]

    return web.json_response({
        "messages": [
            {"content": m.get("content", ""), "role": "user" if "user" in str(m.get("metadata_json", "")) else "assistant",
             "timestamp": m.get("created_at", 0)}
            for m in page_items
        ],
        "page": page,
        "limit": limit,
        "total": len(memories),
    })


async def sessions(request: web.Request) -> web.Response:
    """GET /v1/chat/sessions — list sessions."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    if hub.session_manager:
        return web.json_response({"sessions": hub.session_manager.list_sessions()})
    return web.json_response({"sessions": []})


async def golden(request: web.Request) -> web.Response:
    """POST /v1/chat/golden — mark a reply as golden."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    from anima.tools.builtin.memory_tools import _mark_golden_reply
    result = await _mark_golden_reply(
        scene=data.get("scene", "casual"),
        user_text=data.get("user_text", ""),
        eva_reply=data.get("eva_reply", ""),
        score=data.get("score", 0.85),
    )
    return web.json_response(result)
