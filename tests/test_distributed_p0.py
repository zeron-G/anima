"""Phase-1 distributed security P0 fixes (DISTRIBUTED_DESIGN):
consensus strict-majority (a 1-1 tie no longer passes; a single approver can't
carry a 3-node cluster). The peer auto-approve removal + mesh fail-closed guard
are behavioral (main.py) and covered by the live-boot check."""
from __future__ import annotations

from anima.evolution.consensus import ConsensusEngine
from anima.evolution.proposal import create_proposal


def _proposal(votes: dict):
    p = create_proposal(type="bugfix", title="t", problem="p", solution="s")
    p.votes = dict(votes)
    return p


def test_tie_does_not_pass():
    # 1 approve + 1 reject in a 3-node cluster was APPROVED under the old
    # `approves/total >= 0.5` (a tie passed). Must now NOT approve.
    ce = ConsensusEngine("n1")
    assert ce.check_result(_proposal({"a": "approve", "b": "reject"}), 3) is None


def test_strict_majority_approves():
    ce = ConsensusEngine("n1")
    # 2 approvals in a 3-node cluster = strict majority (need = 2) → approved.
    assert ce.check_result(_proposal({"a": "approve", "b": "approve"}), 3) == "approved"


def test_majority_reject():
    ce = ConsensusEngine("n1")
    assert ce.check_result(_proposal({"a": "reject", "b": "reject"}), 3) == "rejected"


def test_single_approver_cannot_carry_cluster():
    # Only one vote cast in a 3-node cluster → below the vote-count gate → wait,
    # never auto-approve on a lone approval.
    ce = ConsensusEngine("n1")
    assert ce.check_result(_proposal({"a": "approve"}), 3) is None


def test_five_node_needs_three():
    ce = ConsensusEngine("n1")
    # 5-node cluster: need = 3 approvals.
    # 2 approve / 2 reject (4 votes) is still UNDECIDED — the 5th could approve
    # and reach 3 = majority — so we correctly keep waiting.
    assert ce.check_result(_proposal({"a": "approve", "b": "approve", "c": "reject", "d": "reject"}), 5) is None
    # 3 rejects: approval (needs 3) is now impossible → rejected.
    assert ce.check_result(_proposal({"a": "reject", "b": "reject", "c": "reject", "d": "approve"}), 5) == "rejected"
    # 3 approvals = strict majority → approved.
    assert ce.check_result(_proposal({"a": "approve", "b": "approve", "c": "approve", "d": "reject"}), 5) == "approved"
