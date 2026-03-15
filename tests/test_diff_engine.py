"""Tests for the diff engine."""

import pytest

from anima.perception.diff_engine import DiffEngine
from anima.models.perception_frame import DiffRule


@pytest.fixture
def engine(default_diff_rules):
    return DiffEngine(default_diff_rules)


def test_first_snapshot_no_significance(engine):
    """First snapshot (no previous) should have zero significance."""
    diff = engine.compute_diff({"cpu_percent": 50}, None)
    assert diff.significance_score == 0.0
    assert not diff.has_alerts


def test_no_change(engine):
    state = {"cpu_percent": 50, "memory_percent": 60, "disk_percent": 70, "process_count": 100}
    diff = engine.compute_diff(state, state)
    assert diff.significance_score == 0.0
    assert not diff.has_alerts
    # All deltas should be 0
    for fd in diff.field_diffs.values():
        assert fd.delta == 0.0
        assert not fd.significant


def test_cpu_significant_change(engine):
    prev = {"cpu_percent": 50, "memory_percent": 60, "disk_percent": 70, "process_count": 100}
    curr = {"cpu_percent": 70, "memory_percent": 60, "disk_percent": 70, "process_count": 100}
    diff = engine.compute_diff(curr, prev)
    assert diff.field_diffs["cpu_percent"].significant  # delta=20 > threshold=15
    assert diff.field_diffs["cpu_percent"].delta == 20.0
    assert diff.significance_score > 0


def test_cpu_below_threshold(engine):
    prev = {"cpu_percent": 50}
    curr = {"cpu_percent": 60}  # delta=10 < threshold=15
    diff = engine.compute_diff(curr, prev)
    assert not diff.field_diffs["cpu_percent"].significant


def test_file_changes_any_triggers(engine):
    prev = {"file_changes": []}
    curr = {"file_changes": ["a.py"]}
    diff = engine.compute_diff(curr, prev)
    assert diff.field_diffs["file_changes"].significant


def test_file_changes_empty_not_significant(engine):
    prev = {"file_changes": []}
    curr = {"file_changes": []}
    diff = engine.compute_diff(curr, prev)
    assert not diff.field_diffs["file_changes"].significant


def test_alert_on_high_cpu(engine):
    prev = {"cpu_percent": 50}
    curr = {"cpu_percent": 95}  # > 90 alert threshold
    diff = engine.compute_diff(curr, prev)
    assert diff.has_alerts


def test_alert_on_high_disk(engine):
    prev = {"disk_percent": 80}
    curr = {"disk_percent": 96}  # > 95 alert threshold
    diff = engine.compute_diff(curr, prev)
    assert diff.has_alerts


def test_no_alert_below_threshold(engine):
    prev = {"cpu_percent": 50}
    curr = {"cpu_percent": 85}  # < 90
    diff = engine.compute_diff(curr, prev)
    assert not diff.has_alerts


def test_significance_score_fraction(engine):
    """Significance score = fraction of rules that triggered."""
    prev = {
        "cpu_percent": 50, "memory_percent": 50,
        "disk_percent": 50, "file_changes": [], "process_count": 100,
    }
    curr = {
        "cpu_percent": 80,  # +30 > 15 ✓
        "memory_percent": 65,  # +15 > 10 ✓
        "disk_percent": 52,  # +2 < 5 ✗
        "file_changes": ["x.py"],  # > 0 ✓
        "process_count": 103,  # +3 < 5 ✗
    }
    diff = engine.compute_diff(curr, prev)
    assert diff.significance_score == pytest.approx(3 / 5)
    assert len(diff.significant_fields) == 3


def test_from_config():
    cfg = {
        "cpu_percent": {"threshold": 15},
        "memory_percent": {"threshold": 10},
    }
    engine = DiffEngine.from_config(cfg)
    prev = {"cpu_percent": 50, "memory_percent": 50}
    curr = {"cpu_percent": 70, "memory_percent": 55}
    diff = engine.compute_diff(curr, prev)
    assert diff.field_diffs["cpu_percent"].significant
    assert not diff.field_diffs["memory_percent"].significant
