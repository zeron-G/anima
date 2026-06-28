"""Watchdog — external process-survival LIMB for ANIMA's self-healing system.

This is the irreducible external piece: the in-process Sentinel (anima/guardian)
is the brain, but a brain cannot restart the process it lives in. So this thin
external process owns the one thing nothing in-process can do — relaunch ANIMA
after a hard crash or hang.

Authority is INVERTED from the old watchdog: this limb no longer diagnoses or
fixes code (no `claude -p`, no log-grep "second repair brain"). It only keeps
the process alive and obeys restart requests. All repair DECISIONS belong to the
in-process Sentinel; code repair (when enabled) goes through the reviewed
evolution pipeline, never a blind external `claude -p`.

What it does:
  1. Spawn ANIMA as a subprocess.
  2. Liveness by THREE-signal consensus (never a single point of truth):
       - proc.poll()           — did it exit?
       - heartbeat-file age     — written by the script_hb THREAD (load-immune).
       - /v1/healthz + Sentinel tick — distinguishes "alive but brain frozen".
  3. Hung (consensus) → kill + relaunch. Crash (non-zero exit) → relaunch.
     Clean exit (0, no marker) → stop. Marked exit → relaunch (Sentinel asked).
  4. Restart budget is the SHARED guardian Ledger (one budget across the limb +
     the in-process Fixer), so "max N restarts" means N total. Budget exhausted
     → stay down + alert; never restart-storm.

Usage:
  python -m anima watchdog          # supervise ANIMA's lifecycle
  python -m anima watchdog --dry    # observe only; never kill/restart
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from anima.utils.logging import get_logger

log = get_logger("watchdog")

# Data paths honor ANIMA_HOME / installed mode via the config path API.
from anima.config import data_dir, get, source_tree
_DATA = data_dir()
HEARTBEAT_FILE = _DATA / "watchdog_heartbeat.json"
WATCHDOG_LOG = _DATA / "logs" / "watchdog.log"
PROJECT_ROOT = source_tree() or Path(__file__).parent.parent

# Thresholds
HEARTBEAT_TIMEOUT_S = 120        # script_hb heartbeat older than this → suspect hung
CRASH_COOLDOWN_S = 30            # min wait between relaunches
STARTUP_GRACE_S = 30             # don't run liveness consensus during boot
CHECK_INTERVAL_S = 10            # liveness poll cadence
HUNG_CONSENSUS_CHECKS = 3        # consecutive hung verdicts before killing (~30s)
SENTINEL_FREEZE_CHECKS = 6       # tick frozen this many checks while healthz up (~60s) → brain dead
MAX_RESTARTS = 5                 # fallback budget (config guardian.restart_budget wins)
RESTART_WINDOW_S = 600           # fallback window (config guardian.restart_budget wins)
# A restart marker only suppresses liveness while it is FRESH. A graceful reload
# that takes longer than this is itself a failure — and an in-process reload that
# leaks its marker must never blind the limb forever (CODE_REVIEW P0-4).
RESTART_MARKER_MAX_AGE_S = 120


def _log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"{ts} [watchdog] {msg}"
    print(line)
    try:
        WATCHDOG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:  # noqa: BLE001
        log.debug("_log: %s", e)


# ── heartbeat file (written by the in-process script_hb thread; KEEP the name +
#    _update_heartbeat — anima/core/heartbeat.py imports it) ──
def _update_heartbeat() -> None:
    """Called by ANIMA (script heartbeat) to signal liveness."""
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_FILE.write_text(json.dumps({
            "timestamp": time.time(), "pid": os.getpid(),
        }), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.debug("_update_heartbeat: %s", e)


def _heartbeat_age() -> float | None:
    """Age of the heartbeat file in seconds, or None if absent (still booting)."""
    if not HEARTBEAT_FILE.exists():
        return None
    try:
        data = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
        return time.time() - data.get("timestamp", 0)
    except Exception:  # noqa: BLE001
        return None


def _check_heartbeat() -> bool:
    """Back-compat: True if heartbeat is fresh (or absent → assume booting)."""
    age = _heartbeat_age()
    return age is None or age < HEARTBEAT_TIMEOUT_S


def _healthz(port: int) -> tuple[bool, int | None]:
    """Probe the loopback /v1/healthz. Returns (reachable, sentinel_tick)."""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/v1/healthz", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
            return True, data.get("sentinel_tick")
    except Exception:  # noqa: BLE001 — unreachable is the answer
        return False, None


# ── pure decision logic (unit-tested) ──
def classify_exit(ret: int, marker: dict | None) -> str:
    """Why did the subprocess exit? 'requested_restart' | 'crash' | 'stop'."""
    if marker is not None:
        return "requested_restart"
    if ret == 0:
        return "stop"           # clean exit, nobody asked for a restart → done
    return "crash"


def marker_draining(marker: dict | None, now: float) -> bool:
    """True only while a FRESH restart marker exists. A missing marker, or one
    older than RESTART_MARKER_MAX_AGE_S, is NOT draining — so a leaked marker
    (in-process reload) or a hung reload can still be caught by liveness."""
    if marker is None:
        return False
    return (now - marker.get("ts", 0)) < RESTART_MARKER_MAX_AGE_S


def liveness_verdict(*, hb_age: float | None, healthz_ok: bool,
                     tick_frozen: bool, draining: bool) -> str:
    """'alive' | 'hung' | 'brain_frozen'. Consensus, never a single signal."""
    if draining:
        return "alive"                     # a graceful restart is in progress
    if healthz_ok and tick_frozen:
        return "brain_frozen"              # body answers HTTP but Sentinel tick stuck
    if hb_age is not None and hb_age > HEARTBEAT_TIMEOUT_S and not healthz_ok:
        return "hung"                      # two independent signals agree it's dead
    return "alive"


def _supervise(proc: subprocess.Popen, port: int, dry_run: bool) -> str:
    """Monitor a running ANIMA. Returns the relaunch reason, or 'stop'."""
    from anima.guardian.handoff import consume_restart_marker, read_restart_marker

    started = time.time()
    last_tick: int | None = None
    frozen = 0
    hung = 0

    while True:
        ret = proc.poll()
        if ret is not None:
            reason = classify_exit(ret, consume_restart_marker())
            _log(f"ANIMA exited (code {ret}) → {reason}")
            return reason

        time.sleep(CHECK_INTERVAL_S)
        if time.time() - started < STARTUP_GRACE_S:
            continue  # let it boot before judging liveness

        draining = marker_draining(read_restart_marker(), time.time())
        healthz_ok, tick = _healthz(port)
        if healthz_ok and tick is not None:
            frozen = frozen + 1 if tick == last_tick else 0
            last_tick = tick
        else:
            frozen = 0

        verdict = liveness_verdict(
            hb_age=_heartbeat_age(), healthz_ok=healthz_ok,
            tick_frozen=frozen >= SENTINEL_FREEZE_CHECKS, draining=draining)

        if verdict == "alive":
            hung = 0
            continue

        hung += 1
        _log(f"liveness: {verdict} ({hung}/{HUNG_CONSENSUS_CHECKS}) "
             f"hb_age={_heartbeat_age()} healthz={healthz_ok} frozen={frozen}")
        if hung >= HUNG_CONSENSUS_CHECKS:
            if dry_run:
                _log(f"DRY RUN — would kill + relaunch ({verdict})")
                hung = 0
                continue
            _log(f"killing hung process ({verdict})")
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception as e:  # noqa: BLE001
                log.debug("watchdog kill wait: %s", e)
            return verdict


def run_watchdog(dry_run: bool = False) -> None:
    """Process-survival loop — keep ANIMA alive; obey restart requests."""
    from anima.guardian.handoff import Ledger

    _log("=" * 60)
    _log(f"ANIMA Watchdog (process-survival limb) — dry_run={dry_run}")
    _log(f"Project root: {PROJECT_ROOT}")
    _log("=" * 60)

    python_exe = sys.executable
    port = get("dashboard.port", 8420)
    ledger = Ledger()

    # Single shared budget policy: the in-process Sentinel and this limb read the
    # SAME max/window from config so "max N restarts" has one meaning (CODE_REVIEW
    # guardian P1 — previously 5/600 here vs 3/3600 in the Sentinel).
    _rb = get("guardian.restart_budget", {}) or {}
    max_restarts = int(_rb.get("max", MAX_RESTARTS))
    window_s = int(_rb.get("window_s", RESTART_WINDOW_S))

    while True:
        now = time.time()
        # A persisted DEFEATED verdict (budget previously exhausted, or the
        # Sentinel gave up on the process) means relaunching won't help → stay down.
        if ledger.is_defeated("process"):
            _log("process marked DEFEATED — staying DOWN. Clear the ledger to resume.")
            return
        if not ledger.can_restart(now, max_restarts, window_s):
            _log(f"restart budget exhausted ({max_restarts}/{window_s}s) — "
                 f"staying DOWN, marking DEFEATED. Manual intervention required.")
            ledger.mark_defeated("process")
            return

        _log(f"Starting ANIMA... (python: {python_exe})")
        proc = subprocess.Popen([python_exe, "-m", "anima"], cwd=str(PROJECT_ROOT))
        _log(f"ANIMA started (PID {proc.pid})")

        reason = _supervise(proc, port, dry_run)
        if reason == "stop":
            _log("Clean exit — watchdog stopping.")
            return

        ledger.record_restart(reason, time.time(), window_s)
        count = ledger.restart_count(time.time(), window_s)
        _log(f"Relaunching after '{reason}' ({count}/{max_restarts} in window); "
             f"cooldown {CRASH_COOLDOWN_S}s")
        time.sleep(CRASH_COOLDOWN_S)
