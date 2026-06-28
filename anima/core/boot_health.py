"""Boot self-test, known-good anchor, and post-evolution auto-revert.

Part of the frozen recovery core (anima/guardian/frozen.py) — evolution may not
modify this file, because it is the thing that undoes a bad evolution.

Recovery story (docs/EVOLUTION_SAFETY_DESIGN.md §2):
  - Every *healthy* boot records the current commit as "known-good".
  - After a boot, a fast in-process self-test runs.
  - If the self-test FAILS *and this boot followed an evolution reload*, we
    `git reset --hard <known-good>` and request a clean relaunch — so a bad
    evolution can't wedge the process. A failing *cold* boot is NOT reverted
    (it's more likely an environment problem than a bad evolution).
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from anima.config import data_dir, project_root
from anima.utils.logging import get_logger

log = get_logger("boot_health")


def _known_good_path() -> Path:
    d = data_dir() / ".guardian"
    d.mkdir(parents=True, exist_ok=True)
    return d / "known_good.json"


def _git(*args: str, timeout: int = 15) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["git", *args], cwd=str(project_root()),
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, (r.stdout or "").strip() or (r.stderr or "").strip()
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def current_commit() -> str | None:
    code, out = _git("rev-parse", "HEAD", timeout=10)
    return out if code == 0 and out else None


def get_known_good() -> str | None:
    try:
        data = json.loads(_known_good_path().read_text(encoding="utf-8"))
        sha = str(data.get("commit", "")).strip()
        return sha or None
    except Exception:  # noqa: BLE001 — absent/corrupt → no anchor yet
        return None


def record_known_good() -> None:
    """Anchor the current commit as the last state known to boot healthy."""
    sha = current_commit()
    if not sha:
        return
    try:
        _known_good_path().write_text(
            json.dumps({"commit": sha, "ts": time.time()}), encoding="utf-8")
        log.info("Recorded known-good commit: %s", sha[:12])
    except Exception as e:  # noqa: BLE001
        log.debug("record_known_good: %s", e)


def boot_selftest(*, core: dict, tasks: list, hub) -> tuple[bool, str]:
    """Fast, in-process, NO-network smoke test. Catches evolution breakage that
    survives import/construction but dies at runtime. Returns (ok, detail)."""
    # 1. Critical singletons constructed (cognitive/heartbeat liveness is covered
    #    by the task check below — those objects live outside `core`).
    for key in ("event_queue", "memory_store"):
        if not core.get(key):
            return False, f"missing core component: {key}"

    # 2. None of the critical startup tasks (heartbeat/cognitive/...) died immediately.
    for t in tasks or []:
        if t.done() and not t.cancelled():
            exc = t.exception()
            if exc is not None:
                return False, f"task '{t.get_name()}' died at boot: {exc!r}"

    # 3. LLM router has a primary model configured (structural, no API call).
    try:
        router = getattr(hub, "llm_router", None)
        if router is not None and hasattr(router, "_tier1_model"):
            if not getattr(router, "_tier1_model"):
                return False, "llm router has no primary model configured"
    except Exception as e:  # noqa: BLE001 — never let the self-test itself crash boot
        log.debug("selftest llm check skipped: %s", e)

    return True, "ok"


def revert_to_known_good(reason: str) -> str | None:
    """Hard-reset the working tree to the known-good commit. Returns the SHA on
    success, None if there is no anchor or the reset failed. Does NOT relaunch —
    the caller arranges a clean process exit so the reverted code is re-imported."""
    sha = get_known_good()
    if not sha:
        log.error("auto-revert requested but no known-good anchor exists (%s)", reason)
        return None
    cur = current_commit()
    if cur and cur == sha:
        log.warning("auto-revert: already at known-good %s — not a code regression", sha[:12])
        return None
    code, out = _git("reset", "--hard", sha, timeout=20)
    if code != 0:
        log.error("auto-revert git reset failed: %s", out)
        return None
    log.warning("AUTO-REVERTED to known-good %s (%s)", sha[:12], reason)
    return sha
