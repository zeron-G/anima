"""Process singleton — ensures only one ANIMA instance runs at a time.

Uses a file lock + PID check to prevent duplicate processes.
Can be bypassed with --experimental flag.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from anima.config import data_dir
from anima.utils.logging import get_logger

log = get_logger("singleton")

LOCK_FILE = data_dir() / "anima.lock"


def acquire_lock(*, experimental: bool = False) -> bool:
    """Try to acquire the singleton lock.

    Returns True if lock acquired, False if another instance is running.
    In experimental mode, always returns True (no lock enforcement).
    """
    if experimental:
        log.info("Experimental mode — singleton lock bypassed")
        return True

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Check if another instance is running
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
            if _is_process_alive(old_pid):
                log.warning("ANIMA already running (PID %d). Use --experimental to bypass.", old_pid)
                return False
            else:
                log.info("Stale lock file found (PID %d dead), reclaiming.", old_pid)
        except (ValueError, OSError):
            log.info("Invalid lock file, reclaiming.")

    # Write our PID
    try:
        LOCK_FILE.write_text(str(os.getpid()))
        log.info("Singleton lock acquired (PID %d)", os.getpid())
        return True
    except OSError as e:
        log.error("Failed to write lock file: %s", e)
        return False


def release_lock() -> None:
    """Release the singleton lock."""
    try:
        if LOCK_FILE.exists():
            pid = int(LOCK_FILE.read_text().strip())
            if pid == os.getpid():
                LOCK_FILE.unlink()
                log.info("Singleton lock released")
    except (ValueError, OSError):
        try:
            LOCK_FILE.unlink(missing_ok=True)
        except OSError as e:
            log.debug("release_lock: %s", e)


def kill_existing() -> bool:
    """Kill any existing ANIMA process and reclaim the lock.

    Returns True if a process was killed.
    """
    if not LOCK_FILE.exists():
        return False

    try:
        old_pid = int(LOCK_FILE.read_text().strip())
        if _is_process_alive(old_pid) and old_pid != os.getpid():
            import signal
            try:
                if sys.platform == "win32":
                    os.kill(old_pid, signal.SIGTERM)
                else:
                    os.kill(old_pid, signal.SIGTERM)
                log.info("Killed existing ANIMA process (PID %d)", old_pid)
                # Wait a moment for cleanup
                import time
                time.sleep(1)
                # Force kill if still alive
                if _is_process_alive(old_pid):
                    os.kill(old_pid, 9)
                    time.sleep(0.5)
                return True
            except (ProcessLookupError, PermissionError) as e:
                log.debug("singleton: %s", e)
    except (ValueError, OSError) as e:
        log.debug("singleton: %s", e)

    return False


def _is_process_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception as e:
            log.debug("_is_process_alive: %s", e)

    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
