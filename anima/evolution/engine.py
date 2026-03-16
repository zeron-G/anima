"""Evolution Engine — orchestrates the six-layer evolution pipeline.

Pipeline: Proposal → Consensus → Implement → Test → Review → Deploy

This is the main entry point. Called by the major heartbeat.
"""

from __future__ import annotations

import asyncio
import subprocess as _sp
import time
from typing import Any

from anima.config import get, project_root
from anima.evolution.proposal import Proposal, ProposalQueue, ProposalStatus, create_proposal
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
        # Rate limit check
        self._check_rate_limits()
        if time.time() < self._cooldown_until:
            remaining = int(self._cooldown_until - time.time())
            log.warning("Evolution in cooldown (%ds remaining)", remaining)
            return "cooldown"

        log.info("═══ Evolution Pipeline: %s ═══", proposal.title)

        # Layer 2: Consensus
        alive = self.get_alive_count()
        approved = self.consensus.submit_for_voting(proposal, alive)
        if not approved:
            self.memory.record_failure(
                proposal.id, proposal.type.value, proposal.title,
                "Consensus rejected", "Proposal did not pass voting",
            )
            return "rejected"

        # Add to queue
        self.queue.add(proposal)

        # Execute in background (don't block cognitive loop)
        if self._current_proposal is None:
            import asyncio
            asyncio.get_event_loop().create_task(self._execute_next())
            return "approved_executing"

        return "queued"

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
                self._on_failure(proposal)
                worktree.cleanup()
                return "implement_failed"

            # Layer 4: Test
            proposal.status = ProposalStatus.TESTING
            log.info("Layer 4 (Test): running three-level tests...")

            # Test in project root (SubAgent edits files there, not in worktree)
            runner = TestRunner(str(project_root()))

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

            # Get diff from main project (SubAgent edits there, not in worktree)
            try:
                diff_result = _sp.run(
                    ["git", "diff"], cwd=str(project_root()),
                    capture_output=True, text=True, timeout=10,
                )
                diff = diff_result.stdout or ""
            except Exception:
                diff = worktree.get_diff() or ""
            review_ok, review_msg = self._review_diff(diff, proposal)
            if not review_ok:
                log.warning("Review failed: %s", review_msg)
                proposal.status = ProposalStatus.FAILED
                self._on_failure(proposal)
                return "review_failed"

            # Commit changes in main project (SubAgent edited files there)
            _sp.run(["git", "add", "-A"], cwd=str(project_root()), capture_output=True)
            _sp.run(
                ["git", "commit", "-m", f"Evolution {proposal.id}: {proposal.title}"],
                cwd=str(project_root()), capture_output=True,
            )

            # Layer 6: Deploy
            log.info("Layer 6 (Deploy): pushing...")
            try:
                _sp.run(
                    ["git", "push", "origin", "private"],
                    cwd=str(project_root()), capture_output=True, timeout=30,
                )
                proposal.status = ProposalStatus.DEPLOYED
                log.info("Deployed: Evolution %s: %s", proposal.id, proposal.title)
                self.memory.record_success(
                    proposal.id, proposal.type.value, proposal.title,
                    proposal.files, proposal.solution[:200],
                )
            except Exception as e:
                log.warning("Push failed: %s", e)

            # Post-deploy verification
            ok, msg = await self.deployer.verify_deployment()
            if not ok:
                self.deployer.rollback(proposal, msg)
                self._on_failure(proposal)
                return "rolled_back"

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
            self._on_failure(proposal)
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

        impl_prompt = (
            f"EVOLUTION TASK: {proposal.title}\n\n"
            f"PROBLEM: {proposal.problem}\n"
            f"SOLUTION: {proposal.solution}\n"
            f"FILES TO MODIFY: {', '.join(proposal.files) if proposal.files else 'determine from analysis'}\n"
            f"RISK: {proposal.risk}\n\n"
            "INSTRUCTIONS:\n"
            "1. Read the relevant source files\n"
            "2. Make the necessary code changes using edit_file or write_file\n"
            "3. Run tests: shell(command=\"python -m pytest tests/ --tb=short -q\")\n"
            "4. If tests fail, fix the issues\n"
            "5. Report what you changed\n\n"
            "IMPORTANT: Only modify files related to this task. Minimal changes."
        )

        log.info("Spawning implementation agent for %s (using Claude Code)", proposal.id)
        timeout = 600 if proposal.complexity in ("medium", "large") else 300

        # Use Claude Code for code development tasks (user requirement)
        # Falls back to internal agent if Claude Code CLI not available
        try:
            session = await self._agent_manager.spawn_claude_code(
                prompt=impl_prompt,
                working_dir=str(project_root()),
                timeout=timeout,
            )
        except Exception as e:
            log.warning("Claude Code spawn failed (%s), falling back to internal agent", e)
            session = await self._agent_manager.spawn_internal(
                prompt=impl_prompt,
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
            self._on_failure(proposal)
            return "abandoned"

        # Retry — go back to implement
        # TODO: feed test output back to implementation agent for fixing
        return "retry"

    def _review_diff(self, diff: str, proposal: Proposal) -> tuple[bool, str]:
        """Basic automated code review."""
        issues = []

        if not diff or not diff.strip():
            # SubAgent may have already committed — check git log instead
            try:
                log_result = _sp.run(
                    ["git", "log", "--oneline", "-1"],
                    cwd=str(project_root()), capture_output=True, text=True, timeout=5,
                )
                last_commit = log_result.stdout.strip()
                if proposal.title and proposal.title[:20] in last_commit:
                    # SubAgent already committed — that's fine
                    return True, "Already committed"
            except Exception:
                pass
            # Check if there are any new commits since proposal started
            try:
                log_result = _sp.run(
                    ["git", "log", "--oneline", "-3"],
                    cwd=str(project_root()), capture_output=True, text=True, timeout=5,
                )
                recent = log_result.stdout.strip()
                if "Evolution" in recent or "evolution" in recent:
                    return True, "Changes already committed by Claude Code"
            except Exception:
                pass
            # Claude Code may have made changes that just need staging
            _sp.run(["git", "add", "-A"], cwd=str(project_root()), capture_output=True)
            diff_check = _sp.run(["git", "diff", "--cached", "--stat"], cwd=str(project_root()), capture_output=True, text=True)
            if diff_check.stdout and diff_check.stdout.strip():
                return True, "Staged changes found"
            # Really no changes
            issues.append("No changes detected")

        # Check for obvious problems
        lines = diff.split("\n")
        for i, line in enumerate(lines):
            if line.startswith("+") and not line.startswith("+++"):
                # Check for hardcoded paths
                if "D:\\program" in line or "D:\\data\\code" in line or "C:\\Users" in line:
                    issues.append(f"Hardcoded path found: line {i}")
                # Check for debug leftovers
                if "print(" in line and "log." not in line:
                    issues.append(f"Debug print found: line {i}")
                # Check for secrets
                if any(kw in line.lower() for kw in ["password=", "token=", "secret="]):
                    if "env var" not in line.lower() and '""' not in line:
                        issues.append(f"Possible secret in code: line {i}")

        if issues:
            return False, "; ".join(issues)
        return True, "OK"

    def _on_success(self, proposal: Proposal) -> None:
        self._consecutive_failures = 0
        self._evolution_count_this_hour += 1

    def _on_failure(self, proposal: Proposal) -> None:
        self._consecutive_failures += 1
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
            "memory": {
                "successes": len(self.memory.successes),
                "failures": len(self.memory.failures),
                "goals": len(self.memory.goals),
                "anti_patterns": len(self.memory.anti_patterns),
            },
            "agents": self.agent_pool.get_tree_summary(),
        }
