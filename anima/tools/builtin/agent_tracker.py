"""Persistent active-agent tracker — data/workspace/active_agents.json.

Stores in-progress spawn_agent sessions so SELF_THINKING can detect
long-running agents (>60s) and periodically report status to the user.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from anima.utils.logging import get_logger

log = get_logger("agent_tracker")

_STALE_THRESHOLD = 60   # seconds before first report
_RECHECK_INTERVAL = 60  # seconds between re-reports


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
        "last_checked": None,
    }
    _save(data)


def remove(session_id: str) -> None:
    """Called when an agent completes (wait_agent / check_agent done)."""
    data = _load()
    if session_id in data:
        del data[session_id]
        _save(data)


def mark_checked(session_id: str) -> None:
    """Update last_checked timestamp so we don't re-report too soon."""
    data = _load()
    if session_id in data:
        data[session_id]["last_checked"] = time.time()
        _save(data)


def get_overdue_agents() -> list[dict]:
    """Return agents running >STALE_THRESHOLD that haven't been checked in RECHECK_INTERVAL."""
    now = time.time()
    data = _load()
    overdue = []
    for entry in data.values():
        runtime = now - entry.get("started_at", now)
        if runtime < _STALE_THRESHOLD:
            continue
        last_checked = entry.get("last_checked")
        if last_checked and (now - last_checked) < _RECHECK_INTERVAL:
            continue
        overdue.append({**entry, "runtime_s": int(runtime)})
    return overdue
