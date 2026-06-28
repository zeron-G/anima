from anima.core.governance import GovernanceEngine


def test_governance_status_includes_quiet_ratio_and_recent_actions():
    gov = GovernanceEngine()
    gov._recent_self_thinking_actions = ["quiet", "tool", "quiet", "tool", "quiet"]
    gov._drift_scores = [0.1, 0.2, 0.6, 0.7]

    status = gov.get_status()

    assert status["activity_level"] in {"active", "cautious", "minimal"}
    assert status["recent_self_thinking"] == ["quiet", "tool", "quiet", "tool", "quiet"]
    assert status["quiet_ratio"] == 0.6
    assert status["drift_scores"] == [0.1, 0.2, 0.6, 0.7]
