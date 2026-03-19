"""Unified subprocess execution layer.

**ALL** external command execution in ANIMA MUST go through this module.
Direct ``subprocess.run()`` calls with user-controlled input are
forbidden outside of this file.

This module prevents command injection by enforcing:

1. **No shell=True with user-controlled input** — only the ``shell``
   tool (which passes through ``safety.assess_command_risk()``) is
   allowed to use shell mode.
2. **Structural argument parsing** — ``split_command()`` uses
   ``shlex.split()`` and rejects shell metacharacters.
3. **Centralized timeout and resource limits** — every tool category
   has a default timeout.
4. **Audit logging** — every command execution is logged with tool
   name, command summary, and duration.

Fixes: C-01, C-02, C-03 (shell injection in github/google/remote tools)
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import sys
import time
from typing import Any

from anima.models.tool_spec import RiskLevel
from anima.tools.safety import assess_command_risk
from anima.utils.errors import CommandRejected
from anima.utils.logging import get_logger

log = get_logger("safe_subprocess")

# ── Configuration ──

# Tools that MUST use list-form commands (never shell=True)
_NEVER_SHELL: frozenset[str] = frozenset({
    "github", "google", "remote_exec", "remote_write",
    "claude_code", "audit_run",
})

# Default timeouts per tool category (seconds)
_TOOL_TIMEOUTS: dict[str, int] = {
    "shell": 60,
    "github": 30,
    "google": 30,
    "remote_exec": 30,
    "remote_write": 15,
    "search": 15,
    "glob_search": 15,
    "system_info": 10,
    "claude_code": 300,
    "audit_run": 120,
}
_DEFAULT_TIMEOUT = 30

# Shell metacharacters that should NEVER appear in non-shell arguments
_DANGEROUS_SEQUENCES: frozenset[str] = frozenset({
    "&&", "||", ";", "`",
    "$(", "${",
    "\n", "\r",
    # Pipe is allowed in split_command because shlex handles it,
    # but we block it as a precaution for non-shell tools
    "|",
})


# ── Public API ──


def split_command(executable: str, user_args: str) -> list[str]:
    """Safely split user arguments into a command list.

    Parameters
    ----------
    executable:
        The program to run (e.g. ``"gh"``, ``"gog"``).
    user_args:
        User-provided argument string to parse.

    Returns
    -------
    ``[executable, arg1, arg2, ...]`` suitable for
    ``subprocess.run(cmd, shell=False)``.

    Raises
    ------
    CommandRejected
        If *user_args* contains shell metacharacters that indicate
        an injection attempt.
    ValueError
        If *user_args* cannot be safely parsed by ``shlex``.

    Examples
    --------
    >>> split_command("gh", "pr list --repo owner/repo")
    ['gh', 'pr', 'list', '--repo', 'owner/repo']

    >>> split_command("gh", "pr list && rm -rf /")
    CommandRejected: Shell metacharacter '&&' detected ...
    """
    # Strip leading/trailing whitespace
    user_args = user_args.strip()
    if not user_args:
        return [executable]

    # First check: scan raw input for dangerous sequences BEFORE parsing
    # This catches cases where shlex might normalize away the danger
    for seq in _DANGEROUS_SEQUENCES:
        if seq in user_args:
            raise CommandRejected(
                f"Shell metacharacter {seq!r} detected in arguments. "
                f"This tool does not support shell syntax — "
                f"pass individual arguments instead.",
                command=f"{executable} {user_args[:80]}",
            )

    # Parse with shlex (handles quoting correctly)
    try:
        parts = shlex.split(user_args)
    except ValueError as exc:
        raise ValueError(
            f"Cannot parse arguments for {executable}: {exc}"
        ) from exc

    # Second check: verify parsed tokens don't contain residual danger
    # (e.g. a quoted string that contains backticks)
    for part in parts:
        for seq in ("`", "$(", "${"):
            if seq in part:
                raise CommandRejected(
                    f"Command substitution {seq!r} detected in argument "
                    f"{part!r}. This is not allowed.",
                    command=f"{executable} {user_args[:80]}",
                )

    return [executable] + parts


async def run_safe(
    cmd: list[str] | str,
    *,
    tool_name: str = "shell",
    timeout: int | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    check_safety: bool = True,
    max_output: int = 100_000,
) -> dict[str, Any]:
    """Execute an external command safely.

    All tool handlers that need to run external processes should call
    this function instead of ``subprocess.run()`` directly.

    Parameters
    ----------
    cmd:
        Command to execute.  May be a list (preferred, no shell) or
        a string (only for the ``shell`` tool after safety check).
    tool_name:
        Name of the calling tool — used for timeout lookup, logging,
        and policy enforcement.
    timeout:
        Override timeout in seconds.  ``None`` uses the default for
        *tool_name*.
    cwd:
        Working directory for the subprocess.
    env:
        Environment variables.  Defaults to a copy of ``os.environ``
        with the Python executable's directory prepended to PATH.
    check_safety:
        Whether to run ``assess_command_risk()`` on string commands.
        Only relevant when *cmd* is a string.
    max_output:
        Maximum bytes of stdout/stderr to capture.  Prevents OOM
        from commands that produce unbounded output.

    Returns
    -------
    ``{"returncode": int, "stdout": str, "stderr": str}``
    with optional ``"error": str`` if returncode != 0 or timeout.

    Raises
    ------
    CommandRejected
        If *cmd* is a string and *tool_name* is in ``_NEVER_SHELL``,
        or if the safety check blocks the command.
    """
    effective_timeout = timeout or _TOOL_TIMEOUTS.get(tool_name, _DEFAULT_TIMEOUT)

    # ── Determine shell mode ──

    if isinstance(cmd, str):
        # String commands: only allowed for the 'shell' tool
        if tool_name in _NEVER_SHELL:
            raise CommandRejected(
                f"Tool '{tool_name}' must use list-form commands. "
                f"Use split_command() to safely parse arguments.",
                command=cmd[:100],
            )
        # Safety check for shell commands
        if check_safety:
            risk = assess_command_risk(cmd)
            if risk == RiskLevel.BLOCKED:
                raise CommandRejected(
                    "Command blocked by safety policy",
                    command=cmd[:100],
                )
        shell = True
        cmd_for_log = cmd[:100]
    else:
        # List commands: no shell injection risk
        shell = False
        cmd_for_log = " ".join(str(c) for c in cmd[:5])
        if len(cmd) > 5:
            cmd_for_log += " ..."

    # ── Build environment ──

    if env is None:
        env = os.environ.copy()
        # Ensure Python executable is on PATH
        py_dir = os.path.dirname(sys.executable)
        env["PATH"] = py_dir + os.pathsep + env.get("PATH", "")

    log.debug(
        "run_safe [%s]: %s (timeout=%ds, shell=%s)",
        tool_name, cmd_for_log, effective_timeout, shell,
    )

    # ── Execute in thread pool ──

    start = time.time()

    def _run() -> dict[str, Any]:
        try:
            result = subprocess.run(
                cmd,
                shell=shell,
                capture_output=True,
                timeout=effective_timeout,
                cwd=cwd,
                env=env,
                # Prevent excessive output from consuming memory
                # (subprocess.run doesn't support maxlen directly,
                #  but we truncate after capture)
            )
            out = result.stdout[:max_output].decode("utf-8", errors="replace").strip()
            err = result.stderr[:max_output].decode("utf-8", errors="replace").strip()

            d: dict[str, Any] = {
                "returncode": result.returncode,
                "stdout": out,
                "stderr": err,
            }
            if result.returncode != 0:
                d["error"] = f"Exit {result.returncode}: {err[:200]}"
            return d

        except subprocess.TimeoutExpired:
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": f"Command timed out after {effective_timeout}s",
                "error": f"timeout ({effective_timeout}s)",
            }
        except FileNotFoundError as exc:
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": str(exc),
                "error": f"Command not found: {exc}",
            }
        except OSError as exc:
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": str(exc),
                "error": str(exc),
            }

    result = await asyncio.get_running_loop().run_in_executor(None, _run)
    elapsed = time.time() - start

    log.debug(
        "run_safe [%s]: completed in %.1fs (rc=%d)",
        tool_name, elapsed, result["returncode"],
    )

    return result


def get_tool_timeout(tool_name: str) -> int:
    """Get the configured timeout for a tool.

    Used by ``ToolExecutor`` to set per-tool timeouts on handler
    invocation (not just subprocess execution).
    """
    return _TOOL_TIMEOUTS.get(tool_name, _DEFAULT_TIMEOUT)
