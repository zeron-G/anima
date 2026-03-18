"""Persistent active-agent tracker — data/workspace/active_agents.json.

Stores in-progress spawn_agent sessions so SELF_THINKING can detect
long-running agents (>90s) and notify the user exactly once.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from anima.utils.logging import get_logger

log = get_logger("agent_tracker")

_STALE_THRESHOLD = 60  # seconds before notifying user


def _path() -> Path:
    from anima.config import data_dir
    p = data_dir() / "workspace" / "active_agents.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        _path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("agent_tracker save failed: %s", e)


def register(session_id: str, task_summary: str) -> None:
    """Called by spawn_agent after a session is created."""
    data = _load()
    data[session_id] = {
        "session_id": session_id,
        "task_summary": task_summary[:200],
        "started_at": time.time(),
        "notified": False,
    }
    _save(data)


def remove(session_id: str) -> None:
    """Called when an agent completes (wait_agent / check_agent done)."""
    data = _load()
    if session_id in data:
        del data[session_id]
        _save(data)


def mark_notified(session_id: str) -> None:
    """Mark so we don't notify again on the next heartbeat."""
    data = _load()
    if session_id in data:
        data[session_id]["notified"] = True
        _save(data)


def get_stale_unnotified() -> list[dict]:
    """Return agents running >STALE_THRESHOLD seconds that haven't been notified yet."""
    now = time.time()
    data = _load()
    stale = []
    for entry in data.values():
        if entry.get("notified"):
            continue
        runtime = now - entry.get("started_at", now)
        if runtime >= _STALE_THRESHOLD:
            stale.append({**entry, "runtime_s": int(runtime)})
    return stale
