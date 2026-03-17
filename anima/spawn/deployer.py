"""Spawn deployer — deploy ANIMA to a remote machine via SSH."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from anima.spawn.packager import create_spawn_package
from anima.utils.logging import get_logger

log = get_logger("spawn.deployer")


async def deploy_to_remote(
    target: str,
    python_cmd: str = "python3",
    install_dir: str = "",
    network_secret: str = "",
    include_env: bool = True,
    timeout: int = 300,
) -> dict:
    """Deploy ANIMA to a remote machine via SSH + SCP.

    Args:
        target: SSH target (user@host)
        python_cmd: Python command on remote machine
        install_dir: Install directory on remote (default: ~/.anima)
        network_secret: Shared network secret
        include_env: Whether to send .env with API keys
        timeout: Total timeout in seconds

    Returns:
        {"success": bool, "node_id": str, "message": str}
    """
    log.info("Deploying to %s (python=%s)", target, python_cmd)

    # 1. Create spawn package
    package = create_spawn_package(
        network_secret=network_secret,
        include_env=include_env,
    )

    try:
        remote_dir = install_dir or "~/.anima"
        remote_tmp = f"/tmp/anima-spawn-{os.getpid()}.tar.gz"

        # 2. SCP package to remote
        log.info("Uploading package to %s...", target)
        scp_result = await _run_cmd(
            f'scp -o StrictHostKeyChecking=no "{package}" {target}:{remote_tmp}',
            timeout=120,
        )
        if scp_result["returncode"] != 0:
            return {"success": False, "node_id": "", "message": f"SCP failed: {scp_result['stderr']}"}

        # 3. Extract and bootstrap on remote
        log.info("Bootstrapping on remote...")
        bootstrap_cmds = f"""
            mkdir -p {remote_dir} &&
            cd /tmp && tar xzf {remote_tmp} &&
            cd /tmp && ANIMA_INSTALL_DIR={remote_dir} PYTHON_CMD={python_cmd} bash bootstrap.sh &&
            rm -f {remote_tmp}
        """
        ssh_result = await _run_cmd(
            f'ssh -o StrictHostKeyChecking=no {target} "{bootstrap_cmds}"',
            timeout=timeout,
        )

        if ssh_result["returncode"] != 0:
            return {"success": False, "node_id": "",
                    "message": f"Bootstrap failed: {ssh_result['stderr'][:300]}"}

        log.info("Deploy successful: %s", target)
        return {
            "success": True,
            "node_id": "",  # Will be known after gossip discovery
            "message": f"Deployed to {target}. Node will join network via gossip.",
            "stdout": ssh_result["stdout"][:500],
        }

    finally:
        # Clean up local package
        try:
            package.unlink()
        except Exception as e:
            log.warning("deployer: %s", e)


async def deploy_local(
    install_dir: str,
    port: int = 8421,
    network_secret: str = "",
) -> dict:
    """Deploy a second ANIMA node locally (for testing).

    Args:
        install_dir: Where to install the second node
        port: Dashboard port (different from primary)
        network_secret: Shared network secret

    Returns:
        {"success": bool, "node_id": str, "pid": int}
    """
    log.info("Deploying local node to %s", install_dir)

    package = create_spawn_package(
        network_secret=network_secret,
        include_env=True,
    )

    try:
        install = Path(install_dir)
        install.mkdir(parents=True, exist_ok=True)

        # Extract
        import tarfile
        with tarfile.open(str(package), "r:gz") as tar:
            tar.extractall(str(install))

        # Modify config for different ports
        import yaml
        config_path = install / "config" / "default.yaml"
        if config_path.exists():
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            cfg["dashboard"] = {"port": port}
            cfg["network"]["listen_port"] = get_free_port()
            config_path.write_text(yaml.dump(cfg, default_flow_style=False))

        # Start
        python = sys.executable
        proc = await asyncio.create_subprocess_exec(
            python, "-m", "anima",
            cwd=str(install),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        log.info("Local node started: PID=%d, dir=%s", proc.pid, install_dir)
        return {"success": True, "node_id": "", "pid": proc.pid}

    finally:
        try:
            package.unlink()
        except Exception as e:
            log.warning("deployer: %s", e)


def get_free_port() -> int:
    """Get a free TCP port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


async def _run_cmd(cmd: str, timeout: int = 60) -> dict:
    """Run a shell command and return result."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "returncode": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace").strip(),
            "stderr": stderr.decode("utf-8", errors="replace").strip(),
        }
    except asyncio.TimeoutError:
        return {"returncode": -1, "stdout": "", "stderr": "Command timed out"}
