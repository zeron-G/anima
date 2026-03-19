"""GitHub tool — interact with GitHub via the gh CLI."""

from __future__ import annotations

from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.tools.safe_subprocess import split_command, run_safe


async def _github(command: str, timeout: int = 30) -> dict:
    """Run a GitHub CLI (gh) command safely.

    Examples: repo list, issue list --repo owner/repo, pr list
    """
    try:
        cmd = split_command("gh", command)
    except (ValueError, Exception) as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e), "error": str(e)}
    return await run_safe(cmd, tool_name="github", timeout=timeout)


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
