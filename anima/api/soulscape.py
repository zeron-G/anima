"""Soulscape API handlers — /v1/soulscape/*"""

from __future__ import annotations

import json
import time
from pathlib import Path

from aiohttp import web

from anima.api.auth import check_auth
from anima.config import agent_dir, data_dir
from anima.utils.logging import get_logger

log = get_logger("api.soulscape")


async def emotion(request: web.Request) -> web.Response:
    """GET /v1/soulscape/emotion — current emotion state."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    hub = request.app["hub"]
    return web.json_response(hub.emotion_state.to_dict())


async def persona(request: web.Request) -> web.Response:
    """GET /v1/soulscape/persona — persona_state.yaml values."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    import yaml
    path = agent_dir() / "memory" / "persona_state.yaml"
    if not path.exists():
        return web.json_response({})
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return web.json_response(data)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def update_persona(request: web.Request) -> web.Response:
    """PUT /v1/soulscape/persona — update persona_state values."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    import yaml
    try:
        updates = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    path = agent_dir() / "memory" / "persona_state.yaml"
    current = {}
    if path.exists():
        current = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    for k, v in updates.items():
        if isinstance(v, (int, float)):
            current[k] = max(0.0, min(1.0, float(v)))

    current["last_updated"] = time.strftime("%Y-%m-%d")
    path.write_text(yaml.dump(current, allow_unicode=True), encoding="utf-8")
    return web.json_response({"success": True, "persona": current})


async def personality(request: web.Request) -> web.Response:
    """GET /v1/soulscape/personality — personality.md content."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    path = agent_dir() / "identity" / "personality.md"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return web.json_response({"content": content})


async def update_personality(request: web.Request) -> web.Response:
    """PUT /v1/soulscape/personality — update personality.md."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    content = data.get("content", "")
    if len(content) > 2000:
        return web.json_response({"error": "personality.md exceeds 500 token limit"}, status=400)

    path = agent_dir() / "identity" / "personality.md"
    # Backup
    if path.exists():
        backup = path.with_suffix(".md.bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(content, encoding="utf-8")

    # Log to growth_log
    growth = agent_dir() / "memory" / "growth_log.md"
    entry = f"\n---\n*{time.strftime('%Y-%m-%d %H:%M')}*\n**变化**: personality.md — 手动编辑\n**原因**: 用户通过 Soulscape UI 直接修改\n"
    with open(growth, "a", encoding="utf-8") as f:
        f.write(entry)

    return web.json_response({"success": True})


async def relationship(request: web.Request) -> web.Response:
    """GET /v1/soulscape/relationship — relationship.md content."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    path = agent_dir() / "identity" / "relationship.md"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return web.json_response({"content": content})


async def update_relationship(request: web.Request) -> web.Response:
    """PUT /v1/soulscape/relationship — update relationship.md."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    path = agent_dir() / "identity" / "relationship.md"
    if path.exists():
        backup = path.with_suffix(".md.bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(data.get("content", ""), encoding="utf-8")

    growth = agent_dir() / "memory" / "growth_log.md"
    entry = f"\n---\n*{time.strftime('%Y-%m-%d %H:%M')}*\n**变化**: relationship.md — 手动编辑\n**原因**: 用户通过 Soulscape UI 直接修改\n"
    with open(growth, "a", encoding="utf-8") as f:
        f.write(entry)

    return web.json_response({"success": True})


async def growth_log(request: web.Request) -> web.Response:
    """GET /v1/soulscape/growth-log — growth log entries."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    path = agent_dir() / "memory" / "growth_log.md"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return web.json_response({"content": content})


async def golden_replies(request: web.Request) -> web.Response:
    """GET /v1/soulscape/golden-replies — golden replies list."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    path = agent_dir() / "memory" / "golden_replies.jsonl"
    entries = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return web.json_response({"replies": entries, "count": len(entries)})


async def delete_golden_reply(request: web.Request) -> web.Response:
    """DELETE /v1/soulscape/golden-replies/:id"""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    reply_id = request.match_info.get("id", "")
    path = agent_dir() / "memory" / "golden_replies.jsonl"
    if not path.exists():
        return web.json_response({"error": "not found"}, status=404)

    entries = []
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            try:
                e = json.loads(line)
                if e.get("id") != reply_id:
                    entries.append(e)
            except json.JSONDecodeError:
                pass

    lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
    return web.json_response({"success": True, "remaining": len(entries)})


async def style_rules(request: web.Request) -> web.Response:
    """GET /v1/soulscape/style-rules — style.md content."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    path = agent_dir() / "rules" / "style.md"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return web.json_response({"content": content})


async def update_style_rules(request: web.Request) -> web.Response:
    """PUT /v1/soulscape/style-rules."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    path = agent_dir() / "rules" / "style.md"
    path.write_text(data.get("content", ""), encoding="utf-8")
    return web.json_response({"success": True})


async def boundaries(request: web.Request) -> web.Response:
    """GET /v1/soulscape/boundaries."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    path = agent_dir() / "rules" / "boundaries.md"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return web.json_response({"content": content})


async def drift(request: web.Request) -> web.Response:
    """GET /v1/soulscape/drift — recent drift scores."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    limit = int(request.query.get("limit", "50"))
    path = data_dir() / "logs" / "drift.jsonl"
    entries = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return web.json_response({"entries": entries[-limit:], "total": len(entries)})
