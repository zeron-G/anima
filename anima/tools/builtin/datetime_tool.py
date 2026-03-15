"""Datetime tool."""

from __future__ import annotations

from datetime import datetime, timezone

from anima.models.tool_spec import ToolSpec, RiskLevel


async def _get_datetime() -> dict:
    """Get current date and time."""
    now = datetime.now()
    utc = datetime.now(timezone.utc)
    return {
        "local": now.isoformat(),
        "utc": utc.isoformat(),
        "timestamp": utc.timestamp(),
    }


def get_datetime_tool() -> ToolSpec:
    return ToolSpec(
        name="get_datetime",
        description="Get the current date and time",
        parameters={"type": "object", "properties": {}},
        risk_level=RiskLevel.SAFE,
        handler=_get_datetime,
    )
