"""Integration tests — verify pipeline components work together.

These tests run in CI Stage 3 and validate:
  1. Issue tracker + self-audit integration
  2. Evolution engine evo/* branch + PR flow
  3. Idle scheduler audit task dispatch
"""

import pytest


# ── Test 1: Issue tracker lifecycle ──

@pytest.mark.asyncio
async def test_issue_tracker_lifecycle(tmp_path):
    """Verify issue creation, update, closure, and persistence."""
    from anima.core.issue_tracker import IssueTracker

    tracker = IssueTracker(issues_dir=tmp_path / "issues")

    # Create
    issue = tracker.create(
        title="Test bug",
        description="Something broke",
        priority="high",
        labels=["bug", "test"],
    )
    assert issue.id.startswith("iss_")
    assert issue.title == "Test bug"
    assert issue.priority.value == "high"

    # List
    issues = tracker.list_issues(status="open")
    assert len(issues) == 1

    # Update
    updated = tracker.update(issue.id, status="in_progress")
    assert updated.status.value == "in_progress"

    # Close
    closed = tracker.close(issue.id, resolution="Fixed in PR #1")
    assert closed.status.value == "closed"
    assert closed.resolution == "Fixed in PR #1"

    # Stats
    stats = tracker.get_stats()
    assert stats["total"] == 1
    assert stats["closed"] == 1

    # Persistence: reload from disk
    tracker2 = IssueTracker(issues_dir=tmp_path / "issues")
    reloaded = tracker2.get(issue.id)
    assert reloaded is not None
    assert reloaded.status.value == "closed"


# ── Test 2: Self-audit tier 1 (static analysis) ──

@pytest.mark.asyncio
async def test_self_audit_tier1_runs(tmp_path):
    """Verify static analysis audit runs without crashing."""
    from anima.core.self_audit import SelfAudit
    from anima.core.issue_tracker import IssueTracker

    tracker = IssueTracker(issues_dir=tmp_path / "issues")
    audit = SelfAudit(issue_tracker=tracker)

    result = await audit.run_tier(1)
    assert result.tier == 1
    assert result.name == "static_analysis"
    assert isinstance(result.passed, bool)
    assert isinstance(result.findings, list)
    assert result.duration_s >= 0


# ── Test 3: Evolution engine has PR deployment ──

def test_evolution_engine_has_pr_deploy():
    """Verify evolution engine has evo/* branch + PR deploy method."""
    from anima.evolution.engine import EvolutionEngine

    engine = EvolutionEngine()
    assert hasattr(engine, '_deploy_via_pr'), "Missing _deploy_via_pr method"


# ── Test 4: Idle scheduler audit tasks ──

def test_idle_scheduler_has_audit_tasks():
    """Verify idle scheduler includes 4 audit tasks in pool."""
    from anima.core.idle_scheduler import _default_task_pool

    pool = _default_task_pool()
    task_ids = {t.id for t in pool}

    assert "audit_static" in task_ids, "Missing audit_static idle task"
    assert "audit_tests" in task_ids, "Missing audit_tests idle task"
    assert "audit_deps" in task_ids, "Missing audit_deps idle task"
    assert "audit_issues" in task_ids, "Missing audit_issues idle task"

    # Verify correct weights and levels
    task_map = {t.id: t for t in pool}
    assert task_map["audit_static"].weight.name == "LIGHT"
    assert task_map["audit_static"].min_idle_level == "light"
    assert task_map["audit_tests"].weight.name == "MEDIUM"
    assert task_map["audit_tests"].min_idle_level == "moderate"
    assert task_map["audit_deps"].weight.name == "MEDIUM"
    assert task_map["audit_deps"].min_idle_level == "moderate"
    assert task_map["audit_issues"].weight.name == "HEAVY"
    assert task_map["audit_issues"].min_idle_level == "deep"


# ── Test 5: Audit tools registration ──

def test_audit_tools_available():
    """Verify audit tools can be imported and return correct specs."""
    from anima.tools.builtin.audit_tools import get_audit_tools

    tools = get_audit_tools()
    tool_names = {t.name for t in tools}
    assert tool_names == {"audit_run", "audit_status", "issue_manage"}
