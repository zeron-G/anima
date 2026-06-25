"""Evolution Deployer — merge, push, hot-reload, rollback.

Safely commits and pushes verified evolution code,
triggers hot-reload via ReloadManager, and auto-rollbacks on failure.
"""

from __future__ import annotations

import asyncio
import re

from anima.config import data_dir
from anima.evolution.proposal import Proposal
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
        self._reload_manager = None

    def set_reload_manager(self, rm) -> None:
        self._reload_manager = rm

    # NOTE: the code-deploy path (cherry-pick → optional push) lives in
    # EvolutionEngine._deploy_via_pr — the single active deploy route. The
    # Deployer only owns post-deploy verification + hot-reload.

    async def verify_deployment(self) -> tuple[bool, str]:
        """Wait and check if ANIMA is healthy after deployment."""
        log.info("Verifying deployment (%ds)...", VERIFY_TIMEOUT_S)
        await asyncio.sleep(VERIFY_TIMEOUT_S)

        log_file = data_dir() / "logs" / "anima.log"
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
