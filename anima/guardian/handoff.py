"""Cross-process coordination between the in-process Sentinel and the external
process-survival limb (the slimmed watchdog). Three pieces of shared state, all
written atomically (write-temp + os.replace) under ``<data>/.guardian/``:

  - **sentinel tick token** — Sentinel stamps a monotonically-rising counter each
    loop. The limb reads it: if the process answers /v1/healthz but the tick is
    frozen, the brain is dead while the body lives → force a hard restart.
  - **restart marker** — the in-process side requests a graceful restart by
    writing this (used by the P4 process-restart Fixer); the limb relaunches a
    marked exit instead of treating it as a crash.
  - **Ledger** — a single source of truth for the restart budget + DEFEATED
    set, SHARED by Sentinel and the limb so "max N restarts" means N total, not
    N-each (the design's cross-process single-budget invariant).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from anima.utils.logging import get_logger

log = get_logger("guardian.handoff")

_TICK = "sentinel_tick.json"
_MARKER = "restart.marker"
_LEDGER = "ledger.json"


def guardian_dir() -> Path:
    from anima.config import data_dir
    d = data_dir() / ".guardian"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _atomic_write(path: Path, obj: dict) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)  # atomic on the same filesystem


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing/partial file is just "no data"
        return None


# ── Sentinel tick token ──
def write_sentinel_tick(tick: int) -> None:
    try:
        _atomic_write(guardian_dir() / _TICK,
                      {"tick": tick, "ts": time.time(), "pid": os.getpid()})
    except Exception as e:  # noqa: BLE001 — best effort, never fatal
        log.debug("write_sentinel_tick: %s", e)


def read_sentinel_tick() -> dict | None:
    return _read_json(guardian_dir() / _TICK)


# ── Restart marker (graceful-restart handshake; written by P4 Fixer) ──
def write_restart_marker(reason: str, pid: int | None = None) -> bool:
    try:
        _atomic_write(guardian_dir() / _MARKER, {
            "reason": reason, "ts": time.time(),
            "pid": pid if pid is not None else os.getpid(), "phase": "draining",
        })
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("write_restart_marker failed: %s", e)
        return False


def read_restart_marker() -> dict | None:
    return _read_json(guardian_dir() / _MARKER)


def consume_restart_marker() -> dict | None:
    """Read + archive the marker (archive, not delete, for the audit trail)."""
    p = guardian_dir() / _MARKER
    data = _read_json(p)
    if data is not None:
        try:
            os.replace(p, guardian_dir() / (_MARKER + ".done"))
        except Exception:  # noqa: BLE001
            pass
    return data


class Ledger:
    """Cross-process restart budget + DEFEATED set (single source of truth so the
    limb and the in-process Fixer can't each spend a separate budget)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (guardian_dir() / _LEDGER)

    def _load(self) -> dict:
        return _read_json(self._path) or {"restarts": [], "defeated": []}

    def _save(self, d: dict) -> None:
        try:
            _atomic_write(self._path, d)
        except Exception as e:  # noqa: BLE001
            log.warning("ledger save failed: %s", e)

    # restart budget (sliding window)
    def restart_count(self, now: float, window_s: float) -> int:
        d = self._load()
        return sum(1 for r in d.get("restarts", []) if now - r.get("ts", 0) <= window_s)

    def can_restart(self, now: float, max_n: int, window_s: float) -> bool:
        return self.restart_count(now, window_s) < max_n

    def record_restart(self, reason: str, now: float, window_s: float = 86400) -> None:
        d = self._load()
        restarts = [r for r in d.get("restarts", []) if now - r.get("ts", 0) <= window_s]
        restarts.append({"ts": now, "reason": reason})
        d["restarts"] = restarts[-100:]
        self._save(d)

    # DEFEATED set (persists across process restarts; a defeated component is NOT
    # auto-repaired again until an explicit human/agent reset)
    def mark_defeated(self, component: str) -> None:
        d = self._load()
        if component not in d.get("defeated", []):
            d.setdefault("defeated", []).append(component)
            self._save(d)

    def is_defeated(self, component: str) -> bool:
        return component in self._load().get("defeated", [])

    def clear_defeated(self, component: str | None = None) -> None:
        d = self._load()
        if component is None:
            d["defeated"] = []
        else:
            d["defeated"] = [c for c in d.get("defeated", []) if c != component]
        self._save(d)
