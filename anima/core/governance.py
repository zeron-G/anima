"""Unified governance engine — controls Eva's autonomous behavior boundaries.

Consolidates scattered governance checks into one module:
- Personality update frequency limits
- Evolution proposal safety checks
- Self-thinking loop detection
- Style drift accumulation monitoring
- Activity level management
"""

from __future__ import annotations

import json
import threading
import time

from anima.config import get, data_dir
from anima.utils.logging import get_logger

log = get_logger("governance")

# Areas evolution may modify WITHOUT per-change human approval — the designated
# "safe zone" for autonomous growth (add tools, skills, perception). Everything
# else (api, llm, memory, network, dashboard, non-frozen core, …) needs approval.
# Override/extend via config `evolution.evolvable_paths`.
_DEFAULT_EVOLVABLE_PATHS = (
    "anima/tools/builtin/",   # new tools (additive; Eva already has these capabilities)
    "anima/perception/",      # scanners / environment perception
)

# Sensitive (non-frozen) modules that ALWAYS require approval, even if a broad
# allowlist were configured.
_CORE_MODULES = frozenset({
    "anima/core/cognitive.py", "anima/core/heartbeat.py",
    "anima/core/event_queue.py", "anima/core/pipeline.py",
    "anima/core/stages.py",
})


def _under_any(path: str, prefixes) -> bool:
    """True if POSIX-normalized `path` lives under one of `prefixes`."""
    p = (path or "").replace("\\", "/").lstrip("./")
    for pre in (prefixes or ()):
        pre = str(pre).replace("\\", "/").lstrip("./")
        if pre and (p == pre.rstrip("/") or p.startswith(pre if pre.endswith("/") else pre + "/")):
            return True
    return False


class GovernanceEngine:
    """Unified governance — controls Eva's autonomous behavior boundaries."""

    def __init__(self) -> None:
        self._last_personality_update: float = 0
        self._last_relationship_update: float = 0
        self._recent_self_thinking_actions: list[str] = []
        self._drift_scores: list[float] = []

    def check_personality_update(self, file: str) -> tuple[bool, str]:
        """Check if personality/relationship update is allowed.

        personality.md: max once per 4 hours
        relationship.md: max once per 24 hours
        """
        now = time.time()
        if file == "personality":
            if now - self._last_personality_update < 14400:  # 4 hours
                remaining = int((14400 - (now - self._last_personality_update)) / 60)
                return False, f"Personality update cooldown: {remaining}min remaining"
            self._last_personality_update = now
            return True, "OK"
        elif file == "relationship":
            if now - self._last_relationship_update < 86400:  # 24 hours
                remaining = int((86400 - (now - self._last_relationship_update)) / 3600)
                return False, f"Relationship update cooldown: {remaining}h remaining"
            self._last_relationship_update = now
            return True, "OK"
        return True, "OK"

    def check_evolution_proposal(self, proposal: dict, recent_failures: int) -> tuple[bool, str]:
        """Decide whether an evolution proposal may proceed.

        Tiered, narrowest-first:
          1. FROZEN recovery core → hard reject, no override. These decide
             whether/how evolution runs and recovers; if evolution could rewrite
             them, all guarantees collapse.
          2. NEEDS APPROVAL → any file that is a sensitive core module OR sits
             OUTSIDE the explicitly-evolvable allowlist. Autonomous self-modification
             is narrowed to designated safe areas (tools/skills/perception); touching
             any other source needs a real OUT-OF-BAND human approval token. We never
             trust a model-supplied flag (CODE_REVIEW P0-1).
          3. Consecutive failures → extended cooldown.

        The default flips from "allow unless core" to "allow only in the safe zone"
        — the user's intent that hard self-modification be tightly constrained.
        """
        files = proposal.get("files", []) or []
        ok, reason = self.gate_files(files, proposal.get("id", ""))
        if not ok:
            return False, reason
        if recent_failures >= 3:
            return False, f"Too many recent failures ({recent_failures}), extended cooldown"
        return True, "OK"

    def gate_files(self, files, proposal_id: str) -> tuple[bool, str]:
        """Frozen + allowlist + approval gate over a file list. Used BOTH at submit
        time (declared files) and post-implementation (the worktree's ACTUAL changed
        files) — the latter closes "declare innocuous files, edit frozen ones"."""
        from anima.guardian.frozen import frozen_hits

        files = files or []
        frozen = frozen_hits(files)
        if frozen:
            return False, f"FROZEN recovery core — change refused (human-only): {frozen}"

        evolvable = get("evolution.evolvable_paths", _DEFAULT_EVOLVABLE_PATHS)
        gated = []
        for f in files:
            nf = str(f).replace("\\", "/").lstrip("./")
            if nf in _CORE_MODULES or not _under_any(nf, evolvable):
                gated.append(nf)
        if gated:
            pid = str(proposal_id or "").strip()
            if not pid or not self.is_evolution_approved(pid):
                return False, (
                    f"Change needs human approval (core or outside evolvable allowlist): "
                    f"{gated}. Approve out-of-band, then retry "
                    f"(create {self.approvals_dir() / (pid or '<id>')}.approved)."
                )
        return True, "OK"

    @staticmethod
    def approvals_dir():
        """Directory where a human drops <proposal_id>.approved tokens to authorize
        a core-module evolution. Outside the LLM's reach — file creation is the
        out-of-band signal that replaces the spoofable `human_confirmed` flag."""
        d = data_dir() / ".guardian" / "approvals"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def is_evolution_approved(self, proposal_id: str) -> bool:
        """True iff a human has dropped an approval token for this proposal id."""
        if not proposal_id:
            return False
        return (self.approvals_dir() / f"{proposal_id}.approved").exists()

    def check_self_thinking_loop(self, action: str) -> bool:
        """Detect if self-thinking is in an action loop.

        If 3 consecutive self-thoughts all executed tool calls,
        reduce activity (return False to skip next one).
        """
        self._recent_self_thinking_actions.append(action)
        if len(self._recent_self_thinking_actions) > 5:
            self._recent_self_thinking_actions.pop(0)

        # If last 3 all had tool actions, signal to reduce
        if len(self._recent_self_thinking_actions) >= 3:
            recent = self._recent_self_thinking_actions[-3:]
            if all(a != "quiet" for a in recent):
                log.info("Self-thinking loop detected — 3 consecutive active thoughts")
                return False  # Should reduce activity
        return True

    def load_recent_drift_scores(self, max_entries: int = 20) -> None:
        """Load recent drift scores from drift.jsonl."""
        drift_path = data_dir() / "logs" / "drift.jsonl"
        if not drift_path.exists():
            return
        try:
            lines = drift_path.read_text(encoding="utf-8").strip().split("\n")
            recent = lines[-max_entries:] if len(lines) > max_entries else lines
            self._drift_scores = []
            for line in recent:
                if line.strip():
                    entry = json.loads(line)
                    self._drift_scores.append(entry.get("drift_score", 0))
        except Exception:
            pass

    def check_drift_accumulation(self, drift_score: float) -> bool:
        """Check if style drift is accumulating dangerously.

        If 5 consecutive responses have drift_score > 0.5,
        trigger style fallback (return False).
        """
        # Bootstrap from drift.jsonl if no scores loaded yet
        if not self._drift_scores:
            self.load_recent_drift_scores()

        self._drift_scores.append(drift_score)
        if len(self._drift_scores) > 10:
            self._drift_scores.pop(0)

        if len(self._drift_scores) >= 5:
            recent = self._drift_scores[-5:]
            if all(s > 0.5 for s in recent):
                log.warning("Drift accumulation detected — 5 consecutive high-drift responses")
                return False  # Should trigger style reset
        return True

    def recent_quiet_ratio(self) -> float:
        """Fraction of recent self-thoughts that produced no action (0..1).

        A high ratio means Eva keeps thinking but doing nothing — the heartbeat
        uses this to back off so autonomous thought stays few-but-meaningful (S4).
        """
        acts = self._recent_self_thinking_actions
        if not acts:
            return 0.0
        return sum(1 for a in acts if a == "quiet") / len(acts)

    def get_activity_level(self) -> str:
        """Determine current activity level based on config."""
        return get("governance.default_mode", "active")

    def get_status(self) -> dict:
        """Return a compact governance snapshot for API/UI consumers."""
        return {
            "activity_level": self.get_activity_level(),
            "recent_self_thinking": self._recent_self_thinking_actions[-5:],
            "quiet_ratio": round(self.recent_quiet_ratio(), 3),
            "drift_scores": self._drift_scores[-10:],
        }


# Module-level singleton
_governance: GovernanceEngine | None = None
_governance_lock = threading.Lock()


def get_governance() -> GovernanceEngine:
    """Get or create the singleton GovernanceEngine (thread-safe).

    .. deprecated::
        Prefer dependency injection via CognitiveContext.governance.
        This function is retained for API/tool layer compatibility.
    """
    global _governance
    if _governance is not None:
        return _governance
    with _governance_lock:
        if _governance is None:
            _governance = GovernanceEngine()
    return _governance
