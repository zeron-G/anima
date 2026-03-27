"""Tests for edge-runtime profiles, startup behavior, and deployment packaging."""

from __future__ import annotations

import tarfile

from anima.config import active_profile, available_profiles, load_config
from anima.spawn.packager import create_spawn_package
from anima.startup_check import verify_dependencies


def test_edge_profile_is_available():
    assert "edge-pidog" in available_profiles()


def test_load_edge_profile_without_local_overrides():
    cfg = load_config(profile="edge-pidog", include_local=False)

    assert active_profile() == "edge-pidog"
    assert cfg["runtime"]["profile"] == "edge-pidog"
    assert cfg["runtime"]["role"] == "edge_embodied"
    assert cfg["runtime"]["embodiment"] == "robot_dog"
    assert cfg["runtime"]["require_llm_credentials"] is False
    assert cfg["robotics"]["enabled"] is True
    assert cfg["robotics"]["nodes"][0]["base_urls"] == ["http://127.0.0.1:8888"]
    assert cfg["channels"]["discord"]["enabled"] is False


def test_edge_profile_allows_degraded_start_without_llm(monkeypatch):
    cfg = load_config(profile="edge-pidog", include_local=False)

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("anima.startup_check._has_claude_code_credentials", lambda: False)

    issues = verify_dependencies(cfg)

    assert not any(
        severity == "critical" and "No LLM credentials found" in message
        for severity, message in issues
    )
    assert any(
        severity == "warning" and "No LLM credentials found" in message
        for severity, message in issues
    )


def test_default_runtime_still_requires_llm_credentials(monkeypatch):
    monkeypatch.delenv("ANIMA_PROFILE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("anima.startup_check._has_claude_code_credentials", lambda: False)

    cfg = load_config(include_local=False)
    issues = verify_dependencies(cfg)

    assert any(
        severity == "critical" and "No LLM credentials found" in message
        for severity, message in issues
    )


def test_edge_spawn_package_contains_profile_and_service(tmp_path):
    package_path = create_spawn_package(
        output_path=str(tmp_path / "edge-pidog.tar.gz"),
        parent_address="10.0.0.5:9420",
        network_secret="shared-secret",
        include_env=False,
        profile="edge-pidog",
        edge_mode=True,
        install_service=True,
    )

    with tarfile.open(package_path, "r:gz") as tar:
        names = set(tar.getnames())
        assert "config/profiles/edge-pidog.yaml" in names
        assert "local/env.yaml.example" in names
        assert "docs/EDGE_ANIMA.md" in names
        assert "deploy/anima-edge.service" in names

        bootstrap = tar.extractfile("bootstrap.sh").read().decode("utf-8")
        assert 'ANIMA_PROFILE="edge-pidog"' in bootstrap
        assert "-m anima --edge" in bootstrap
        assert 'cp -a ./. "$ANIMA_DIR/"' in bootstrap

        service = tar.extractfile("deploy/anima-edge.service").read().decode("utf-8")
        assert "Environment=ANIMA_PROFILE=edge-pidog" in service
        assert "ExecStart=__ANIMA_DIR__/.venv/bin/python -m anima --edge" in service
