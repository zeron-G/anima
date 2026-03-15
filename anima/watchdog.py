"""Watchdog — external repair loop for ANIMA.

Dual-loop architecture:
  Inner loop: Eva's self-evolution (modify own code every 30min)
  Outer loop: Watchdog monitors ANIMA health, invokes Claude Code on failure

The watchdog is a separate process that:
  1. Spawns ANIMA as a subprocess
  2. Monitors: process alive? heartbeat file fresh? error patterns in logs?
  3. On crash → reads error context → calls `claude -p` to diagnose & fix
  4. On repeated runtime errors → same: Claude Code diagnoses & fixes
  5. After fix → restarts ANIMA

Usage:
  python -m anima watchdog          # Start watchdog (manages ANIMA lifecycle)
  python -m anima watchdog --dry    # Monitor only, don't auto-fix
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

# Project root (one level up from anima/)
PROJECT_ROOT = Path(__file__).parent.parent
LOG_FILE = PROJECT_ROOT / "data" / "logs" / "anima.log"
HEARTBEAT_FILE = PROJECT_ROOT / "data" / "watchdog_heartbeat.json"
WATCHDOG_LOG = PROJECT_ROOT / "data" / "logs" / "watchdog.log"

# Thresholds
HEARTBEAT_TIMEOUT_S = 120       # No heartbeat for 2 min → consider hung
ERROR_WINDOW_S = 300            # Look at errors in last 5 minutes
ERROR_THRESHOLD = 5             # 5+ unique errors in window → trigger repair
CRASH_COOLDOWN_S = 30           # Wait 30s between crash restarts
MAX_CONSECUTIVE_FIXES = 3       # Don't loop forever on unfixable errors
CLAUDE_TIMEOUT_S = 300          # 5 min max for Claude Code to work


def _log(msg: str) -> None:
    """Write to watchdog log + stdout."""
    ts = time.strftime("%H:%M:%S")
    line = f"{ts} [watchdog] {msg}"
    print(line)
    try:
        WATCHDOG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _read_recent_errors(lines: int = 200) -> list[str]:
    """Read recent error lines from ANIMA log."""
    if not LOG_FILE.exists():
        return []
    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
        all_lines = text.splitlines()
        recent = all_lines[-lines:]
        errors = [
            l for l in recent
            if "[ERROR]" in l or "[CRITICAL]" in l or "Traceback" in l
            or "Error:" in l or "Exception:" in l
        ]
        return errors
    except Exception:
        return []


def _extract_crash_context(lines: int = 80) -> str:
    """Extract the last N lines of log for crash context."""
    if not LOG_FILE.exists():
        return "(no log file found)"
    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
        all_lines = text.splitlines()
        return "\n".join(all_lines[-lines:])
    except Exception as e:
        return f"(failed to read log: {e})"


def _detect_error_pattern(window_s: int = ERROR_WINDOW_S) -> dict | None:
    """Detect repeated error patterns in recent log.

    Returns dict with error info if threshold exceeded, None otherwise.
    """
    errors = _read_recent_errors(500)
    if len(errors) < ERROR_THRESHOLD:
        return None

    # Count unique error signatures (first line of each error)
    signatures = Counter()
    for e in errors:
        # Normalize: strip timestamp, keep error type + message
        sig = re.sub(r"^\d{2}:\d{2}:\d{2}\s+", "", e).strip()
        sig = sig[:120]  # Truncate for grouping
        signatures[sig] += 1

    # Find most common error
    most_common, count = signatures.most_common(1)[0]
    if count >= ERROR_THRESHOLD:
        return {
            "signature": most_common,
            "count": count,
            "total_errors": len(errors),
            "unique_patterns": len(signatures),
        }
    return None


def _update_heartbeat() -> None:
    """Called by ANIMA to signal it's alive (via script heartbeat)."""
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_FILE.write_text(json.dumps({
            "timestamp": time.time(),
            "pid": os.getpid(),
        }), encoding="utf-8")
    except Exception:
        pass


def _check_heartbeat() -> bool:
    """Check if ANIMA's heartbeat is fresh."""
    if not HEARTBEAT_FILE.exists():
        return True  # No heartbeat file yet — ANIMA may be starting up
    try:
        data = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
        age = time.time() - data.get("timestamp", 0)
        return age < HEARTBEAT_TIMEOUT_S
    except Exception:
        return True  # Can't read → assume OK


def _invoke_claude_code(prompt: str, max_budget: float = 2.0) -> tuple[bool, str]:
    """Invoke Claude Code CLI to diagnose and fix an issue.

    Returns (success, output).
    """
    _log(f"Invoking Claude Code for repair...")
    _log(f"Prompt: {prompt[:200]}...")

    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "text",
        "--allowedTools", "Read,Edit,Bash,Grep,Glob,Write",
        "--max-budget-usd", str(max_budget),
        "--model", "sonnet",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_S,
            cwd=str(PROJECT_ROOT),
            env={**os.environ, "CLAUDE_CODE_ENTRYPOINT": "watchdog"},
        )
        output = result.stdout.strip()
        if result.returncode == 0:
            _log(f"Claude Code completed successfully ({len(output)} chars)")
            return True, output
        else:
            _log(f"Claude Code failed (exit {result.returncode}): {result.stderr[:200]}")
            return False, result.stderr
    except subprocess.TimeoutExpired:
        _log("Claude Code timed out")
        return False, "Timeout"
    except FileNotFoundError:
        _log("Claude Code CLI not found — install it first")
        return False, "claude CLI not found"
    except Exception as e:
        _log(f"Claude Code invocation failed: {e}")
        return False, str(e)


def _build_crash_prompt(crash_context: str, exit_code: int) -> str:
    """Build a prompt for Claude Code to diagnose a crash."""
    return f"""ANIMA (an autonomous AI agent system) just crashed with exit code {exit_code}.

Your job: diagnose the crash, fix the root cause, run tests, and commit.

## Recent log output (last 80 lines):
```
{crash_context}
```

## Instructions:
1. Read the relevant source files mentioned in the traceback
2. Identify the root cause
3. Fix the issue with minimal, targeted changes
4. Run tests: `D:/data/code/github/anima/.venv/Scripts/python.exe -m pytest tests/ --ignore=tests/test_oauth_live.py --ignore=tests/stress_test.py --tb=short -q`
5. If tests pass, commit: `git add -A && git commit -m "watchdog: fix <brief description>"`
6. Do NOT push to remote — the watchdog will handle that after restart verification

IMPORTANT: Only fix the specific crash. Do not refactor or improve unrelated code.
IMPORTANT: If you can't determine the cause, say so — don't make speculative changes.
"""


def _build_error_pattern_prompt(error_info: dict, log_context: str) -> str:
    """Build a prompt for Claude Code to fix repeated runtime errors."""
    return f"""ANIMA (an autonomous AI agent system) has a recurring runtime error.

Error signature (seen {error_info['count']}x in last 5 minutes):
```
{error_info['signature']}
```

Total errors: {error_info['total_errors']}, unique patterns: {error_info['unique_patterns']}

## Recent log context:
```
{log_context}
```

## Instructions:
1. Read the source files involved in this error
2. Fix the root cause (not just suppress the error)
3. Run tests: `D:/data/code/github/anima/.venv/Scripts/python.exe -m pytest tests/ --ignore=tests/test_oauth_live.py --ignore=tests/stress_test.py --tb=short -q`
4. If tests pass, commit: `git add -A && git commit -m "watchdog: fix <brief description>"`
5. Do NOT push to remote

IMPORTANT: Only fix the specific error pattern. Minimal changes only.
"""


def run_watchdog(dry_run: bool = False) -> None:
    """Main watchdog loop — monitor ANIMA and repair on failure."""
    _log("=" * 60)
    _log("ANIMA Watchdog started")
    _log(f"Project root: {PROJECT_ROOT}")
    _log(f"Dry run: {dry_run}")
    _log("=" * 60)

    python_exe = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
    # Fallback to anaconda env if .venv doesn't exist
    if not Path(python_exe).exists():
        python_exe = r"D:\program\codesupport\anaconda\envs\anima\python.exe"

    consecutive_fixes = 0
    last_crash_time = 0

    while True:
        # Start ANIMA
        _log(f"Starting ANIMA... (python: {python_exe})")
        proc = subprocess.Popen(
            [python_exe, "-m", "anima"],
            cwd=str(PROJECT_ROOT),
        )
        _log(f"ANIMA started (PID {proc.pid})")

        # Monitor loop
        error_check_interval = 60  # Check for error patterns every 60s
        last_error_check = time.time()

        while True:
            # Check if process is still alive
            ret = proc.poll()
            if ret is not None:
                # Process exited
                crash_time = time.time()
                _log(f"ANIMA exited with code {ret}")

                if ret == 0:
                    _log("Clean exit — stopping watchdog")
                    return

                # Crash handling
                if crash_time - last_crash_time < CRASH_COOLDOWN_S:
                    _log(f"Crash too soon after last one — waiting {CRASH_COOLDOWN_S}s")
                    time.sleep(CRASH_COOLDOWN_S)

                last_crash_time = crash_time
                consecutive_fixes += 1

                if consecutive_fixes > MAX_CONSECUTIVE_FIXES:
                    _log(f"Too many consecutive fixes ({consecutive_fixes}) — stopping. Manual intervention needed.")
                    return

                # Try to fix
                crash_context = _extract_crash_context()
                if dry_run:
                    _log("DRY RUN — would invoke Claude Code to fix crash")
                    _log(f"Context:\n{crash_context[-500:]}")
                else:
                    prompt = _build_crash_prompt(crash_context, ret)
                    success, output = _invoke_claude_code(prompt)
                    if success:
                        _log("Fix applied — restarting ANIMA")
                        _log(f"Claude Code output: {output[:300]}")
                    else:
                        _log(f"Fix failed — restarting anyway: {output[:200]}")

                break  # Exit monitor loop → restart ANIMA

            # Periodic error pattern check
            if time.time() - last_error_check > error_check_interval:
                last_error_check = time.time()

                # Check heartbeat
                if not _check_heartbeat():
                    _log("ANIMA heartbeat stale — may be hung")
                    # Don't kill yet, just log. If it's truly hung, it'll crash eventually.

                # Check for repeated errors
                error_info = _detect_error_pattern()
                if error_info:
                    _log(f"Repeated error detected: {error_info['signature'][:100]} "
                         f"({error_info['count']}x)")

                    if not dry_run and consecutive_fixes < MAX_CONSECUTIVE_FIXES:
                        log_context = _extract_crash_context(lines=100)
                        prompt = _build_error_pattern_prompt(error_info, log_context)
                        success, output = _invoke_claude_code(prompt)
                        if success:
                            _log("Runtime fix applied — ANIMA will hot-reload if .py changed")
                            consecutive_fixes += 1
                        # Don't restart — let hot-reload handle it
                    elif dry_run:
                        _log("DRY RUN — would invoke Claude Code for runtime error")

            time.sleep(5)  # Check every 5 seconds

        # Brief pause before restart
        time.sleep(3)
        _log("Restarting ANIMA...")

        # Reset consecutive fix counter if ANIMA ran for > 10 minutes before crashing
        if time.time() - last_crash_time > 600:
            consecutive_fixes = 0
