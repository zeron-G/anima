"""S4 regression: autonomous cognition is paced to be few-but-meaningful.

The heartbeat extends the self-thinking interval when recent thoughts produced
no action (governance.recent_quiet_ratio), on top of emotion pacing (S2).
"""

from __future__ import annotations

from anima.core.governance import GovernanceEngine


def test_recent_quiet_ratio_empty():
    g = GovernanceEngine()
    assert g.recent_quiet_ratio() == 0.0


def test_recent_quiet_ratio_all_quiet():
    g = GovernanceEngine()
    for _ in range(4):
        g.check_self_thinking_loop("quiet")
    assert g.recent_quiet_ratio() == 1.0


def test_recent_quiet_ratio_mixed():
    g = GovernanceEngine()
    for a in ["quiet", "active", "quiet", "quiet"]:
        g.check_self_thinking_loop(a)
    assert g.recent_quiet_ratio() == 0.75  # 3 of 4 quiet


def test_quiet_ratio_triggers_backoff_threshold():
    """The heartbeat backs off when ratio >= 0.6; verify the boundary the loop
    relies on is reachable from real action history."""
    g = GovernanceEngine()
    for a in ["active", "quiet", "quiet", "quiet"]:
        g.check_self_thinking_loop(a)
    ratio = g.recent_quiet_ratio()
    assert ratio >= 0.6
    # interval multiplier the heartbeat applies
    assert (1.0 + ratio) > 1.5
