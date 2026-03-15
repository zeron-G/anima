"""Shell command execution tool."""

from __future__ import annotations

import asyncio
import os
import sys

from anima.models.tool_spec import ToolSpec, RiskLevel

# Auto-detect Python path: prefer conda env, then sys.executable
_PYTHON_PATH = sys.executable
# Also build a PATH that includes the Python directory
_EXTRA_PATH = os.path.dirname(_PYTHON_PATH)


async def _run_shell(command: str, timeout: int = 30) -> dict:
    """Execute a shell command and return output.

    Injects the current Python interpreter's directory into PATH so that
    `python` and `pip` work inside subprocess even if not on system PATH.
    """
    env = os.environ.copy()
    # Ensure python is findable
    path = env.get("PATH", "")
    if _EXTRA_PATH not in path:
        env["PATH"] = _EXTRA_PATH + os.pathsep + path
    # Set UTF-8 encoding for subprocess
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        stdout_str = stdout.decode("utf-8", errors="replace").strip()
        stderr_str = stderr.decode("utf-8", errors="replace").strip()

        result = {
            "returncode": proc.returncode,
            "stdout": stdout_str,
            "stderr": stderr_str,
        }
        # If command failed, include a clear error message
        if proc.returncode != 0:
            result["error"] = f"Command exited with code {proc.returncode}"
            if stderr_str:
                result["error"] += f": {stderr_str[:200]}"
        return result
    except asyncio.TimeoutError:
        proc.kill()
        return {"returncode": -1, "stdout": "", "stderr": "Command timed out", "error": "timeout"}


def get_shell_tool() -> ToolSpec:
    return ToolSpec(
        name="shell",
        description=(
            "Execute a shell command and return stdout/stderr. "
            "Python is available via `python` (no full path needed). "
            "Check returncode: 0=success, nonzero=error."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)", "default": 30},
            },
            "required": ["command"],
        },
        risk_level=RiskLevel.HIGH,
        handler=_run_shell,
    )
