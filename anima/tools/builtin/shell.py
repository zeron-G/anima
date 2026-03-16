"""Shell command execution tool.

Uses subprocess.run in a thread (via asyncio.to_thread) instead of
asyncio.create_subprocess_shell, because WindowsSelectorEventLoopPolicy
(required for ZMQ) does not support async subprocesses on Windows.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

from anima.models.tool_spec import ToolSpec, RiskLevel

_PYTHON_PATH = sys.executable
_EXTRA_PATH = os.path.dirname(_PYTHON_PATH)


def _run_shell_sync(command: str, timeout: int = 30, working_directory: str | None = None) -> dict:
    """Execute a shell command synchronously (runs in thread)."""
    env = os.environ.copy()
    path = env.get("PATH", "")
    if _EXTRA_PATH not in path:
        env["PATH"] = _EXTRA_PATH + os.pathsep + path
    env["PYTHONIOENCODING"] = "utf-8"
    # Ensure 'python' command resolves to our Python, not Windows Store stub
    # Replace 'python ' at start of command with full path
    if command.startswith("python ") or command.startswith("python\n"):
        command = _PYTHON_PATH + command[6:]
    elif command == "python":
        command = _PYTHON_PATH

    # Resolve working directory — default to project root
    cwd = None
    if working_directory:
        cwd = os.path.expandvars(os.path.expanduser(working_directory))
    else:
        from anima.config import project_root
        cwd = str(project_root())
        if not os.path.isdir(cwd):
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": f"working_directory does not exist: {cwd}",
                "error": f"working_directory does not exist: {cwd}",
            }

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
        stdout_str = result.stdout.decode("utf-8", errors="replace").strip()
        stderr_str = result.stderr.decode("utf-8", errors="replace").strip()

        out = {
            "returncode": result.returncode,
            "stdout": stdout_str,
            "stderr": stderr_str,
        }
        if result.returncode != 0:
            out["error"] = f"Command exited with code {result.returncode}"
            if stderr_str:
                out["error"] += f": {stderr_str[:200]}"
        return out
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "stdout": "", "stderr": "Command timed out", "error": "timeout"}
    except Exception as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e), "error": str(e)}


async def _run_shell(command: str, timeout: int = 30, working_directory: str | None = None) -> dict:
    """Execute a shell command (async wrapper, runs in thread)."""
    return await asyncio.get_event_loop().run_in_executor(
        None, _run_shell_sync, command, timeout, working_directory
    )


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
                "working_directory": {"type": "string", "description": "Optional working directory to run the command in"},
            },
            "required": ["command"],
        },
        risk_level=RiskLevel.HIGH,
        handler=_run_shell,
    )
