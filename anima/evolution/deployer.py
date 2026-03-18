"""Evolution Deployer — merge, push, hot-reload, rollback.

Safely commits and pushes verified evolution code,
triggers hot-reload via ReloadManager, and auto-rollbacks on failure.
"""

from __future__ import annotations

import asyncio
import re
import subprocess

from anima.config import project_root
from anima.evolution.proposal import Proposal, ProposalStatus
from anima.evolution.memory import EvolutionMemory
from anima.utils.logging import get_logger

log = get_logger("evolution.deployer")

VERIFY_TIMEOUT_S = 30

# Persistent background errors unrelated to deployments — never trigger rollback.
KNOWN_NOISE_ERRORS = [
    re.compile(r, re.IGNORECASE) for r in [
        r"AUTHENTICATIONFAILED",          # Gmail / IMAP auth
        r"torch.*not.*installed",         # TTS optional dep
        r"tts.*torch",                    # TTS torch missing
        r"discord\.py.*not.*installed",   # optional Discord dep
        r"No module named 'torch'",       # torch import error
    ]
]


def _is_noise(line: str) -> bool:
    return any(p.search(line) for p in KNOWN_NOISE_ERRORS)


class Deployer:
    """Deploys verified evolution branches safely."""

    def __init__(self, memory: EvolutionMemory) -> None:
        self._memory = memory
        self._root = str(project_root())
        self._reload_manager = None

    def set_reload_manager(self, rm) -> None:
        self._reload_manager = rm

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + list(args),
            cwd=self._root, capture_output=True, text=True,
            check=check, timeout=30,
        )

    def deploy(self, proposal: Proposal, worktree_branch: str) -> bool:
        """Merge evolution branch into private and push."""
        try:
            proposal.status = ProposalStatus.DEPLOYED
            current = self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
            if current != "private":
                self._git("checkout", "private")

            merge_msg = f"Evolution {proposal.id}: {proposal.title}"
            result = self._git("merge", worktree_branch, "--no-ff", "-m", merge_msg, check=False)
            if result.returncode != 0:
                log.error("Merge failed: %s", result.stderr)
                self._git("merge", "--abort", check=False)
                proposal.status = ProposalStatus.FAILED
                return False

            push_result = self._git("push", "origin", "private", check=False)
            if push_result.returncode != 0:
                log.warning("Push failed: %s", push_result.stderr)

            log.info("Deployed: %s", merge_msg)
            self._memory.record_success(
                proposal.id, proposal.type.value, proposal.title,
                proposal.files, proposal.solution[:200],
            )
            return True

        except Exception as e:
            log.error("Deploy failed: %s", e)
            proposal.status = ProposalStatus.FAILED
            return False

    def rollback(self, proposal: Proposal, reason: str) -> bool:
        """Revert the last commit."""
        try:
            self._git("revert", "HEAD", "--no-edit")
            self._git("push", "origin", "private", check=False)
            proposal.status = ProposalStatus.ROLLED_BACK
            self._memory.record_failure(
                proposal.id, proposal.type.value, proposal.title,
                f"Rolled back: {reason}", f"Deploy verification failed: {reason}",
            )
            log.warning("Rolled back %s: %s", proposal.id, reason)
            return True
        except Exception as e:
            log.error("Rollback failed: %s", e)
            return False

    async def verify_deployment(self) -> tuple[bool, str]:
        """Wait and check if ANIMA is healthy after deployment."""
        log.info("Verifying deployment (%ds)...", VERIFY_TIMEOUT_S)
        await asyncio.sleep(VERIFY_TIMEOUT_S)

        log_file = project_root() / "data" / "logs" / "anima.log"
        if not log_file.exists():
            return False, "No log file"
        try:
            text = log_file.read_text(encoding="utf-8", errors="replace")
            recent = text.splitlines()[-20:]
            errors = [line for line in recent if "[ERROR]" in line or "[CRITICAL]" in line]
            errors = [line for line in errors if not _is_noise(line)]
            if errors:
                return False, f"Post-deploy errors: {errors[0]}"
            return True, "OK"
        except Exception as e:
            return False, str(e)

    def trigger_hot_reload(self, proposal: Proposal) -> None:
        """Trigger ANIMA hot-reload if .py files were modified.

        Uses simple flag setting — no API calls that could fail
        due to code version mismatch during reload.
        """
        py_files = [f for f in proposal.files if f.endswith(".py")]
        if not py_files:
            return

        if self._reload_manager:
            try:
                # Just set the flag directly — don't call methods that
                # might have changed in the evolution we just deployed
                self._reload_manager._restart_requested = True
                self._reload_manager._restart_reason = f"Evolution {proposal.id}: {proposal.title}"
                log.info("Hot-reload flagged for %s", proposal.id)
            except Exception as e:
                log.warning("Hot-reload flag failed: %s — restart manually", e)
