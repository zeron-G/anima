"""Skill sandbox — isolated execution environment for skill commands."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from anima.utils.logging import get_logger

log = get_logger("skill_sandbox")


class SkillSandbox:
    """Run skill commands in an isolated environment."""

    def __init__(self, skill_path: Path, timeout: int = 30) -> None:
        self._path = skill_path
        self._timeout = timeout

    async def run(self, command: str) -> dict:
        """Execute a command in the skill directory with restricted environment."""
        # Build sanitized environment
        env = dict(os.environ)
        # Remove sensitive keys
        for key in ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_OAUTH_TOKEN",
                     "OPENAI_API_KEY", "DISCORD_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"]:
            env.pop(key, None)
        env["PYTHONPATH"] = str(self._path)

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                command, shell=True,
                cwd=str(self._path),
                env=env,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout[:2000],
                "stderr": result.stderr[:1000],
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Command timed out ({self._timeout}s)"}
        except Exception as e:
            return {"success": False, "error": str(e)}
