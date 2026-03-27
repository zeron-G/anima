"""Deployment target helpers for spawn/deploy workflows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from anima.config import get, resolve_profile_path


def get_known_remote_node(name: str) -> dict[str, Any] | None:
    """Return a configured remote node entry from local config."""
    needle = str(name or "").strip().lower()
    if not needle:
        return None

    for entry in get("network.remote_nodes", []) or []:
        if str(entry.get("name", "") or "").strip().lower() == needle:
            return dict(entry)
    return None


def profile_deploy_defaults(profile: str) -> dict[str, Any]:
    """Load deploy defaults from a committed runtime profile."""
    profile_name = str(profile or "").strip()
    if not profile_name:
        return {}

    path = resolve_profile_path(profile_name)
    if not path.exists():
        return {}

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    deploy = dict(raw.get("deploy") or {})
    runtime = dict(raw.get("runtime") or {})

    if "edge_mode" not in deploy:
        mode = str(deploy.get("mode", "") or "").strip().lower()
        if mode:
            deploy["edge_mode"] = mode == "edge"
        else:
            deploy["edge_mode"] = str(runtime.get("role", "") or "").startswith("edge")

    return deploy


def resolve_deploy_plan(
    *,
    profile: str = "",
    edge_mode: bool | None = None,
    install_service: bool | None = None,
    install_dir: str = "",
    python_cmd: str = "",
    include_env: bool | None = None,
    node_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve effective deployment settings from profile + node metadata."""
    node_cfg = dict(node_cfg or {})
    deploy_cfg = dict(node_cfg.get("deploy") or {})
    edge_cfg = dict(get("edge", {}) or {})

    profile_name = (
        str(profile or "").strip()
        or str(deploy_cfg.get("profile", "") or "").strip()
        or str(node_cfg.get("profile", "") or "").strip()
    )

    if not profile_name and edge_mode:
        profile_name = str(edge_cfg.get("default_profile", "edge-pidog") or "edge-pidog")

    profile_defaults = profile_deploy_defaults(profile_name)
    resolved_edge_mode = (
        bool(edge_mode)
        if edge_mode is not None
        else bool(
            deploy_cfg.get(
                "edge_mode",
                node_cfg.get(
                    "edge_mode",
                    profile_defaults.get("edge_mode", False),
                ),
            )
        )
    )

    if not profile_name and resolved_edge_mode:
        profile_name = str(
            deploy_cfg.get("profile")
            or node_cfg.get("profile")
            or edge_cfg.get("default_profile", "edge-pidog")
            or "edge-pidog"
        )
        profile_defaults = profile_deploy_defaults(profile_name)

    resolved_install_service = (
        bool(install_service)
        if install_service is not None
        else bool(
            deploy_cfg.get(
                "install_service",
                node_cfg.get(
                    "install_service",
                    profile_defaults.get(
                        "install_service",
                        edge_cfg.get("auto_install_service", True) if resolved_edge_mode else False,
                    ),
                ),
            )
        )
    )

    resolved_install_dir = (
        str(install_dir or "").strip()
        or str(deploy_cfg.get("install_dir", "") or "").strip()
        or str(node_cfg.get("install_dir", "") or "").strip()
        or str(profile_defaults.get("install_dir", "") or "").strip()
        or (
            str(edge_cfg.get("install_dir", "~/.anima-edge") or "~/.anima-edge")
            if resolved_edge_mode
            else "~/.anima"
        )
    )

    platform = (
        str(deploy_cfg.get("platform", "") or "").strip().lower()
        or str(node_cfg.get("platform", "") or "").strip().lower()
    )
    if not platform:
        platform = "windows" if "\\" in resolved_install_dir else "linux"

    resolved_python = (
        str(python_cmd or "").strip()
        or str(deploy_cfg.get("python_cmd", "") or "").strip()
        or str(node_cfg.get("python_cmd", "") or "").strip()
        or str(profile_defaults.get("python_cmd", "") or "").strip()
        or ("python" if platform == "windows" else "python3")
    )

    resolved_include_env = (
        bool(include_env)
        if include_env is not None
        else bool(
            deploy_cfg.get(
                "include_env",
                node_cfg.get("include_env", True),
            )
        )
    )

    local_overrides = dict(node_cfg.get("local_overrides") or {})

    return {
        "profile": profile_name,
        "edge_mode": resolved_edge_mode,
        "install_service": resolved_install_service,
        "install_dir": resolved_install_dir,
        "python_cmd": resolved_python,
        "include_env": resolved_include_env,
        "platform": platform,
        "service_name": str(edge_cfg.get("service_name", "anima-edge") or "anima-edge"),
        "local_overrides": local_overrides,
    }
