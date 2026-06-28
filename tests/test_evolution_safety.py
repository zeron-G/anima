"""Tests for the evolution-safety foundation (docs/EVOLUTION_SAFETY_DESIGN.md):
frozen recovery core, governance gate, watchdog de-blinding, boot-health anchor.
All pure logic — no network, no live process.
"""
from __future__ import annotations

import json

import pytest

from anima.guardian import frozen
from anima import watchdog as wd


# ── Frozen recovery core ──
@pytest.mark.parametrize("path", [
    "anima/guardian/sentinel.py",
    "anima/guardian/frozen.py",
    "anima/watchdog.py",
    "anima/core/reload.py",
    "anima/core/governance.py",
    "anima/core/boot_health.py",
    "anima/evolution/engine.py",
    "anima/main.py",
    "anima/__main__.py",
    r"anima\core\reload.py",                     # Windows separators
    "./anima/guardian/handoff.py",               # leading ./
    "D:/data/code/github/anima/anima/main.py",   # absolute
])
def test_frozen_paths_blocked(path):
    assert frozen.is_frozen(path) is True


@pytest.mark.parametrize("path", [
    "anima/core/cognitive.py",
    "anima/tools/builtin/evolution_tools.py",
    "anima/api/chat.py",
    "README.md",
    "",
])
def test_non_frozen_paths_allowed(path):
    assert frozen.is_frozen(path) is False


def test_frozen_hits_subset():
    files = ["anima/api/chat.py", "anima/watchdog.py", "anima/core/cognitive.py"]
    assert frozen.frozen_hits(files) == ["anima/watchdog.py"]


# ── Governance gate ──
def _gov():
    from anima.core.governance import GovernanceEngine
    return GovernanceEngine()


def test_governance_rejects_frozen_no_override():
    gov = _gov()
    ok, reason = gov.check_evolution_proposal(
        {"id": "p1", "files": ["anima/watchdog.py"]}, recent_failures=0)
    assert ok is False and "FROZEN" in reason


def test_governance_core_requires_approval(tmp_path, monkeypatch):
    from anima.core.governance import GovernanceEngine
    monkeypatch.setattr(GovernanceEngine, "approvals_dir", staticmethod(lambda: tmp_path))
    gov = _gov()
    prop = {"id": "p2", "files": ["anima/core/cognitive.py"]}
    ok, reason = gov.check_evolution_proposal(prop, recent_failures=0)
    assert ok is False and "human approval" in reason
    # Drop the out-of-band approval token → now allowed.
    (tmp_path / "p2.approved").write_text("ok", encoding="utf-8")
    ok2, _ = gov.check_evolution_proposal(prop, recent_failures=0)
    assert ok2 is True


def test_governance_allows_normal_file():
    gov = _gov()
    ok, _ = gov.check_evolution_proposal(
        {"id": "p3", "files": ["anima/api/chat.py"]}, recent_failures=0)
    assert ok is True


def test_governance_failure_cooldown():
    gov = _gov()
    ok, reason = gov.check_evolution_proposal(
        {"id": "p4", "files": ["anima/api/chat.py"]}, recent_failures=3)
    assert ok is False and "failures" in reason


# ── Watchdog de-blinding ──
def test_marker_draining_fresh_vs_stale():
    now = 1000.0
    assert wd.marker_draining(None, now) is False
    assert wd.marker_draining({"ts": now - 10}, now) is True
    # A leaked / stale marker must NOT blind liveness forever (P0-4).
    assert wd.marker_draining({"ts": now - wd.RESTART_MARKER_MAX_AGE_S - 1}, now) is False


def test_classify_exit():
    assert wd.classify_exit(0, {"reason": "x"}) == "requested_restart"
    assert wd.classify_exit(1, {"reason": "x"}) == "requested_restart"
    assert wd.classify_exit(0, None) == "stop"
    assert wd.classify_exit(1, None) == "crash"


def test_liveness_verdict():
    assert wd.liveness_verdict(hb_age=999, healthz_ok=False, tick_frozen=True, draining=True) == "alive"
    assert wd.liveness_verdict(hb_age=10, healthz_ok=True, tick_frozen=True, draining=False) == "brain_frozen"
    assert wd.liveness_verdict(hb_age=999, healthz_ok=False, tick_frozen=False, draining=False) == "hung"
    assert wd.liveness_verdict(hb_age=10, healthz_ok=True, tick_frozen=False, draining=False) == "alive"


# ── Boot-health known-good anchor ──
def test_known_good_roundtrip(tmp_path, monkeypatch):
    from anima.core import boot_health as bh
    p = tmp_path / "known_good.json"
    monkeypatch.setattr(bh, "_known_good_path", lambda: p)
    monkeypatch.setattr(bh, "current_commit", lambda: "abc123def456")
    assert bh.get_known_good() is None
    bh.record_known_good()
    assert bh.get_known_good() == "abc123def456"
    assert json.loads(p.read_text())["commit"] == "abc123def456"


def test_revert_noop_when_already_known_good(tmp_path, monkeypatch):
    from anima.core import boot_health as bh
    p = tmp_path / "known_good.json"
    p.write_text(json.dumps({"commit": "samesha", "ts": 0}), encoding="utf-8")
    monkeypatch.setattr(bh, "_known_good_path", lambda: p)
    monkeypatch.setattr(bh, "current_commit", lambda: "samesha")
    # Already at known-good → not a regression → no reset attempted.
    assert bh.revert_to_known_good("test") is None
