"""Claude Code tool — Eva's external repair & delegation loop.

Three modes:
  1. One-shot delegation: Eva delegates a task, gets result, session discarded
  2. Persistent chat: Eva starts/continues a conversation with Claude Code
     (session preserved via -c flag, enabling ongoing collaboration)
  3. Self-repair: Eva detects issues she can't fix → Claude Code repairs her code

The "左脚踩右脚" (bootstrapping) pattern:
  Eva and Claude Code collaborate — Eva monitors runtime, Claude Code fixes code.
  They can maintain ongoing dialogue about the project.

Uses subprocess.run in thread (not asyncio.create_subprocess) because
WindowsSelectorEventLoopPolicy breaks async subprocesses on Windows.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

from anima.config import project_root
from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger

log = get_logger("tools.claude_code")

CLAUDE_TIMEOUT_S = 300  # 5 min max
# Track the last session ID for continuation
_last_session_id: str = ""


def _run_sync(
    prompt: str,
    working_directory: str = "",
    timeout: int = 120,
    max_budget: float = 2.0,
    continue_session: bool = False,
    resume_session: str = "",
    save_session: bool = False,
) -> dict:
    """Run Claude Code CLI synchronously (called in thread via run_in_executor)."""
    global _last_session_id
    cwd = working_directory or str(project_root())

    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "json",
        "--allowedTools", "Read,Edit,Bash,Grep,Glob,Write",
        "--max-budget-usd", str(max_budget),
        "--model", "sonnet",
    ]

    # Session handling
    if resume_session:
        cmd.extend(["-r", resume_session])
    elif continue_session and _last_session_id:
        cmd.extend(["-r", _last_session_id])

    if not save_session and not continue_session and not resume_session:
        cmd.append("--no-session-persistence")

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

        # Try to parse JSON output for session ID
        response_text = stdout
        session_id = ""
        try:
            data = json.loads(stdout)
            response_text = data.get("result", data.get("content", stdout))
            session_id = data.get("session_id", "")
            if session_id:
                _last_session_id = session_id
        except (json.JSONDecodeError, TypeError):
            # Plain text output — that's fine
            response_text = stdout

        if result.returncode == 0:
            log.info("Claude Code completed (%d chars)", len(response_text))
            ret = {
                "success": True,
                "output": response_text[:3000],
                "returncode": 0,
            }
            if session_id:
                ret["session_id"] = session_id
            return ret
        else:
            log.warning("Claude Code failed (exit %d)", result.returncode)
            return {
                "success": False,
                "output": response_text[:1000],
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
    """Delegate a task to Claude Code (one-shot, no session persistence)."""
    return await asyncio.get_event_loop().run_in_executor(
        None, _run_sync, prompt, working_directory, timeout, 2.0, False, "", False
    )


async def _claude_code_chat(
    message: str,
    resume_session: str = "",
    timeout: int = 180,
    max_budget: float = 2.0,
) -> dict:
    """Chat with Claude Code in a persistent session.

    First call starts a new session. Subsequent calls continue the conversation.
    Use resume_session to resume a specific past session by ID.

    This enables ongoing collaboration — you can discuss problems, iterate on
    solutions, and maintain context across multiple exchanges.
    """
    continue_flag = bool(not resume_session)  # Continue last session if no specific ID
    save = True  # Always save session for chat mode

    return await asyncio.get_event_loop().run_in_executor(
        None, _run_sync, message, "", timeout, max_budget, continue_flag, resume_session, save
    )


async def _self_repair(error_description: str, max_budget: float = 1.5) -> dict:
    """Invoke Claude Code to repair your own code.

    Use when you encounter an error you can't fix from within your own tools.
    Claude Code will read source files, diagnose, fix, run tests, and commit.
    Your hot-reload system will pick up the changes automatically.
    """
    root = str(project_root())
    full_prompt = f"""You are being invoked by Eva (ANIMA's AI agent) to fix an issue in her own codebase.

Project: ANIMA — a heartbeat-driven autonomous AI agent system
Location: {root}
Test command: D:/program/codesupport/anaconda/envs/anima/python.exe -m pytest tests/ --ignore=tests/test_oauth_live.py --ignore=tests/stress_test.py --tb=short -q

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
        None, _run_sync, full_prompt, root, 300, max_budget, False, "", False
    )


def get_claude_code_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="claude_code",
            description=(
                "Delegate a one-shot task to Claude Code CLI. "
                "Use for tasks needing deep reasoning, multi-file analysis, "
                "code generation, or debugging beyond simple tool calls. "
                "Session is NOT preserved — use claude_code_chat for ongoing dialogue."
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
            name="claude_code_chat",
            description=(
                "Chat with Claude Code in a persistent session. "
                "First call starts a new conversation. Subsequent calls continue it. "
                "Use resume_session to resume a specific past session by ID. "
                "This enables ongoing collaboration — discuss problems, iterate on "
                "solutions, and maintain context across multiple exchanges. "
                "Claude Code remembers everything from the conversation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Your message to Claude Code",
                    },
                    "resume_session": {
                        "type": "string",
                        "description": "Session ID to resume (optional — omit to continue last session)",
                        "default": "",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 180)",
                        "default": 180,
                    },
                    "max_budget": {
                        "type": "number",
                        "description": "Max USD to spend (default $2.00)",
                        "default": 2.0,
                    },
                },
                "required": ["message"],
            },
            risk_level=RiskLevel.MEDIUM,
            handler=_claude_code_chat,
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
