"""Evolution Engine — orchestrates the six-layer evolution pipeline.

Pipeline: Proposal → Consensus → Implement → Test → Review → Deploy

Retry architecture:
  - Test/review/implement failures trigger re-implementation with error context
  - Up to max_retries (default 3) per proposal
  - On final exhaustion, pushes FOLLOW_UP event back to cognitive loop
  - Governance rejections return actionable feedback to the LLM tool call
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess as _sp
import time

from anima.config import project_root, data_dir, source_tree, get
from anima.evolution.proposal import Proposal, ProposalQueue, ProposalStatus
from anima.evolution.consensus import ConsensusEngine
from anima.evolution.sandbox import Worktree, TestRunner
from anima.evolution.memory import EvolutionMemory
from anima.evolution.deployer import Deployer
from anima.evolution.agent_pool import AgentPool
from anima.utils.logging import get_logger


def _pytest_failures(output: str) -> int:
    """Failure count = failed + collection/setup errors, from pytest output like
    '5 failed, 392 passed' or '3 errors'."""
    total = 0
    m = re.search(r'(\d+) failed', output or "")
    if m:
        total += int(m.group(1))
    m = re.search(r'(\d+) errors?\b', output or "")
    if m:
        total += int(m.group(1))
    return total


def _pytest_trustworthy(output: str) -> bool:
    """True only if pytest actually ran to a summary we can trust. A crash,
    timeout, collection error, or 'no tests ran' is NOT trustworthy and must be
    treated as a hard failure — never as '0 failures = pass' (CODE_REVIEW
    evolution P2: previously a change that broke test collection sailed through)."""
    o = output or ""
    if o.strip() == "TIMEOUT":
        return False
    fatal = ("INTERNALERROR", "errors during collection", "ERROR collecting",
             "no tests ran", "Traceback (most recent call last)")
    if any(f in o for f in fatal):
        return False
    # A trustworthy run reports a recognizable passed/failed/skipped summary.
    return re.search(r'\d+ (passed|failed|skipped|deselected|xfailed|xpassed)', o) is not None


log = get_logger("evolution.engine")

# Rate limits
MAX_EVOLUTIONS_PER_HOUR = 3
MAX_CONSECUTIVE_FAILURES = 3
FAILURE_COOLDOWN_S = 7200  # 2 hours


def _git_branch() -> str:
    """The single integration branch name, used consistently by deploy, rollback,
    and remote sync. Previously deploy pushed `master` while rollback force-pushed
    `private` — a remote data-loss hazard (CODE_REVIEW P0-3)."""
    return get("evolution.git_branch", "master")


class EvolutionEngine:
    """Main orchestrator for the evolution pipeline."""

    def __init__(self, node_id: str = "local", governance=None) -> None:
        self._node_id = node_id
        self._governance = governance
        self.memory = EvolutionMemory()
        self.queue = ProposalQueue()
        self.consensus = ConsensusEngine(node_id)
        self.deployer = Deployer(self.memory)
        self.agent_pool = AgentPool()

        self._running = False
        self._exec_lock = asyncio.Lock()
        self._current_proposal: Proposal | None = None
        self._evolution_count_this_hour = 0
        self._consecutive_failures = 0
        self._last_hour_reset = time.time()
        self._cooldown_until = 0
        self._last_failure: dict | None = None

        # External references (set by main.py via wire())
        self._gossip_mesh = None
        self._reload_manager = None
        self._agent_manager = None
        self._event_queue = None  # For pushing FOLLOW_UP events on failure

    def wire(self, gossip_mesh=None, reload_manager=None, agent_manager=None,
             event_queue=None) -> None:
        """Wire external dependencies."""
        self._gossip_mesh = gossip_mesh
        self._reload_manager = reload_manager
        self._event_queue = event_queue
        if agent_manager:
            self._agent_manager = agent_manager
        if gossip_mesh:
            self.consensus.set_gossip(gossip_mesh.broadcast_event)
        if reload_manager:
            self.deployer.set_reload_manager(reload_manager)

    def get_alive_count(self) -> int:
        if self._gossip_mesh:
            return self._gossip_mesh.get_alive_count()
        return 1

    # ── Pipeline Entry ──

    async def submit_proposal(self, proposal: Proposal) -> str:
        """Submit a proposal through the pipeline. Returns final status."""
        # Installed kernel (no source tree) cannot evolve code — disable cleanly
        # instead of running git inside site-packages.
        if source_tree() is None:
            log.warning(
                "Evolution disabled: not running from a source checkout "
                "(installed kernel has no repository to evolve)"
            )
            return "disabled: not a source checkout"
        # Governance check — core module protection + failure cooldown
        if self._governance:
            gov = self._governance
        else:
            from anima.core.governance import get_governance
            gov = get_governance()
        allowed, reason = gov.check_evolution_proposal(
            {"id": proposal.id, "files": proposal.files, "title": proposal.title},
            self._consecutive_failures,
        )
        if not allowed:
            log.warning("Governance rejected evolution: %s", reason)
            self._throttle_after_rejection("governance")
            return f"governance_rejected: {reason}"

        # Rate limit check — ENFORCED (previously only logged, so the hourly cap
        # was decorative; CODE_REVIEW evolution P2).
        if not self._check_rate_limits():
            self._throttle_after_rejection("rate_limit")
            return "rejected: hourly evolution rate limit reached"
        # Reset failure counter after cooldown expires
        if self._cooldown_until > 0 and time.time() >= self._cooldown_until:
            log.info("Cooldown expired — resetting consecutive failures (%d → 0)", self._consecutive_failures)
            self._consecutive_failures = 0
            self._cooldown_until = 0
        if time.time() < self._cooldown_until:
            remaining = int(self._cooldown_until - time.time())
            log.warning("Evolution in cooldown (%ds remaining)", remaining)
            return f"cooldown: {remaining}s remaining"

        log.info("═══ Evolution Pipeline: %s ═══", proposal.title)

        # Layer 2: Consensus — C-04 fix: actually wait for votes
        alive = self.get_alive_count()
        voting_started = self.consensus.submit_for_voting(proposal, alive)
        if not voting_started:
            self.memory.record_failure(
                proposal.id, proposal.type.value, proposal.title,
                "Consensus rejected", "Proposal did not pass voting",
            )
            self._throttle_after_rejection("consensus")
            return "rejected: consensus vote failed"

        # For multi-node: wait for votes before proceeding
        if alive > 1:
            result = await self._wait_for_votes(proposal, alive, timeout=30)
            if result != "approved":
                self.memory.record_failure(
                    proposal.id, proposal.type.value, proposal.title,
                    f"Consensus: {result}", f"Voting result: {result}",
                )
                self._throttle_after_rejection("consensus")
                log.info("Evolution consensus: %s for '%s'", result, proposal.title)
                return f"rejected: {result}"

        # Add to queue
        self.queue.add(proposal)

        # Execute in background (don't block cognitive loop)
        async with self._exec_lock:
            if self._current_proposal is None:
                asyncio.get_event_loop().create_task(self._execute_next())
                return "approved_executing"
        return "queued"

    async def _wait_for_votes(self, proposal, alive: int, timeout: int = 30) -> str:
        """Poll consensus.check_result() until voting completes or timeout."""
        import asyncio
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self.consensus.check_result(proposal, alive)
            if result is not None:
                return result
            await asyncio.sleep(2)
        result = self.consensus.check_result(proposal, alive)
        if result is not None:
            return result
        log.warning("Consensus voting timed out for '%s' after %ds", proposal.title, timeout)
        return "timeout"

    async def _execute_next(self) -> str:
        """Execute the next proposal with full retry loop.

        On any stage failure (implement, test, review), re-runs implementation
        with the error context so the agent can fix the issue. Up to max_retries
        attempts total. On final exhaustion, pushes a FOLLOW_UP event so Eva
        can revise and resubmit.
        """
        proposal = self.queue.pop()
        if not proposal:
            return "empty"

        self._current_proposal = proposal
        result = "unknown"

        try:
            # ── Retry loop: implement → test → review, retry on any failure ──
            for attempt in range(1, proposal.max_retries + 1):
                worktree = None
                try:
                    log.info("═══ Attempt %d/%d for %s ═══", attempt, proposal.max_retries, proposal.title)

                    # Layer 3: Implement
                    proposal.status = ProposalStatus.IMPLEMENTING
                    log.info("Layer 3 (Implement): %s", proposal.title)

                    worktree = Worktree(proposal.id)
                    worktree_path = worktree.create()
                    proposal.implementation_branch = worktree.branch

                    # Build error context from previous attempts
                    error_context = getattr(proposal, '_last_error_context', None)
                    impl_ok = await self._run_implementation(proposal, worktree_path, error_context=error_context)
                    if not impl_ok:
                        proposal._last_error_context = "Implementation agent failed or timed out. Simplify the change."
                        log.warning("Implementation failed (attempt %d/%d)", attempt, proposal.max_retries)
                        worktree.cleanup()
                        continue  # ← RETRY instead of return

                    # Layer 4: Test
                    proposal.status = ProposalStatus.TESTING
                    log.info("Layer 4 (Test): running tests...")

                    runner = TestRunner(str(worktree_path))

                    # Level 1: Static
                    ok, out = runner.level1_static(proposal.files)
                    if not ok:
                        proposal._last_error_context = f"Level 1 static check failed:\n{out[:500]}\nFix the syntax errors."
                        log.warning("Level 1 FAILED (attempt %d/%d)", attempt, proposal.max_retries)
                        worktree.cleanup()
                        continue  # ← RETRY

                    # Level 2: Pytest — compare against baseline
                    baseline_runner = TestRunner(str(project_root()))
                    baseline_ok, baseline_out = baseline_runner.level2_pytest()
                    baseline_failures = _pytest_failures(baseline_out)

                    ok, out = runner.level2_pytest()

                    # A run that did not complete cleanly (crash/timeout/collection
                    # error) is a HARD failure — we cannot read "0 failures" as pass.
                    if not _pytest_trustworthy(out):
                        proposal._last_error_context = (
                            f"Level 2 pytest did not run cleanly (crash / timeout / "
                            f"collection error). Your change likely broke an import or "
                            f"test collection:\n{out[:500]}\nMake a simpler, self-contained change."
                        )
                        log.warning("Level 2 UNTRUSTWORTHY — hard failure (attempt %d/%d)",
                                    attempt, proposal.max_retries)
                        worktree.cleanup()
                        continue  # ← RETRY
                    if not _pytest_trustworthy(baseline_out):
                        # The live tree's own suite can't establish a baseline →
                        # only accept a clean pass to be safe.
                        log.warning("Baseline pytest untrustworthy — requiring a clean evo run")
                        baseline_failures = 0

                    evo_failures = _pytest_failures(out)
                    if evo_failures > baseline_failures:
                        proposal._last_error_context = (
                            f"Level 2 pytest: {evo_failures} failures (baseline has {baseline_failures}). "
                            f"Your change introduced {evo_failures - baseline_failures} new failure(s):\n{out[:500]}\n"
                            f"Fix the failing tests."
                        )
                        log.warning("Level 2 FAILED: %d new failures (attempt %d/%d)",
                                    evo_failures - baseline_failures, attempt, proposal.max_retries)
                        worktree.cleanup()
                        continue  # ← RETRY
                    elif not ok:
                        log.info("Level 2: %d failures (same as baseline %d) — PASS", evo_failures, baseline_failures)

                    # Level 3: Sandbox (skip for trivial/small). Gated OFF by
                    # default: as implemented it spawns a full ANIMA that shares
                    # the live Neon DB + secrets and has evolution enabled (a
                    # second brain — CODE_REVIEW evolution P3). Enable only once an
                    # isolated-DB sandbox profile exists.
                    if proposal.complexity not in ("trivial", "small"):
                        if not get("evolution.sandbox_level3_enabled", False):
                            log.info("Level 3 sandbox skipped (evolution.sandbox_level3_enabled=false — "
                                     "avoids spawning a second brain on the shared DB)")
                        else:
                            ok, out = await runner.level3_sandbox()
                            if not ok:
                                proposal._last_error_context = f"Level 3 sandbox failed:\n{out[:500]}\nMake a simpler change."
                                log.warning("Level 3 FAILED (attempt %d/%d)", attempt, proposal.max_retries)
                                worktree.cleanup()
                                continue  # ← RETRY

                    # Layer 5: Review
                    proposal.status = ProposalStatus.REVIEWING
                    log.info("Layer 5 (Review): checking diff...")

                    try:
                        diff_result = _sp.run(
                            ["git", "diff"], cwd=str(worktree_path),
                            capture_output=True, text=True, timeout=10,
                        )
                        diff = diff_result.stdout or ""
                    except Exception:
                        diff = worktree.get_diff() or ""

                    # Diff-scope gate: enforce frozen/allowlist on the ACTUAL changed
                    # files, not just the declared ones — an agent (esp. a self-repair
                    # one that declared no files) could otherwise edit frozen/out-of-
                    # scope code. Same gate as submit-time governance.
                    scope_ok, scope_msg = self._enforce_change_scope(worktree_path, proposal)
                    if not scope_ok:
                        proposal._last_error_context = (
                            f"Change scope rejected: {scope_msg}\n"
                            f"Edit ONLY files in the evolvable allowlist; never the frozen recovery core."
                        )
                        log.warning("Change scope REJECTED (attempt %d/%d): %s",
                                    attempt, proposal.max_retries, scope_msg)
                        worktree.cleanup()
                        continue  # ← RETRY

                    review_ok, review_msg = self._review_diff(diff, proposal, cwd=str(worktree_path))
                    if not review_ok:
                        proposal._last_error_context = (
                            f"Code review failed: {review_msg}\n"
                            f"Remove debug prints, hardcoded paths, and secrets before resubmitting."
                        )
                        log.warning("Review FAILED (attempt %d/%d): %s", attempt, proposal.max_retries, review_msg)
                        worktree.cleanup()
                        continue  # ← RETRY

                    # ═══ ALL CHECKS PASSED — proceed to deploy ═══
                    _sp.run(["git", "checkout", "-b", f"evo/{proposal.id}"],
                            cwd=str(worktree_path), capture_output=True, timeout=15)
                    _sp.run(["git", "add", "-A"], cwd=str(worktree_path),
                            capture_output=True, timeout=15)
                    _sp.run(
                        ["git", "commit", "-m", f"Evolution {proposal.id}: {proposal.title}"],
                        cwd=str(worktree_path), capture_output=True, timeout=15,
                    )

                    safety_tag = self._create_safety_tag(proposal)

                    # Layer 6: Deploy
                    log.info("Layer 6 (Deploy): creating PR on evo/%s...", proposal.id)
                    pr_ok, pr_msg = self._deploy_via_pr(proposal, f"evo/{proposal.id}", cwd=str(worktree_path))

                    if pr_ok:
                        proposal.status = ProposalStatus.DEPLOYING if hasattr(ProposalStatus, 'DEPLOYING') else ProposalStatus.DEPLOYED
                        log.info("Deployed via PR: %s — %s", proposal.title, pr_msg)
                        self.memory.record_success(
                            proposal.id, proposal.type.value, proposal.title,
                            proposal.files, proposal.solution[:200],
                        )
                        self._broadcast_deployment(proposal)

                        # Post-deploy health check
                        if self.deployer and hasattr(self.deployer, 'verify_deployment'):
                            try:
                                ok, verify_msg = await self.deployer.verify_deployment()
                                if not ok:
                                    log.warning("Post-deploy health check failed: %s", verify_msg)
                                    self._auto_rollback_to_tag(proposal, safety_tag, verify_msg)
                                    result = "rolled_back"
                                    break
                            except Exception as e:
                                log.warning("Health check error: %s", e)

                        self.deployer.trigger_hot_reload(proposal)
                        self._on_success(proposal)
                        self.queue.archive(proposal)
                        log.info("═══ Evolution COMPLETE: %s ═══", proposal.title)
                        result = "deployed"
                        break  # ← SUCCESS, exit retry loop
                    else:
                        proposal._last_error_context = f"Deploy failed: {pr_msg}. Try a lower-risk approach."
                        log.warning("Deploy FAILED (attempt %d/%d): %s", attempt, proposal.max_retries, pr_msg)
                        worktree.cleanup()
                        continue  # ← RETRY

                finally:
                    if worktree:
                        worktree.cleanup()

            else:
                # ── All retries exhausted ──
                log.warning("═══ Evolution EXHAUSTED after %d attempts: %s ═══", proposal.max_retries, proposal.title)
                proposal.status = ProposalStatus.ABANDONED
                last_error = getattr(proposal, '_last_error_context', 'Unknown error')
                self._on_failure(proposal, stage="exhausted", error=last_error[:300])
                self.memory.record_failure(
                    proposal.id, proposal.type.value, proposal.title,
                    f"Exhausted {proposal.max_retries} attempts", last_error[:300],
                )
                # Push failure back to cognitive loop so Eva can revise
                self._push_failure_feedback(proposal, last_error)
                result = "exhausted"

        except Exception as e:
            log.error("Evolution pipeline error: %s", e)
            proposal.status = ProposalStatus.FAILED
            self._on_failure(proposal, stage="pipeline", error=str(e))
            self._push_failure_feedback(proposal, str(e))
            result = "error"

        finally:
            self._current_proposal = None
            self.agent_pool.cleanup_expired()

        return result

    async def _run_implementation(self, proposal: Proposal, worktree_path,
                                   error_context: str | None = None) -> bool:
        """Layer 3: Run a SubAgent to implement the proposal."""
        if not self._agent_manager:
            log.error("No agent_manager wired — cannot implement")
            return False

        files_hint = ', '.join(proposal.files) if proposal.files else 'determine from analysis'
        impl_prompt = (
            f"TASK: {proposal.title}\n"
            f"PROBLEM: {proposal.problem}\n"
            f"SOLUTION: {proposal.solution}\n"
            f"FILES: {files_hint}\n"
            f"RISK: {proposal.risk}\n\n"
            "Make the code changes. Minimal edits only.\n"
            "Do NOT run the full test suite — the pipeline handles testing.\n"
            "Just read the files, make the fix, and report what you changed."
        )

        # If this is a retry, include the error from the previous attempt
        if error_context:
            impl_prompt += (
                f"\n\n⚠ PREVIOUS ATTEMPT FAILED:\n{error_context}\n\n"
                f"Fix the above issue in this attempt. Do NOT repeat the same mistake."
            )

        log.info("Spawning implementation agent for %s (attempt %d, using Claude Code)",
                 proposal.id, proposal.retry_count + 1)
        timeout = 600

        try:
            session = await self._agent_manager.spawn_claude_code(
                prompt=impl_prompt,
                working_dir=str(worktree_path),
                timeout=timeout,
            )
        except Exception as e:
            log.warning("Claude Code spawn failed (%s), falling back to internal agent", e)
            session = await self._agent_manager.spawn_internal(
                prompt=impl_prompt,
                working_dir=str(worktree_path),
                timeout=timeout,
            )

        result = await self._agent_manager.wait_for(session.id, timeout=timeout)

        if result.status in ("completed", "done"):
            log.info("Implementation agent completed: %s", proposal.id)
            return True
        elif result.status == "timeout":
            log.warning("Implementation agent timed out: %s", proposal.id)
            return False
        else:
            log.warning("Implementation agent %s: status=%s error=%s",
                        proposal.id, result.status, result.error or "none")
            return False

    def _push_failure_feedback(self, proposal: Proposal, error: str) -> None:
        """Push a FOLLOW_UP event back to the cognitive loop with failure context.

        This ensures Eva knows about the failure and can revise her approach
        instead of silently moving on.
        """
        if not self._event_queue:
            return
        try:
            from anima.models.event import Event, EventType, EventPriority
            feedback_msg = (
                f"[EVOLUTION FAILED] Proposal '{proposal.title}' (ID: {proposal.id}) "
                f"failed after {proposal.retry_count} attempt(s).\n"
                f"Last error: {error[:400]}\n\n"
                f"You should either:\n"
                f"1. Revise the approach and resubmit with evolution_propose\n"
                f"2. Record an anti-pattern with evolution_record_lesson\n"
                f"3. Try a completely different improvement"
            )
            event = Event(
                type=EventType.FOLLOW_UP,
                payload={"text": feedback_msg, "source": "evolution_feedback"},
                priority=EventPriority.NORMAL,
                source="evolution_engine",
            )
            self._event_queue.put_nowait(event)
            log.info("Pushed evolution failure feedback to cognitive loop")
        except Exception as e:
            log.warning("Failed to push failure feedback: %s", e)

    def _broadcast_deployment(self, proposal: Proposal) -> None:
        """Broadcast deployment to gossip mesh peers."""
        if not self._gossip_mesh:
            return
        try:
            commit = _sp.run(["git", "rev-parse", "HEAD"], cwd=str(project_root()),
                             capture_output=True, text=True, timeout=10).stdout.strip()
            self._gossip_mesh.broadcast_event({
                "type": "evolution_deployed",
                "proposal_id": proposal.id,
                "title": proposal.title,
                "commit_hash": commit,
                "files": proposal.files,
            })
            log.info("Broadcast evolution_deployed to peers")
        except Exception as e:
            log.warning("Failed to broadcast deployment: %s", e)

    def _review_diff(self, diff: str, proposal: Proposal, cwd: str = "") -> tuple[bool, str]:
        """Basic automated code review."""
        review_cwd = cwd or str(project_root())
        issues = []

        if not diff or not diff.strip():
            try:
                log_result = _sp.run(
                    ["git", "log", "--oneline", "-1"],
                    cwd=review_cwd, capture_output=True, text=True, timeout=5,
                )
                last_commit = log_result.stdout.strip()
                if proposal.title and proposal.title[:20] in last_commit:
                    return True, "Already committed"
            except Exception as e:
                log.debug("_review_diff: %s", e)
            try:
                log_result = _sp.run(
                    ["git", "log", "--oneline", "-3"],
                    cwd=review_cwd, capture_output=True, text=True, timeout=5,
                )
                recent = log_result.stdout.strip()
                if "Evolution" in recent or "evolution" in recent:
                    return True, "Changes already committed by Claude Code"
            except Exception as e:
                log.debug("engine: %s", e)
            _sp.run(["git", "add", "-A"], cwd=review_cwd, capture_output=True, timeout=15)
            diff_check = _sp.run(["git", "diff", "--cached", "--stat"], cwd=review_cwd, capture_output=True, text=True, timeout=10)
            if diff_check.stdout and diff_check.stdout.strip():
                return True, "Staged changes found"
            issues.append("No changes detected")

        lines = diff.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("+") and not line.startswith("+++"):
                home_dir = os.path.expanduser("~")
                hardcoded_indicators = [
                    home_dir,
                    "D:\\program", "D:\\data\\code",
                    "C:\\Users",
                    "/Users/", "/home/",
                    "/opt/", "/usr/local/",
                ]
                if any(indicator in line for indicator in hardcoded_indicators):
                    issues.append(f"Hardcoded path found: line {i}")
                if (re.search(r'\bprint\(', line)
                        and "log." not in line
                        and "# noqa" not in line
                        and "# allow-print" not in line):
                    issues.append(f"Debug print found: line {i}")
                if any(kw in line.lower() for kw in ["password=", "token=", "secret="]):
                    if "env var" not in line.lower() and '""' not in line:
                        issues.append(f"Possible secret in code: line {i}")

        if issues:
            return False, "; ".join(issues)
        return True, "OK"

    def _deploy_via_pr(self, proposal: Proposal, branch: str,
                       cwd: str = "") -> tuple[bool, str]:
        """Deploy: merge to local master first, then push to remote.

        Flow: worktree commit → cherry-pick to local master → push → record PR.
        Local is always authoritative. GitHub PR is just for audit trail.
        """
        worktree_root = cwd or str(project_root())
        main_root = str(project_root())
        target = _git_branch()
        commit_msg = f"Evolution {proposal.id}: {proposal.title}"
        did_stash = False

        try:
            # Step 1: Get the commit hash from worktree
            result = _sp.run(
                ["git", "rev-parse", "HEAD"],
                cwd=worktree_root, capture_output=True, text=True, timeout=10,
            )
            evo_commit = result.stdout.strip()
            if not evo_commit:
                return False, "No commit found in worktree"

            # Step 2: Stash any dirty files in main repo (Eva writes feelings/persona
            # at runtime). Track whether a stash was actually created so we don't
            # mis-handle a "no stash entries" pop later.
            stash = _sp.run(["git", "stash", "--include-untracked", "-m", "pre-evo-autostash"],
                            cwd=main_root, capture_output=True, text=True, timeout=15)
            did_stash = "No local changes to save" not in (stash.stdout + stash.stderr)

            # Step 3: Cherry-pick the evolution commit into the integration branch
            co = _sp.run(["git", "checkout", target],
                         cwd=main_root, capture_output=True, text=True, timeout=15)
            if co.returncode != 0:
                if did_stash:
                    _sp.run(["git", "stash", "pop"], cwd=main_root, capture_output=True, timeout=10)
                return False, f"Could not checkout {target}: {(co.stderr or '').strip()[:200]}"
            pick = _sp.run(
                ["git", "cherry-pick", evo_commit, "--no-edit"],
                cwd=main_root, capture_output=True, text=True, timeout=30,
            )
            if pick.returncode != 0:
                # Cherry-pick conflict — abort and fall back to merge
                _sp.run(["git", "cherry-pick", "--abort"],
                        cwd=main_root, capture_output=True, timeout=10)
                # Try merge instead
                merge = _sp.run(
                    ["git", "merge", evo_commit, "-m", commit_msg, "--no-edit"],
                    cwd=main_root, capture_output=True, text=True, timeout=30,
                )
                if merge.returncode != 0:
                    _sp.run(["git", "merge", "--abort"],
                            cwd=main_root, capture_output=True, timeout=10)
                    if did_stash:
                        _sp.run(["git", "stash", "pop"],
                                cwd=main_root, capture_output=True, timeout=10)
                    return False, f"Cherry-pick and merge both failed: {pick.stderr} / {merge.stderr}"

            log.info("Evolution committed to local %s: %s", target, commit_msg)

            # Step 4: Push to remote (opt-in; local is authoritative). Never force.
            pushed = False
            if get("evolution.git_remote_sync", False):
                push = _sp.run(
                    ["git", "push", "origin", target],
                    cwd=main_root, capture_output=True, text=True, timeout=30,
                )
                if push.returncode != 0:
                    log.warning("Push to remote failed (will retry later): %s", push.stderr)
                pushed = push.returncode == 0
            else:
                log.info("Evolution remote sync off — committed to local %s only", target)

            # Step 5: Restore stashed runtime files — with a defined conflict path
            # (no more silent half-merged tree, CODE_REVIEW P1).
            stash_note = ""
            if did_stash:
                pop = _sp.run(["git", "stash", "pop"],
                              cwd=main_root, capture_output=True, text=True, timeout=10)
                if pop.returncode != 0:
                    log.critical("Stash pop conflicted after deploy — taking deployed "
                                 "code and dropping the stash. Review: %s",
                                 (pop.stderr or "").strip()[:300])
                    _sp.run(["git", "checkout", "--", "."], cwd=main_root, capture_output=True, timeout=10)
                    _sp.run(["git", "stash", "drop"], cwd=main_root, capture_output=True, timeout=10)
                    stash_note = " (WARNING: runtime-file stash conflicted and was dropped)"

            # Step 6: Clean up branches (remote delete only when sync enabled)
            if get("evolution.git_remote_sync", False):
                _sp.run(["git", "push", "origin", "--delete", branch],
                        cwd=main_root, capture_output=True, timeout=15)
            _sp.run(["git", "branch", "-D", branch],
                    cwd=main_root, capture_output=True, timeout=15)

            push_status = "pushed" if pushed else "local only"
            log.info("Deploy complete (%s): %s%s", push_status, commit_msg, stash_note)
            return True, f"Deployed to local {target} ({push_status}){stash_note}"

        except Exception as e:
            # Restore on any failure
            _sp.run(["git", "checkout", target],
                    cwd=main_root, capture_output=True, timeout=15)
            if did_stash:
                _sp.run(["git", "stash", "pop"],
                        cwd=main_root, capture_output=True, timeout=10)
            _sp.run(["git", "branch", "-D", branch],
                    cwd=main_root, capture_output=True, timeout=15)
            return False, str(e)

    def _changed_files(self, worktree_path) -> list[str]:
        """Repo-relative paths actually modified in the worktree (staged+unstaged
        +untracked), POSIX-normalized."""
        files: set[str] = set()
        for args in (["git", "diff", "--name-only"],
                     ["git", "diff", "--name-only", "--cached"],
                     ["git", "ls-files", "--others", "--exclude-standard"]):
            try:
                r = _sp.run(args, cwd=str(worktree_path), capture_output=True,
                            text=True, timeout=10)
                for line in (r.stdout or "").splitlines():
                    line = line.strip().replace("\\", "/")
                    if line:
                        files.add(line)
            except Exception:  # noqa: BLE001
                pass
        return sorted(files)

    def _enforce_change_scope(self, worktree_path, proposal) -> tuple[bool, str]:
        """Re-run the governance frozen/allowlist/approval gate on the worktree's
        ACTUAL changed files (not just proposal.files)."""
        changed = self._changed_files(worktree_path)
        if not changed:
            return True, "no changes"
        if self._governance is not None:
            gov = self._governance
        else:
            from anima.core.governance import get_governance
            gov = get_governance()
        return gov.gate_files(changed, getattr(proposal, "id", ""))

    def _create_safety_tag(self, proposal) -> str:
        # Tag the CURRENT integration-branch tip — the exact pre-deploy state to
        # roll back to. (Was HEAD~1, which is one commit too early — rolling back
        # would have restored the wrong state. CODE_REVIEW P0-3.) This runs before
        # the cherry-pick lands, so HEAD here == pre-deploy tip.
        tag_name = f"pre-evo-{proposal.id}"
        try:
            _sp.run(["git", "tag", tag_name, "HEAD"],
                    cwd=str(project_root()), capture_output=True, timeout=10)
            log.info("Created safety tag: %s → HEAD", tag_name)
        except Exception as e:
            log.warning("Failed to create safety tag: %s", e)
        return tag_name

    def _auto_rollback_to_tag(self, proposal, tag_name: str, reason: str) -> bool:
        try:
            _sp.run(["git", "reset", "--hard", tag_name],
                    cwd=str(project_root()), capture_output=True, timeout=15)
            # Local is authoritative. We deliberately do NOT push the rollback:
            # force-pushing a shared branch from an autonomous process is a
            # remote data-loss hazard (CODE_REVIEW P0-3). A human / the normal
            # sync reconciles the remote.
            log.warning("Auto-rolled back to tag %s (local only): %s", tag_name, reason)
            self.memory.record_failure(
                proposal.id, proposal.type.value if hasattr(proposal.type, 'value') else str(proposal.type),
                proposal.title, f"Auto-rollback: {reason}", reason,
            )
            return True
        except Exception as e:
            log.error("Auto-rollback to %s failed: %s", tag_name, e)
            return False

    def _on_success(self, proposal: Proposal) -> None:
        self._consecutive_failures = 0
        self._evolution_count_this_hour += 1

    def _on_failure(self, proposal: Proposal, stage: str = "unknown", error: str = "") -> None:
        self._consecutive_failures += 1
        self._last_failure = {
            "title": proposal.title,
            "proposal_id": proposal.id,
            "stage": stage,
            "error": error or proposal.status.value,
            "timestamp": time.time(),
        }
        try:
            log_path = os.path.join(str(data_dir()), "logs", "evolution_failures.jsonl")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(self._last_failure) + "\n")
        except Exception as exc:
            log.warning("Could not write failure log: %s", exc)
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self._cooldown_until = time.time() + FAILURE_COOLDOWN_S
            log.warning("Too many failures (%d) — cooling down for %ds",
                        self._consecutive_failures, FAILURE_COOLDOWN_S)

    def _check_rate_limits(self) -> bool:
        """True if another evolution is allowed this hour. Enforced by the caller."""
        now = time.time()
        if now - self._last_hour_reset > 3600:
            self._evolution_count_this_hour = 0
            self._last_hour_reset = now

        if self._evolution_count_this_hour >= MAX_EVOLUTIONS_PER_HOUR:
            log.warning("Rate limit reached: %d evolutions this hour", self._evolution_count_this_hour)
            return False
        return True

    def _throttle_after_rejection(self, reason: str) -> None:
        """Count a rejection toward the consecutive-failure throttle so a tight
        loop of rejected proposals eventually trips the cooldown (CODE_REVIEW
        evolution P2: previously only pipeline failures counted, so rejected
        proposals could loop with no backoff)."""
        self._consecutive_failures += 1
        if (self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES
                and self._cooldown_until <= time.time()):
            self._cooldown_until = time.time() + FAILURE_COOLDOWN_S
            log.warning("Throttle: %d consecutive rejections/failures (%s) — cooldown %ds",
                        self._consecutive_failures, reason, FAILURE_COOLDOWN_S)

    # ── Status ──

    def get_status(self) -> dict:
        return {
            "running": self._current_proposal is not None,
            "current": self._current_proposal.to_dict() if self._current_proposal else None,
            "queue_size": self.queue.size,
            "evolutions_this_hour": self._evolution_count_this_hour,
            "consecutive_failures": self._consecutive_failures,
            "cooldown_remaining": max(0, int(self._cooldown_until - time.time())),
            "last_failure": self._last_failure,
            "memory": {
                "successes": len(self.memory.successes),
                "failures": len(self.memory.failures),
                "goals": len(self.memory.goals),
                "anti_patterns": len(self.memory.anti_patterns),
            },
            "agents": self.agent_pool.get_tree_summary(),
        }
