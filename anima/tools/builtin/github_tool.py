"""GitHub tool — interact with GitHub via the gh CLI."""

from __future__ import annotations

import asyncio

from anima.models.tool_spec import ToolSpec, RiskLevel


def _gh_sync(command: str, timeout: int = 30) -> dict:
    """Run a gh CLI command."""
    import subprocess, os, sys
    env = os.environ.copy()
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")

    try:
        result = subprocess.run(
            f"gh {command}", shell=True, capture_output=True,
            timeout=timeout, env=env,
        )
        out = result.stdout.decode("utf-8", errors="replace").strip()
        err = result.stderr.decode("utf-8", errors="replace").strip()
        return {"returncode": result.returncode, "stdout": out, "stderr": err}
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": "Timed out", "error": "timeout"}
    except Exception as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e), "error": str(e)}


async def _github(command: str, timeout: int = 30) -> dict:
    """Run a GitHub CLI (gh) command.

    Examples:
      repo list
      issue list --repo owner/repo
      pr list --repo owner/repo
      api repos/owner/repo
      release list --repo owner/repo
    """
    return await asyncio.get_event_loop().run_in_executor(None, _gh_sync, command, timeout)


def get_github_tool() -> ToolSpec:
    return ToolSpec(
        name="github",
        description=(
            "Run a GitHub CLI (gh) command. Examples: "
            "'repo list', 'issue list --repo owner/repo', "
            "'pr list', 'api /user', 'release list --repo owner/repo'. "
            "Full gh CLI syntax supported."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "gh CLI command (without 'gh' prefix)"},
                "timeout": {"type": "integer", "default": 30},
            },
            "required": ["command"],
        },
        risk_level=RiskLevel.MEDIUM,
        handler=_github,
    )
