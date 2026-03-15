"""System resource monitoring — CPU, memory, disk, processes."""

from __future__ import annotations

import platform

import psutil


def sample_system_state() -> dict:
    """Take a snapshot of system resources. Called by script heartbeat."""
    mem = psutil.virtual_memory()
    try:
        disk = psutil.disk_usage("C:\\" if platform.system() == "Windows" else "/")
        disk_percent = disk.percent
    except Exception:
        disk_percent = 0.0

    return {
        "cpu_percent": psutil.cpu_percent(interval=0),
        "memory_percent": mem.percent,
        "memory_available_mb": round(mem.available / (1024 * 1024)),
        "disk_percent": disk_percent,
        "process_count": len(psutil.pids()),
    }
