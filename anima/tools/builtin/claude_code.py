"""Claude Code tool — Eva's external repair & delegation loop.

Two use cases:
  1. General delegation: Eva delegates complex tasks to Claude Code
  2. Self-repair: Eva detects issues she can't fix → calls Claude Code to repair

Claude Code runs as a subprocess via `claude -p` (non-interactive).
Uses subprocess.run in thread (not asyncio.create_subprocess) because
WindowsSelectorEventLoopPolicy breaks async subprocesses.

This is the "左脚踩右脚" (bootstrapping) pattern:
  Eva detects problem → calls Claude Code → Claude Code fixes Eva's code
  → Eva hot-reloads → continues running
"""

from __future__ import annotations

import asyncio
import os
import subprocess

from anima.config import project_root
from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger

log = get_logger("tools.claude_code")

CLAUDE_TIMEOUT_S = 300  # 5 min max


def _run_sync(prompt: str, working_directory: str = "", timeout: int = 120, max_budget: float = 2.0) -> dict:
    """Run Claude Code CLI synchronously (called in thread via run_in_executor)."""
    cwd = working_directory or str(project_root())

    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "text",
        "--allowedTools", "Read,Edit,Bash,Grep,Glob,Write",
        "--max-budget-usd", str(max_budget),
        "--model", "sonnet",
        "--no-session-persistence",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=min(timeout, CLAUDE_TIMEOUT_S),
            cwd=cwd,
            env={**os.environ, "CLAUDE_CODE_ENTRYPOINT": "eva_tool"},
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode == 0:
            log.info("Claude Code completed (%d chars output)", len(stdout))
            return {
                "success": True,
                "output": stdout[:3000],
                "returncode": 0,
            }
        else:
            log.warning("Claude Code failed (exit %d)", result.returncode)
            return {
                "success": False,
                "output": stdout[:1000],
                "error": stderr[:500],
                "returncode": result.returncode,
            }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Claude Code timed out after {timeout}s", "returncode": -1}
    except FileNotFoundError:
        return {"success": False, "error": "claude CLI not found on this system", "returncode": -1}
    except Exception as e:
        return {"success": False, "error": str(e), "returncode": -1}


async def _claude_code(prompt: str, working_directory: str = "", timeout: int = 120) -> dict:
    """Delegate a task to Claude Code CLI.

    Claude Code runs as a separate process with full filesystem access.
    Use for complex multi-file tasks, deep analysis, or debugging.
    """
    return await asyncio.get_event_loop().run_in_executor(
        None, _run_sync, prompt, working_directory, timeout, 2.0
    )


async def _self_repair(error_description: str, max_budget: float = 1.5) -> dict:
    """Invoke Claude Code to repair your own code.

    Use when you encounter an error you can't fix from within your own tools.
    Claude Code will read source files, diagnose, fix, run tests, and commit.
    Your hot-reload system will pick up the changes automatically.

    Be specific: include error messages, tracebacks, file paths, and what you tried.
    """
    root = str(project_root())
    full_prompt = f"""You are being invoked by Eva (ANIMA's AI agent) to fix an issue in her own codebase.

Project: ANIMA — a heartbeat-driven autonomous AI agent system
Location: {root}
Test command: .venv/Scripts/python.exe -m pytest tests/ --ignore=tests/test_oauth_live.py --ignore=tests/stress_test.py --tb=short -q

Eva's error report:
{error_description}

Instructions:
1. Read the relevant source files mentioned in the error
2. Diagnose the root cause
3. Fix with minimal, targeted changes
4. Run the test command above
5. If tests pass, commit: `git add <specific files> && git commit -m "eva-repair: <brief description>"`
6. Do NOT push to remote — Eva's hot-reload will pick up the changes

IMPORTANT: Only fix the specific issue. Do not refactor or improve unrelated code.
"""
    return await asyncio.get_event_loop().run_in_executor(
        None, _run_sync, full_prompt, root, 300, max_budget
    )


def get_claude_code_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="claude_code",
            description=(
                "Delegate a complex task to Claude Code CLI. "
                "Use for tasks needing deep reasoning, multi-file analysis, "
                "code generation, or debugging beyond simple tool calls. "
                "Claude Code has full filesystem access and can do multi-step work."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Task description for Claude Code",
                    },
                    "working_directory": {
                        "type": "string",
                        "description": "Directory to run in (default: project root)",
                        "default": "",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 120, max 300)",
                        "default": 120,
                    },
                },
                "required": ["prompt"],
            },
            risk_level=RiskLevel.MEDIUM,
            handler=_claude_code,
        ),
        ToolSpec(
            name="self_repair",
            description=(
                "Invoke Claude Code to repair your own code when you encounter "
                "an error you can't fix yourself. Claude Code will read your source "
                "files, diagnose the issue, apply a fix, run tests, and commit. "
                "Your hot-reload system will then pick up the changes automatically. "
                "Be specific: include error messages, tracebacks, file paths, "
                "and what you already tried."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "error_description": {
                        "type": "string",
                        "description": (
                            "Detailed description of the problem. Include: "
                            "error messages, tracebacks, file paths, what you tried."
                        ),
                    },
                    "max_budget": {
                        "type": "number",
                        "default": 1.5,
                        "description": "Max USD to spend on this repair (default $1.50)",
                    },
                },
                "required": ["error_description"],
            },
            risk_level=RiskLevel.HIGH,
            handler=_self_repair,
        ),
    ]
