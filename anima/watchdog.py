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


_claude_available: bool | None = None  # Cached check result


def _check_claude_cli() -> bool:
    """Check if Claude Code CLI is available. Cached after first check."""
    global _claude_available
    if _claude_available is not None:
        return _claude_available

    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        _claude_available = result.returncode == 0
        if _claude_available:
            _log(f"Claude Code CLI available: {result.stdout.strip()}")
        else:
            _log("Claude Code CLI found but returned error")
    except FileNotFoundError:
        _claude_available = False
        _log("Claude Code CLI not installed — watchdog will only do restarts, no auto-diagnosis")
    except Exception as e:
        _claude_available = False
        _log(f"Claude Code CLI check failed: {e}")

    return _claude_available


def _invoke_claude_code(prompt: str, max_budget: float = 2.0) -> tuple[bool, str]:
    """Invoke Claude Code CLI to diagnose and fix an issue.

    Returns (success, output). Returns (False, "unavailable") if CLI not installed.
    """
    if not _check_claude_cli():
        _log("Skipping Claude Code (not available) — restart only")
        return False, "Claude Code CLI not available"

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


def _post_startup_health_check() -> str | None:
    """Check ANIMA subsystems after startup. Returns error description or None if healthy."""
    if not LOG_FILE.exists():
        return "No log file — ANIMA may not have started"

    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
        # Get only lines from the most recent startup
        lines = text.splitlines()
        # Find the last "ANIMA starting..." line
        start_idx = 0
        for i in range(len(lines) - 1, -1, -1):
            if "ANIMA starting..." in lines[i]:
                start_idx = i
                break
        recent = lines[start_idx:]
        recent_text = "\n".join(recent)

        issues = []

        # Check Discord
        if "Discord connected as" not in recent_text:
            if "discord.py not installed" in recent_text:
                issues.append("Discord: discord.py package not installed")
            elif "Discord thread starting" in recent_text:
                issues.append("Discord: thread started but never connected (token invalid?)")
            else:
                issues.append("Discord: not started at all")

        # Check network/gossip
        if "Gossip mesh started" not in recent_text:
            issues.append("Network: gossip mesh not started")
        elif "Node discovered" not in recent_text:
            # Not critical — peer might be offline
            pass

        # Check cognitive loop
        if "Agentic loop started" not in recent_text:
            issues.append("Cognitive: agentic loop not started")

        # Check heartbeat
        if "Heartbeat engine started" not in recent_text:
            issues.append("Heartbeat: engine not started")

        # Check for startup errors
        error_lines = [l for l in recent if "[ERROR]" in l or "[CRITICAL]" in l]
        if error_lines:
            issues.append(f"Startup errors ({len(error_lines)}): {error_lines[0]}")

        # Check for import errors
        import_errors = [l for l in recent if "ModuleNotFoundError" in l or "ImportError" in l]
        if import_errors:
            issues.append(f"Missing module: {import_errors[0]}")

        return "; ".join(issues) if issues else None

    except Exception as e:
        return f"Health check failed: {e}"


def _build_health_check_prompt(health_issues: str, log_context: str) -> str:
    """Build a prompt to fix post-startup health issues."""
    return f"""ANIMA (an autonomous AI agent system) started but has health issues:

{health_issues}

## Recent log:
```
{log_context}
```

## Instructions:
1. Diagnose why the subsystem(s) failed to start
2. Common fixes:
   - Missing package: `python -m pip install <package>`
   - Import error: check the module exists, fix typos
   - Config issue: check config/default.yaml
3. Fix the root cause
4. Run tests: `.venv/Scripts/python.exe -m pytest tests/ --ignore=tests/test_oauth_live.py --ignore=tests/stress_test.py --tb=short -q`
5. If tests pass, commit: `git add <files> && git commit -m "watchdog: fix <description>"`

IMPORTANT: Only fix the specific health issue. Minimal changes.
"""


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
4. Run tests: `python -m pytest tests/ --tb=short -q`
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
3. Run tests: `python -m pytest tests/ --tb=short -q`
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

    # Use the same Python that's running the watchdog
    python_exe = sys.executable

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

        # Post-startup health check (wait for boot, then verify subsystems)
        _log("Waiting 30s for startup, then running health check...")
        time.sleep(30)
        if proc.poll() is None:  # Still alive
            health = _post_startup_health_check()
            if health:
                _log(f"Health check FAILED: {health}")
                if not dry_run:
                    # Try to fix the startup issue
                    log_context = _extract_crash_context(lines=60)
                    prompt = _build_health_check_prompt(health, log_context)
                    success, output = _invoke_claude_code(prompt)
                    if success:
                        _log(f"Health fix applied: {output[:200]}")
                        # Kill and restart to apply fix
                        proc.terminate()
                        try:
                            proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                        consecutive_fixes += 1
                        if consecutive_fixes > MAX_CONSECUTIVE_FIXES:
                            _log("Too many consecutive fixes — stopping")
                            return
                        time.sleep(3)
                        continue  # Restart loop
            else:
                _log("Health check PASSED — all subsystems OK")
                consecutive_fixes = 0  # Reset on healthy start

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

                # Check heartbeat — kill if stale for 3+ consecutive checks (3 min)
                if not _check_heartbeat():
                    stale_count = getattr(run_watchdog, '_stale_count', 0) + 1
                    run_watchdog._stale_count = stale_count
                    _log(f"ANIMA heartbeat stale ({stale_count}/3)")
                    if stale_count >= 3:
                        _log("Heartbeat stale for 3+ minutes — killing hung process")
                        proc.kill()
                        try:
                            proc.wait(timeout=10)
                        except Exception:
                            pass
                        run_watchdog._stale_count = 0
                        break  # Exit monitor loop → restart
                else:
                    run_watchdog._stale_count = 0

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
