"""Tests for the L0 skill capability/permission model (track B)."""
from __future__ import annotations

import pytest

from anima.skills.permissions import (
    SkillPermissions, build_skill_env, is_skill_approved,
    requires_install_approval,
)


def test_permissions_from_meta_undeclared():
    p = SkillPermissions.from_meta({"name": "x"})
    assert p.declared is False
    assert p.is_elevated() is False  # nothing requested


def test_permissions_from_meta_declared():
    p = SkillPermissions.from_meta({"permissions": {
        "shell": True, "network": ["api.github.com"],
        "filesystem": {"read": ["./data"], "write": ["./out"]},
        "risk": "high",
    }})
    assert p.declared and p.shell and p.network == ["api.github.com"]
    assert p.fs_write == ["./out"] and p.risk == "high"
    assert p.is_elevated() is True


def test_permissions_low_not_elevated():
    p = SkillPermissions.from_meta({"permissions": {"shell": False, "risk": "low"}})
    assert p.declared and not p.is_elevated()


def test_build_skill_env_strips_secrets(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-y")
    monkeypatch.setenv("MY_HARMLESS_VAR", "ok")
    env = build_skill_env()
    assert "DATABASE_URL" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "DEEPSEEK_API_KEY" not in env
    assert env.get("MY_HARMLESS_VAR") == "ok"
    assert env.get("PYTHONIOENCODING") == "utf-8"


def test_requires_install_approval():
    low = SkillPermissions.from_meta({"permissions": {"shell": False, "risk": "low"}})
    elevated = SkillPermissions.from_meta({"permissions": {"shell": True}})
    undeclared = SkillPermissions.from_meta({})

    assert requires_install_approval("https://github.com/x/y", low)[0] is True   # remote
    assert requires_install_approval("git@github.com:x/y", low)[0] is True
    assert requires_install_approval("/local/path", elevated)[0] is True          # elevated
    assert requires_install_approval("/local/path", undeclared)[0] is True        # undeclared
    assert requires_install_approval("/local/path", low)[0] is False              # local + declared + low


def test_is_skill_approved(tmp_path, monkeypatch):
    import anima.skills.permissions as perms
    monkeypatch.setattr(perms, "skill_approvals_dir", lambda: tmp_path)
    assert is_skill_approved("foo") is False
    (tmp_path / "foo.approved").write_text("ok", encoding="utf-8")
    assert is_skill_approved("foo") is True
