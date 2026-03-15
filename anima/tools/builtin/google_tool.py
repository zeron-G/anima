"""Google Workspace tool — wraps the gog CLI for Gmail, Calendar, Drive, etc.

Requires: gog CLI installed (brew install steipete/tap/gogcli)
Setup: gog auth credentials /path/to/client_secret.json
       gog auth add you@gmail.com --services gmail,calendar,drive

If gog is not installed, returns an error with install instructions.
"""

from __future__ import annotations

import asyncio
import subprocess
import os

from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger

log = get_logger("tools.google")


def _gog_sync(command: str, timeout: int = 30) -> dict:
    """Run a gog CLI command."""
    # Try WSL first (where gog might be installed via brew)
    for cmd_prefix in ["gog", "wsl bash -c 'gog"]:
        try:
            if cmd_prefix.startswith("wsl"):
                full_cmd = f"wsl bash -c 'gog {command}'"
            else:
                full_cmd = f"gog {command}"

            result = subprocess.run(
                full_cmd, shell=True, capture_output=True,
                timeout=timeout, env=os.environ.copy(),
            )
            out = result.stdout.decode("utf-8", errors="replace").strip()
            err = result.stderr.decode("utf-8", errors="replace").strip()

            if result.returncode == 0 or out:
                return {"returncode": result.returncode, "stdout": out, "stderr": err}
        except Exception:
            continue

    return {
        "returncode": -1,
        "stdout": "",
        "stderr": "gog CLI not installed. Install: brew install steipete/tap/gogcli",
        "error": "gog CLI not available",
    }


async def _google(command: str, timeout: int = 30) -> dict:
    """Run a Google Workspace (gog) command.

    Examples:
      gmail search 'newer_than:7d' --max 10
      gmail send --to user@email.com --subject 'Hi' --body 'Hello'
      calendar events primary --from 2026-03-15 --to 2026-03-22
      drive search 'name contains report' --max 5
      contacts list --max 20
    """
    return await asyncio.get_event_loop().run_in_executor(None, _gog_sync, command, timeout)


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
