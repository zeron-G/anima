"""System info tool."""

from __future__ import annotations

import platform

import psutil

from anima.models.tool_spec import ToolSpec, RiskLevel


async def _get_system_info() -> dict:
    """Get system information."""
    return {
        "platform": platform.system(),
        "platform_version": platform.version(),
        "architecture": platform.machine(),
        "hostname": platform.node(),
        "python_version": platform.python_version(),
        "cpu_count": psutil.cpu_count(),
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent": psutil.disk_usage("/").percent if platform.system() != "Windows"
            else psutil.disk_usage("C:\\").percent,
    }


def get_system_info_tool() -> ToolSpec:
    return ToolSpec(
        name="system_info",
        description="Get system information (OS, CPU, memory, disk)",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_get_system_info,
    )
