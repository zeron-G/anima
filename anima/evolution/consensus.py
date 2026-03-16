"""Evolution Consensus — distributed voting on proposals.

Single node: self-review (optionally invoke Claude Code as external reviewer).
Multi node: broadcast proposal, collect votes, ≥50% approve → pass.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from anima.evolution.proposal import Proposal, ProposalStatus
from anima.utils.logging import get_logger

log = get_logger("evolution.consensus")


class ConsensusEngine:
    """Manages voting on evolution proposals."""

    def __init__(self, node_id: str = "local") -> None:
        self._node_id = node_id
        self._gossip_broadcast: Callable | None = None
        self._pending_votes: dict[str, Proposal] = {}  # proposal_id → Proposal

    def set_gossip(self, broadcast_fn: Callable) -> None:
        self._gossip_broadcast = broadcast_fn

    def submit_for_voting(self, proposal: Proposal, alive_count: int = 1) -> bool:
        """Submit a proposal for voting.

        Single node: auto-approve with self-review.
        Multi node: broadcast and wait for votes.

        Returns True if voting started (or auto-approved).
        """
        proposal.status = ProposalStatus.VOTING

        if alive_count <= 1:
            # Single node — self-review
            return self._self_review(proposal)

        # Multi node — broadcast
        if self._gossip_broadcast:
            self._gossip_broadcast({
                "type": "evolution_propose",
                "proposal": proposal.to_dict(),
                "from_node": self._node_id,
            })
            self._pending_votes[proposal.id] = proposal
            log.info("Proposal %s broadcast for voting (%d nodes)", proposal.id, alive_count)
            return True

        # No gossip available — fallback to self-review
        return self._self_review(proposal)

    def handle_vote(self, proposal_id: str, node_id: str, vote: str, reason: str = "") -> None:
        """Handle an incoming vote from a remote node."""
        proposal = self._pending_votes.get(proposal_id)
        if not proposal:
            return

        proposal.votes[node_id] = vote
        if reason:
            proposal.vote_reasons[node_id] = reason

        log.info("Vote on %s from %s: %s", proposal_id, node_id[:8], vote)

    def check_result(self, proposal: Proposal, total_nodes: int) -> str | None:
        """Check if voting is complete. Returns 'approved', 'rejected', or None (still waiting)."""
        votes = proposal.votes
        if len(votes) < max(1, total_nodes - 1):  # Wait for all non-proposer nodes
            return None

        approves = sum(1 for v in votes.values() if v == "approve")
        rejects = sum(1 for v in votes.values() if v == "reject")
        total = len(votes)

        if total == 0:
            return None

        if approves / total >= 0.5:
            proposal.status = ProposalStatus.APPROVED
            self._pending_votes.pop(proposal.id, None)
            log.info("Proposal %s APPROVED (%d/%d)", proposal.id, approves, total)
            return "approved"
        elif rejects / total > 0.5:
            proposal.status = ProposalStatus.REJECTED
            self._pending_votes.pop(proposal.id, None)
            log.info("Proposal %s REJECTED (%d/%d reject)", proposal.id, rejects, total)
            return "rejected"

        return None

    def _self_review(self, proposal: Proposal) -> bool:
        """Single-node self-review. Auto-approve with safety checks."""
        # Risk-based review
        if proposal.risk == "high":
            log.warning("High-risk proposal %s — requires careful review", proposal.id)
            # In future: invoke Claude Code for external review
            # For now: approve but log warning

        if proposal.breaking_change:
            log.warning("Breaking change proposal %s — extra caution", proposal.id)

        proposal.votes[self._node_id] = "approve"
        proposal.status = ProposalStatus.APPROVED
        log.info("Proposal %s self-approved (single node)", proposal.id)
        return True
