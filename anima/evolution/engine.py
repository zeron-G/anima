"""Evolution Engine — orchestrates the six-layer evolution pipeline.

Pipeline: Proposal → Consensus → Implement → Test → Review → Deploy

This is the main entry point. Called by the major heartbeat.
"""

from __future__ import annotations

import json
import os
import re
import subprocess as _sp
import time

from anima.config import project_root
from anima.evolution.proposal import Proposal, ProposalQueue, ProposalStatus
from anima.evolution.consensus import ConsensusEngine
from anima.evolution.sandbox import Worktree, TestRunner
from anima.evolution.memory import EvolutionMemory
from anima.evolution.deployer import Deployer
from anima.evolution.agent_pool import AgentPool
from anima.utils.logging import get_logger

log = get_logger("evolution.engine")

# Rate limits
MAX_EVOLUTIONS_PER_HOUR = 3
MAX_CONSECUTIVE_FAILURES = 3
FAILURE_COOLDOWN_S = 7200  # 2 hours


class EvolutionEngine:
    """Main orchestrator for the evolution pipeline."""

    def __init__(self, node_id: str = "local") -> None:
        self._node_id = node_id
        self.memory = EvolutionMemory()
        self.queue = ProposalQueue()
        self.consensus = ConsensusEngine(node_id)
        self.deployer = Deployer(self.memory)
        self.agent_pool = AgentPool()

        self._running = False
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

    def wire(self, gossip_mesh=None, reload_manager=None, agent_manager=None) -> None:
        """Wire external dependencies."""
        self._gossip_mesh = gossip_mesh
        self._reload_manager = reload_manager
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
        # Governance check — core module protection + failure cooldown
        from anima.core.governance import get_governance
        gov = get_governance()
        allowed, reason = gov.check_evolution_proposal(
            {"files": proposal.files, "title": proposal.title, "human_confirmed": getattr(proposal, 'human_confirmed', False)},
            self._consecutive_failures,
        )
        if not allowed:
            log.warning("Governance rejected evolution: %s", reason)
            return "governance_rejected"

        # Rate limit check
        self._check_rate_limits()
        if time.time() < self._cooldown_until:
            remaining = int(self._cooldown_until - time.time())
            log.warning("Evolution in cooldown (%ds remaining)", remaining)
            return "cooldown"

        log.info("═══ Evolution Pipeline: %s ═══", proposal.title)

        # Layer 2: Consensus — C-04 fix: actually wait for votes
        alive = self.get_alive_count()
        voting_started = self.consensus.submit_for_voting(proposal, alive)
        if not voting_started:
            self.memory.record_failure(
                proposal.id, proposal.type.value, proposal.title,
                "Consensus rejected", "Proposal did not pass voting",
            )
            return "rejected"

        # For multi-node: wait for votes before proceeding
        if alive > 1:
            result = await self._wait_for_votes(proposal, alive, timeout=30)
            if result != "approved":
                self.memory.record_failure(
                    proposal.id, proposal.type.value, proposal.title,
                    f"Consensus: {result}", f"Voting result: {result}",
                )
                log.info("Evolution consensus: %s for '%s'", result, proposal.title)
                return result

        # Add to queue
        self.queue.add(proposal)

        # Execute in background (don't block cognitive loop)
        if self._current_proposal is None:
            import asyncio
            asyncio.get_event_loop().create_task(self._execute_next())
            return "approved_executing"

        return "queued"

    async def _wait_for_votes(self, proposal, alive: int, timeout: int = 30) -> str:
        """Poll consensus.check_result() until voting completes or timeout.

        Returns 'approved', 'rejected', or 'timeout'.
        """
        import asyncio
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self.consensus.check_result(proposal, alive)
            if result is not None:
                return result
            await asyncio.sleep(2)
        # Timeout — check one final time with whatever votes we have
        result = self.consensus.check_result(proposal, alive)
        if result is not None:
            return result
        log.warning("Consensus voting timed out for '%s' after %ds", proposal.title, timeout)
        return "timeout"

    async def _execute_next(self) -> str:
        """Execute the next proposal in queue through implement → test → review → deploy."""
        proposal = self.queue.pop()
        if not proposal:
            return "empty"

        self._current_proposal = proposal
        worktree = None

        try:
            # Layer 3: Implement (in isolated worktree)
            proposal.status = ProposalStatus.IMPLEMENTING
            log.info("Layer 3 (Implement): %s", proposal.title)

            worktree = Worktree(proposal.id)
            worktree_path = worktree.create()
            proposal.implementation_branch = worktree.branch

            # Implementation: spawn a SubAgent with LLM agentic loop
            impl_ok = await self._run_implementation(proposal, worktree_path)
            if not impl_ok:
                log.warning("Implementation failed for %s", proposal.id)
                proposal.status = ProposalStatus.FAILED
                self._on_failure(proposal, stage="implement", error="Implementation agent failed or timed out")
                worktree.cleanup()
                return "implement_failed"

            # Layer 4: Test
            proposal.status = ProposalStatus.TESTING
            log.info("Layer 4 (Test): running three-level tests...")

            # Test in worktree (SubAgent edits files there)
            runner = TestRunner(str(worktree_path))

            # Level 1: Static
            ok, out = runner.level1_static(proposal.files)
            if not ok:
                return await self._handle_test_failure(proposal, worktree, "Level 1", out)

            # Level 2: Pytest
            ok, out = runner.level2_pytest()
            if not ok:
                return await self._handle_test_failure(proposal, worktree, "Level 2", out)

            # Level 3: Sandbox (optional for trivial changes)
            if proposal.complexity != "trivial":
                ok, out = await runner.level3_sandbox()
                if not ok:
                    return await self._handle_test_failure(proposal, worktree, "Level 3", out)

            # Layer 5: Review
            proposal.status = ProposalStatus.REVIEWING
            log.info("Layer 5 (Review): checking diff...")

            # Get diff from worktree (SubAgent edits there)
            try:
                diff_result = _sp.run(
                    ["git", "diff"], cwd=str(worktree_path),
                    capture_output=True, text=True, timeout=10,
                )
                diff = diff_result.stdout or ""
            except Exception:
                diff = worktree.get_diff() or ""
            review_ok, review_msg = self._review_diff(diff, proposal, cwd=str(worktree_path))
            if not review_ok:
                log.warning("Review failed: %s", review_msg)
                proposal.status = ProposalStatus.FAILED
                self._on_failure(proposal, stage="review", error=review_msg)
                return "review_failed"

            # Create evo/* branch for PR flow (in worktree)
            branch = f"evo/{proposal.id}"
            _sp.run(["git", "checkout", "-b", branch],
                    cwd=str(worktree_path), capture_output=True, timeout=15)

            # Commit changes in worktree (SubAgent edited files there)
            _sp.run(["git", "add", "-A"], cwd=str(worktree_path),
                    capture_output=True, timeout=15)
            _sp.run(
                ["git", "commit", "-m", f"Evolution {proposal.id}: {proposal.title}"],
                cwd=str(worktree_path), capture_output=True, timeout=15,
            )

            # Sprint 8: Create safety tag before deploy
            safety_tag = self._create_safety_tag(proposal)

            # Layer 6: Deploy via evo/* branch + PR (from worktree)
            log.info("Layer 6 (Deploy): creating PR on %s...", branch)
            pr_ok, pr_msg = self._deploy_via_pr(proposal, branch, cwd=str(worktree_path))

            if pr_ok:
                # M-08 fix: set DEPLOYING first, DEPLOYED only after verification
                proposal.status = ProposalStatus.DEPLOYING if hasattr(ProposalStatus, 'DEPLOYING') else ProposalStatus.DEPLOYED
                log.info("Deployed via PR: %s — %s", proposal.title, pr_msg)
                self.memory.record_success(
                    proposal.id, proposal.type.value, proposal.title,
                    proposal.files, proposal.solution[:200],
                )
                # Broadcast to other nodes so they auto-sync
                if self._gossip_mesh:
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
            else:
                # PR failed — abandon this evolution, don't touch production
                log.warning("PR flow failed (%s) — abandoning evolution (production safe)", pr_msg)
                self._on_failure(proposal, stage="deploy", error=f"PR creation failed: {pr_msg}")
                return "deploy_failed"

            # Sprint 8: Post-deploy health check
            if self.deployer and hasattr(self.deployer, 'verify_deployment'):
                try:
                    ok, verify_msg = await self.deployer.verify_deployment()
                    if not ok:
                        log.warning("Post-deploy health check failed: %s", verify_msg)
                        self._auto_rollback_to_tag(proposal, safety_tag, verify_msg)
                        return "rolled_back"
                except Exception as e:
                    log.warning("Health check error: %s", e)

            # Trigger hot-reload if needed
            self.deployer.trigger_hot_reload(proposal)

            # Success!
            self._on_success(proposal)
            self.queue.archive(proposal)
            log.info("═══ Evolution COMPLETE: %s ═══", proposal.title)
            return "deployed"

        except Exception as e:
            log.error("Evolution pipeline error: %s", e)
            proposal.status = ProposalStatus.FAILED
            self._on_failure(proposal, stage="pipeline", error=str(e))
            return "error"

        finally:
            self._current_proposal = None
            if worktree:
                worktree.cleanup()
            # Clean up expired agents
            self.agent_pool.cleanup_expired()

    async def _run_implementation(self, proposal: Proposal, worktree_path) -> bool:
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

        log.info("Spawning implementation agent for %s (using Claude Code)", proposal.id)
        timeout = 600

        # Use Claude Code for code development tasks (user requirement)
        # Falls back to internal agent if Claude Code CLI not available
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

        # Wait for the agent to finish
        result = await self._agent_manager.wait_for(session.id, timeout=timeout)

        if result.status in ("completed", "done"):
            log.info("Implementation agent completed: %s", proposal.id)
            return True
        elif result.status == "timeout":
            log.warning("Implementation agent timed out: %s", proposal.id)
            return False
        else:
            log.warning("Implementation agent %s: status=%s error=%s", proposal.id, result.status, result.error or "none")
            return False

    async def _handle_test_failure(self, proposal: Proposal, worktree: Worktree,
                                    level: str, output: str) -> str:
        """Handle test failure — retry or abandon."""
        proposal.retry_count += 1
        log.warning("Test %s FAILED (attempt %d/%d): %s",
                     level, proposal.retry_count, proposal.max_retries, output[:200])

        if proposal.retry_count >= proposal.max_retries:
            proposal.status = ProposalStatus.ABANDONED
            self.memory.record_failure(
                proposal.id, proposal.type.value, proposal.title,
                f"Test {level} failed after {proposal.max_retries} attempts",
                f"Test output: {output[:300]}",
            )
            self._on_failure(proposal, stage=f"test_{level.lower().replace(' ', '_')}", error=output[:300])
            return "abandoned"

        # Retry — feed test failure output into the next implementation attempt
        proposal.last_test_output = output[:500]
        return "retry"

    def _review_diff(self, diff: str, proposal: Proposal, cwd: str = "") -> tuple[bool, str]:
        """Basic automated code review."""
        review_cwd = cwd or str(project_root())
        issues = []

        if not diff or not diff.strip():
            # SubAgent may have already committed — check git log in worktree
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

        # Check for obvious problems
        lines = diff.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("+") and not line.startswith("+++"):
                # Check for hardcoded paths
                # L-23: Cross-platform hardcoded path detection
                home_dir = os.path.expanduser("~")
                hardcoded_indicators = [
                    home_dir,                          # User home directory
                    "D:\\program", "D:\\data\\code",   # Windows dev paths
                    "C:\\Users",                        # Windows users
                    "/Users/", "/home/",               # macOS/Linux users
                    "/opt/", "/usr/local/",            # Unix install paths
                ]
                if any(indicator in line for indicator in hardcoded_indicators):
                    issues.append(f"Hardcoded path found: line {i}")
                # Check for debug leftovers (word-boundary match to avoid false positives
                # like sprint(, blueprint(, print_func(, etc.)
                if (re.search(r'\bprint\(', line)
                        and "log." not in line
                        and "# noqa" not in line
                        and "# allow-print" not in line):
                    issues.append(f"Debug print found: line {i}")
                # Check for secrets
                if any(kw in line.lower() for kw in ["password=", "token=", "secret="]):
                    if "env var" not in line.lower() and '""' not in line:
                        issues.append(f"Possible secret in code: line {i}")

        if issues:
            return False, "; ".join(issues)
        return True, "OK"

    def _deploy_via_pr(self, proposal: Proposal, branch: str,
                       cwd: str = "") -> tuple[bool, str]:
        """Push evo/* branch and create PR. Auto-merge if low risk (≤3 files)."""
        root = cwd or str(project_root())
        try:
            # Push branch
            push = _sp.run(
                ["git", "push", "-u", "origin", branch],
                cwd=root, capture_output=True, text=True, timeout=30,
            )
            if push.returncode != 0:
                raise RuntimeError(f"Push failed: {push.stderr}")

            # Create PR
            body = (
                f"## Evolution: {proposal.title}\n\n"
                f"**Problem:** {proposal.problem[:200]}\n"
                f"**Solution:** {proposal.solution[:200]}\n"
                f"**Risk:** {proposal.risk}\n"
                f"**Files:** {', '.join(proposal.files[:10])}"
            )
            pr = _sp.run(
                ["gh", "pr", "create",
                 "--base", "master", "--head", branch,
                 "--title", f"evo: {proposal.title}",
                 "--body", body],
                cwd=root, capture_output=True, text=True, timeout=30,
            )
            if pr.returncode != 0:
                raise RuntimeError(f"PR creation failed: {pr.stderr}")

            pr_url = pr.stdout.strip()

            # Auto-merge if low risk (≤3 files changed)
            if len(proposal.files) <= 3 and proposal.risk == "low":
                merge = _sp.run(
                    ["gh", "pr", "merge", "--merge", "--delete-branch"],
                    cwd=root, capture_output=True, text=True, timeout=30,
                )
                if merge.returncode == 0:
                    # Pull merged changes back to project_root (main checkout)
                    main_root = str(project_root())
                    _sp.run(["git", "checkout", "master"],
                            cwd=main_root, capture_output=True, timeout=15)
                    _sp.run(["git", "pull", "origin", "master"],
                            cwd=main_root, capture_output=True, timeout=30)
                    return True, f"Auto-merged: {pr_url}"
                else:
                    log.warning("Auto-merge failed: %s", merge.stderr)

            # High risk or merge failed: leave PR open, return to master
            _sp.run(["git", "checkout", "master"], cwd=root, capture_output=True, timeout=15)
            return True, f"PR created (awaiting review): {pr_url}"

        except Exception as e:
            # Return to master and clean up failed branch
            _sp.run(["git", "checkout", "master"], cwd=root, capture_output=True, timeout=15)
            _sp.run(["git", "branch", "-D", branch], cwd=root, capture_output=True, timeout=15)
            return False, str(e)

    def _create_safety_tag(self, proposal) -> str:
        """Create a git tag as a known-good checkpoint before deploy."""
        tag_name = f"pre-evo-{proposal.id}"
        try:
            _sp.run(["git", "tag", tag_name, "HEAD~1"],
                    cwd=str(project_root()), capture_output=True, timeout=10)
            log.info("Created safety tag: %s", tag_name)
        except Exception as e:
            log.warning("Failed to create safety tag: %s", e)
        return tag_name

    def _auto_rollback_to_tag(self, proposal, tag_name: str, reason: str) -> bool:
        """Rollback to the safety tag if post-deploy health check fails."""
        try:
            _sp.run(["git", "reset", "--hard", tag_name],
                    cwd=str(project_root()), capture_output=True, timeout=15)
            _sp.run(["git", "push", "origin", "private", "--force"],
                    cwd=str(project_root()), capture_output=True, timeout=30)
            log.warning("Auto-rolled back to tag %s: %s", tag_name, reason)
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
        # Persist to structured log for post-mortem diagnosis
        try:
            log_path = os.path.join(str(__import__("anima.config", fromlist=["project_root"]).project_root()), "data", "logs", "evolution_failures.jsonl")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(self._last_failure) + "\n")
        except Exception as exc:
            log.warning("Could not write failure log: %s", exc)
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self._cooldown_until = time.time() + FAILURE_COOLDOWN_S
            log.warning("Too many failures (%d) — cooling down for %ds",
                        self._consecutive_failures, FAILURE_COOLDOWN_S)

    def _check_rate_limits(self) -> None:
        now = time.time()
        if now - self._last_hour_reset > 3600:
            self._evolution_count_this_hour = 0
            self._last_hour_reset = now

        if self._evolution_count_this_hour >= MAX_EVOLUTIONS_PER_HOUR:
            log.warning("Rate limit: %d evolutions this hour", self._evolution_count_this_hour)

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
