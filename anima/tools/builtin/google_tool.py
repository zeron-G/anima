"""Google Workspace tool — wraps the gog CLI for Gmail, Calendar, Drive, etc.

Requires: gog CLI installed (brew install steipete/tap/gogcli)
Setup: gog auth credentials /path/to/client_secret.json
       gog auth add you@gmail.com --services gmail,calendar,drive

If gog is not installed, returns an error with install instructions.
"""

from __future__ import annotations

import shlex

from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.tools.safe_subprocess import split_command, run_safe
from anima.utils.logging import get_logger

log = get_logger("tools.google")


async def _google(command: str, timeout: int = 30) -> dict:
    """Run a Google Workspace (gog) command safely."""
    # Try native gog first
    try:
        cmd = split_command("gog", command)
        result = await run_safe(cmd, tool_name="google", timeout=timeout)
        if result["returncode"] == 0 or result.get("stdout"):
            return result
    except Exception:
        pass

    # WSL fallback — use shlex.quote to prevent injection in bash -c
    try:
        safe_command = shlex.quote(f"gog {command}")
        cmd = ["wsl", "bash", "-c", safe_command]  # No shell=True
        return await run_safe(cmd, tool_name="google", timeout=timeout)
    except Exception as e:
        return {
            "returncode": -1, "stdout": "",
            "stderr": "gog CLI not available. Install: brew install steipete/tap/gogcli",
            "error": str(e),
        }


def get_google_tool() -> ToolSpec:
    return ToolSpec(
        name="google",
        description=(
            "Run Google Workspace commands via gog CLI. "
            "Supports: gmail search/send, calendar events, drive search, "
            "contacts list, sheets get/update, docs export. "
            "Example: 'gmail search newer_than:7d --max 5'"
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "gog command (without 'gog' prefix)"},
                "timeout": {"type": "integer", "default": 30},
            },
            "required": ["command"],
        },
        risk_level=RiskLevel.MEDIUM,
        handler=_google,
    )
