"""Tests for edge-runtime profiles and deployment packaging."""

from __future__ import annotations

import tarfile

from anima.config import active_profile, available_profiles, load_config
from anima.spawn.packager import create_spawn_package


def test_edge_profile_is_available():
    assert "edge-pidog" in available_profiles()


def test_load_edge_profile_without_local_overrides():
    cfg = load_config(profile="edge-pidog", include_local=False)

    assert active_profile() == "edge-pidog"
    assert cfg["runtime"]["profile"] == "edge-pidog"
    assert cfg["runtime"]["role"] == "edge_embodied"
    assert cfg["runtime"]["embodiment"] == "robot_dog"
    assert cfg["robotics"]["enabled"] is True
    assert cfg["robotics"]["nodes"][0]["base_urls"] == ["http://127.0.0.1:8888"]
    assert cfg["channels"]["discord"]["enabled"] is False


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

        service = tar.extractfile("deploy/anima-edge.service").read().decode("utf-8")
        assert "Environment=ANIMA_PROFILE=edge-pidog" in service
        assert "ExecStart=__ANIMA_DIR__/.venv/bin/python -m anima --edge" in service
