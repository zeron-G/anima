"""Memory API handlers — /v1/memory/*"""

from __future__ import annotations

from aiohttp import web

from anima.api.auth import check_auth
from anima.utils.logging import get_logger

log = get_logger("api.memory")


async def search(request: web.Request) -> web.Response:
    """GET /v1/memory/search?q=&limit=&type="""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    query = request.query.get("q", "")
    limit = min(int(request.query.get("limit", "10")), 50)
    mem_type = request.query.get("type", None)

    if not query:
        return web.json_response({"error": "q parameter required"}, status=400)

    results = await hub.memory_store.search_memories_async(query=query, type=mem_type, limit=limit)
    clean = [{
        "id": r.get("id", ""),
        "content": r.get("content", "")[:500],
        "type": r.get("type", ""),
        "importance": r.get("importance", 0),
        "created_at": r.get("created_at", 0),
    } for r in results]

    return web.json_response({"results": clean, "count": len(clean)})


async def recent(request: web.Request) -> web.Response:
    """GET /v1/memory/recent?limit=&type="""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    limit = min(int(request.query.get("limit", "10")), 50)
    mem_type = request.query.get("type", None)

    results = await hub.memory_store.get_recent_memories_async(limit=limit, type=mem_type)
    clean = [{
        "id": r.get("id", ""),
        "content": r.get("content", "")[:500],
        "type": r.get("type", ""),
        "importance": r.get("importance", 0),
        "created_at": r.get("created_at", 0),
    } for r in results]

    return web.json_response({"results": clean, "count": len(clean)})


async def stats(request: web.Request) -> web.Response:
    """GET /v1/memory/stats — memory statistics."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]

    import asyncio
    total = await asyncio.to_thread(
        lambda: hub.memory_store._conn.execute(
            "SELECT COUNT(*) FROM episodic_memories"
        ).fetchone()[0]
    )
    by_type = await asyncio.to_thread(
        lambda: dict(hub.memory_store._conn.execute(
            "SELECT type, COUNT(*) FROM episodic_memories GROUP BY type"
        ).fetchall())
    )

    return web.json_response({
        "total": total,
        "by_type": by_type,
    })


async def documents(request: web.Request) -> web.Response:
    """GET /v1/memory/documents — document list."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    from anima.tools.builtin.document_tools import _document_store
    if not _document_store:
        return web.json_response({"documents": []})

    return web.json_response({
        "documents": _document_store.list_documents(),
    })


async def import_document(request: web.Request) -> web.Response:
    """POST /v1/memory/documents/import."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    from anima.tools.builtin.document_tools import _document_store
    if not _document_store:
        return web.json_response({"error": "document store not initialized"}, status=500)

    result = await _document_store.import_document(
        data.get("file_path", ""),
        data.get("description", ""),
    )
    return web.json_response(result)


async def search_documents(request: web.Request) -> web.Response:
    """GET /v1/memory/documents/search?q="""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    query = request.query.get("q", "")
    if not query:
        return web.json_response({"error": "q required"}, status=400)

    from anima.tools.builtin.document_tools import _document_store
    if not _document_store:
        return web.json_response({"results": []})

    results = await _document_store.search(query, n_results=int(request.query.get("limit", "5")))
    return web.json_response({"results": results})


async def delete_document(request: web.Request) -> web.Response:
    """DELETE /v1/memory/documents/:id"""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    doc_id = request.match_info.get("id", "")

    from anima.tools.builtin.document_tools import _document_store
    if not _document_store:
        return web.json_response({"error": "document store not initialized"}, status=500)

    result = await _document_store.delete_document(doc_id)
    return web.json_response(result)
