"""Spawn packager — creates a deployment package for a new ANIMA node.

Packages the source code, config, persona, and bootstrap script into a
tar.gz archive that can be deployed to a remote machine.
"""

from __future__ import annotations

import io
import json
import tarfile
import time
from pathlib import Path

from anima.config import project_root, get
from anima.network.discovery import get_local_ip
from anima.network.node import NodeIdentity
from anima.utils.ids import gen_id
from anima.utils.logging import get_logger

log = get_logger("spawn.packager")


def create_spawn_package(
    output_path: str | None = None,
    parent_address: str | None = None,
    network_secret: str = "",
    include_env: bool = False,
    profile: str = "",
    edge_mode: bool = False,
    install_service: bool = False,
) -> Path:
    """Create a spawn package (tar.gz) for deploying to a new node.

    Args:
        output_path: Where to save the tar.gz. Default: data/spawn-{timestamp}.tar.gz
        parent_address: ip:port of the parent node (for peers config)
        network_secret: Shared secret for the network
        include_env: Whether to include .env file (with API keys)
        profile: Optional runtime profile such as "edge-pidog"
        edge_mode: Whether the package should start ANIMA via `python -m anima --edge`
        install_service: Whether to include and install a user-level systemd service on Linux

    Returns:
        Path to the created tar.gz file.
    """
    root = project_root()
    ts = int(time.time())

    if output_path is None:
        out_dir = root / "data"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(out_dir / f"spawn-{ts}.tar.gz")

    # Generate a new node ID for the child
    child_id = f"anima-spawn-{gen_id('')[:8]}"

    # Build config with network enabled and parent as peer
    if parent_address is None:
        parent_address = f"{get_local_ip()}:{get('network.listen_port', 9420)}"

    log.info(
        "Creating spawn package: %s (parent: %s, profile=%s, edge=%s)",
        output_path,
        parent_address,
        profile or "default",
        edge_mode,
    )

    with tarfile.open(output_path, "w:gz") as tar:
        # Source code
        for d in ["anima", "config", "prompts", "agents", "skills", "docs"]:
            src = root / d
            if src.exists():
                _add_dir_to_tar(tar, src, d, exclude={
                    "__pycache__", ".pyc", ".egg-info", ".git",
                    "data", ".venv", "venv",
                })

        # pyproject.toml + requirements.txt
        for f in ["pyproject.toml", "requirements.txt"]:
            fp = root / f
            if fp.exists():
                tar.add(str(fp), arcname=f)

        # local/env example (safe to ship, unlike local/env.yaml)
        local_env_example = root / "local" / "env.yaml.example"
        if local_env_example.exists():
            tar.add(str(local_env_example), arcname="local/env.yaml.example")

        service_name = get("edge.service_name", "anima-edge")

        # Bootstrap scripts
        _add_string_to_tar(
            tar,
            "bootstrap.sh",
            _render_bootstrap_sh(
                profile=profile,
                edge_mode=edge_mode,
                install_service=install_service,
                service_name=service_name,
            ),
        )
        _add_string_to_tar(
            tar,
            "bootstrap.ps1",
            _render_bootstrap_ps1(profile=profile, edge_mode=edge_mode),
        )
        if install_service:
            _add_string_to_tar(
                tar,
                f"deploy/{service_name}.service",
                _render_systemd_service(
                    profile=profile,
                    edge_mode=edge_mode,
                ),
            )

        # Child node.json
        node_data = json.dumps({
            "self_id": child_id,
            "registered_nodes": [
                {"id": child_id, "joined_at": ts, "status": "alive"},
            ],
            "created_at": ts,
            "spawned_from": NodeIdentity().node_id,
        }, indent=2)
        _add_string_to_tar(tar, "data/node.json", node_data)

        # Config with network enabled
        config_path = root / "config" / "default.yaml"
        if config_path.exists():
            import yaml
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            cfg["network"] = {
                "enabled": True,
                "listen_port": get("network.listen_port", 9420),
                "secret": network_secret or get("network.secret", ""),
                "peers": [parent_address],
            }
            runtime_cfg = cfg.setdefault("runtime", {})
            runtime_cfg["profile"] = profile or runtime_cfg.get("profile", "default")
            # Disable Discord on child nodes (only parent runs the bot)
            if "channels" in cfg:
                cfg["channels"]["discord"] = {"enabled": False, "token": "", "allowed_users": []}
            cfg_text = yaml.dump(cfg, default_flow_style=False, allow_unicode=True)
            _add_string_to_tar(tar, "config/default.yaml", cfg_text)

        # Gitkeep dirs
        for d in ["data/logs", "data/workspace", "data/uploads", "data/notes"]:
            _add_string_to_tar(tar, f"{d}/.gitkeep", "")

        # .env (optional)
        if include_env:
            env_path = root / ".env"
            if env_path.exists():
                tar.add(str(env_path), arcname=".env")

        # .env.example
        env_example = root / ".env.example"
        if env_example.exists():
            tar.add(str(env_example), arcname=".env.example")

    log.info("Spawn package created: %s (%d bytes)", output_path,
             Path(output_path).stat().st_size)
    return Path(output_path)


def _render_bootstrap_sh(
    *,
    profile: str,
    edge_mode: bool,
    install_service: bool,
    service_name: str,
) -> str:
    profile_env = f'export ANIMA_PROFILE="{profile}"\n' if profile else ""
    cli_args = "--edge" if edge_mode else "--headless"
    install_service_block = ""
    if install_service:
        install_service_block = f"""
# Install user-level systemd service when available
mkdir -p "$HOME/.config/systemd/user"
sed "s|__ANIMA_DIR__|$ANIMA_DIR|g" "deploy/{service_name}.service" > "$HOME/.config/systemd/user/{service_name}.service"
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload || true
  systemctl --user enable --now {service_name}.service || true
else
  echo "systemctl not available; falling back to nohup launch"
  nohup env ANIMA_PROFILE="{profile}" "$ANIMA_DIR/.venv/bin/python" -m anima {cli_args} > data/logs/anima.log 2>&1 &
  echo $! > data/anima.pid
fi
"""
    else:
        install_service_block = f"""
# Start
nohup env ANIMA_PROFILE="{profile}" "$ANIMA_DIR/.venv/bin/python" -m anima {cli_args} > data/logs/anima.log 2>&1 &
echo $! > data/anima.pid
"""

    return f"""#!/bin/bash
# ANIMA Node Bootstrap — one-command deployment
set -e
ANIMA_DIR="${{ANIMA_INSTALL_DIR:-$HOME/.anima}}"
PYTHON="${{PYTHON_CMD:-python3}}"
echo "=== ANIMA Node Bootstrap ==="
echo "Install dir: $ANIMA_DIR"
# Check Python
$PYTHON --version || {{ echo "ERROR: Python 3.11+ required"; exit 1; }}
# Create install dir
mkdir -p "$ANIMA_DIR"
cp -r ./* "$ANIMA_DIR/" 2>/dev/null || true
cd "$ANIMA_DIR"
# Create venv
$PYTHON -m venv .venv
source .venv/bin/activate
# Install
pip install -e . --quiet 2>&1 | tail -3
# Permissions
chmod 600 .env 2>/dev/null || true
{profile_env}{install_service_block}
echo "=== ANIMA started ==="
echo "Dashboard: http://$(hostname -I 2>/dev/null | awk '{{print $1}}' || echo localhost):$(python -c 'import yaml; print(yaml.safe_load(open(\"config/default.yaml\")).get(\"dashboard\",{{}}).get(\"port\",8420))')"
"""


def _render_bootstrap_ps1(*, profile: str, edge_mode: bool) -> str:
    cli_args = "--edge" if edge_mode else "--headless"
    profile_block = ""
    if profile:
        profile_block = f'$env:ANIMA_PROFILE = "{profile}"'
    return f"""# ANIMA Node Bootstrap — Windows PowerShell
$ErrorActionPreference = "Stop"
$ANIMA_DIR = if ($env:ANIMA_INSTALL_DIR) {{ $env:ANIMA_INSTALL_DIR }} else {{ "$env:USERPROFILE\\.anima" }}
$PYTHON = if ($env:PYTHON_CMD) {{ $env:PYTHON_CMD }} else {{ "python" }}
Write-Host "=== ANIMA Node Bootstrap ==="
Write-Host "Install dir: $ANIMA_DIR"
& $PYTHON --version
if ($LASTEXITCODE -ne 0) {{ throw "Python 3.11+ required" }}
New-Item -ItemType Directory -Force -Path $ANIMA_DIR | Out-Null
Copy-Item -Recurse -Force .\\* $ANIMA_DIR\\
Set-Location $ANIMA_DIR
& $PYTHON -m venv .venv
.\\.venv\\Scripts\\Activate
pip install -e . --quiet
{profile_block}
Start-Process -NoNewWindow -FilePath python -ArgumentList "-m anima {cli_args}" -RedirectStandardOutput "data\\logs\\anima.log"
Write-Host "=== ANIMA started ==="
"""


def _render_systemd_service(*, profile: str, edge_mode: bool) -> str:
    cli_args = "--edge" if edge_mode else "--headless"
    environment = f'Environment=ANIMA_PROFILE={profile}\n' if profile else ""
    return f"""[Unit]
Description=ANIMA Edge Runtime
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=__ANIMA_DIR__
{environment}ExecStart=__ANIMA_DIR__/.venv/bin/python -m anima {cli_args}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def _add_dir_to_tar(tar: tarfile.TarFile, src: Path, arcname: str,
                    exclude: set | None = None) -> None:
    exclude = exclude or set()
    for item in src.rglob("*"):
        # Check relative path parts only (not absolute path)
        rel_parts = item.relative_to(src).parts
        if any(ex in rel_parts or item.name.endswith(ex) for ex in exclude):
            continue
        if item.is_file():
            rel = item.relative_to(src.parent)
            tar.add(str(item), arcname=str(rel).replace("\\", "/"))


def _add_string_to_tar(tar: tarfile.TarFile, arcname: str, content: str) -> None:
    data = content.encode("utf-8")
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mtime = int(time.time())
    tar.addfile(info, io.BytesIO(data))
