"""Memory API handlers — /v1/memory/*"""

from __future__ import annotations

from aiohttp import web

from anima.api.context import get_hub
from anima.utils.logging import get_logger

log = get_logger("api.memory")


async def search(request: web.Request) -> web.Response:
    """GET /v1/memory/search?q=&limit=&type="""
    hub = get_hub(request)
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
    hub = get_hub(request)
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
    hub = get_hub(request)

    # Go through _db.fetch (the Postgres manager). Aliased columns for dict rows.
    db = hub.memory_store._db
    row = await db.fetch_one("SELECT COUNT(*) AS n FROM episodic_memories")
    total = (row or {}).get("n", 0)
    rows = await db.fetch("SELECT type, COUNT(*) AS c FROM episodic_memories GROUP BY type")
    by_type = {r["type"]: r["c"] for r in rows}

    return web.json_response({
        "total": total,
        "by_type": by_type,
    })


async def documents(request: web.Request) -> web.Response:
    """GET /v1/memory/documents — document list."""

    from anima.tools.builtin.document_tools import _document_store
    if not _document_store:
        return web.json_response({"documents": []})

    return web.json_response({
        "documents": _document_store.list_documents(),
    })


async def import_document(request: web.Request) -> web.Response:
    """POST /v1/memory/documents/import."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    from anima.tools.builtin.document_tools import _document_store
    if not _document_store:
        return web.json_response({"error": "document store not initialized"}, status=503)

    # Confine file_path to the configured import directory. Previously any path
    # was accepted → arbitrary server-side file read (.env, ~/.codex/auth.json,
    # credentials) via this network endpoint, then exfiltrable through
    # documents/search (CODE_REVIEW P0-6).
    from pathlib import Path
    from anima.config import data_dir, get
    from anima.utils.path_safety import validate_path_within
    from anima.utils.errors import PathTraversalBlocked

    raw = (data.get("file_path", "") or "").strip()
    if not raw:
        return web.json_response({"error": "file_path required"}, status=400)
    import_base = Path(get("memory.import_dir", "") or (data_dir() / "imports"))
    import_base.mkdir(parents=True, exist_ok=True)
    try:
        safe_path = validate_path_within(import_base / raw, import_base)
    except PathTraversalBlocked:
        return web.json_response(
            {"error": "file_path must be inside the import directory"}, status=403)
    if not safe_path.is_file():
        return web.json_response(
            {"error": "file not found in import directory"}, status=404)

    result = await _document_store.import_document(
        str(safe_path),
        data.get("description", ""),
    )
    return web.json_response(result)


async def search_documents(request: web.Request) -> web.Response:
    """GET /v1/memory/documents/search?q="""
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
    doc_id = request.match_info.get("id", "")

    from anima.tools.builtin.document_tools import _document_store
    if not _document_store:
        return web.json_response({"error": "document store not initialized"}, status=503)

    result = await _document_store.delete_document(doc_id)
    return web.json_response(result)
