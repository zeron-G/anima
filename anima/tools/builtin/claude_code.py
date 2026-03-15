"""Claude Code tool — delegate complex tasks to Claude Code CLI.

This lets ANIMA invoke Claude Code (the user's installed CLI) to handle
tasks that require deep reasoning, multi-file editing, or complex
problem-solving that would exceed ANIMA's single-turn capabilities.

Claude Code runs as a subprocess with the --print flag for non-interactive use.
"""

from __future__ import annotations

import asyncio
import os

from anima.models.tool_spec import ToolSpec, RiskLevel


async def _claude_code(
    prompt: str,
    working_directory: str = "",
    timeout: int = 120,
) -> dict:
    """Run Claude Code CLI with a prompt and return its output.

    Uses `claude --print` for non-interactive single-turn mode.
    """
    cmd = ["claude", "--print", prompt]

    env = os.environ.copy()
    cwd = working_directory or None

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        stdout_str = stdout.decode("utf-8", errors="replace").strip()
        stderr_str = stderr.decode("utf-8", errors="replace").strip()

        return {
            "returncode": proc.returncode,
            "stdout": stdout_str,
            "stderr": stderr_str,
        }
    except FileNotFoundError:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": "Claude Code CLI not found. Install it with: npm install -g @anthropic-ai/claude-code",
        }
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Claude Code timed out after {timeout}s",
        }


def get_claude_code_tool() -> ToolSpec:
    return ToolSpec(
        name="claude_code",
        description=(
            "Delegate a complex task to Claude Code CLI. "
            "Use this for tasks that need deep reasoning, multi-file analysis, "
            "code generation, debugging, or anything beyond simple tool calls. "
            "Claude Code has full access to the filesystem and can do multi-step work."
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The task description for Claude Code",
                },
                "working_directory": {
                    "type": "string",
                    "description": "Directory to run in (optional, defaults to project root)",
                    "default": "",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 120)",
                    "default": 120,
                },
            },
            "required": ["prompt"],
        },
        risk_level=RiskLevel.MEDIUM,
        handler=_claude_code,
    )
