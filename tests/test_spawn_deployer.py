"""Tests for deployment planning and known-node deployment routing."""

from __future__ import annotations

from pathlib import Path

import pytest

from anima.spawn.deployer import deploy_to_known_node


@pytest.mark.asyncio
async def test_deploy_to_known_node_uses_profile_and_local_overrides(monkeypatch, tmp_path):
    package_args: dict = {}
    deploy_calls: dict = {}

    def fake_package(**kwargs):
        package_args.update(kwargs)
        path = tmp_path / "spawn.tar.gz"
        path.write_bytes(b"package")
        return path

    async def fake_windows_deploy(**kwargs):
        deploy_calls.update(kwargs)
        return {"success": True, "message": "ok"}

    monkeypatch.setattr(
        "anima.spawn.deployer.get_known_remote_node",
        lambda name: {
            "name": "pidog",
            "platform": "windows",
            "host": "100.88.10.2",
            "hosts": ["192.168.1.174"],
            "user": "eva",
            "password": "secret",
            "profile": "edge-pidog",
            "install_service": True,
            "local_overrides": {
                "machine": {"hostname": "PiDog", "platform": "linux"},
                "network": {"peers": ["192.168.1.10:9420"]},
            },
        },
    )
    monkeypatch.setattr("anima.spawn.deployer.create_spawn_package", fake_package)
    monkeypatch.setattr("anima.spawn.deployer._deploy_known_windows", fake_windows_deploy)

    result = await deploy_to_known_node("pidog")

    assert result["success"] is True
    assert package_args["profile"] == "edge-pidog"
    assert package_args["edge_mode"] is True
    assert package_args["install_service"] is True
    assert package_args["local_overrides"]["machine"]["hostname"] == "PiDog"
    assert deploy_calls["host"] == "100.88.10.2"
    assert deploy_calls["plan"]["platform"] == "windows"
    assert deploy_calls["plan"]["edge_mode"] is True
