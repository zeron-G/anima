"""Spawn deployer — deploy ANIMA packages to local or remote machines."""

from __future__ import annotations

import asyncio
import os
import shlex
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

from anima.config import get
from anima.spawn.packager import create_spawn_package
from anima.spawn.targets import get_known_remote_node, resolve_deploy_plan
from anima.utils.logging import get_logger

log = get_logger("spawn.deployer")


async def deploy_to_remote(
    target: str,
    python_cmd: str = "",
    install_dir: str = "",
    network_secret: str = "",
    include_env: bool | None = None,
    profile: str = "",
    edge_mode: bool | None = None,
    install_service: bool | None = None,
    timeout: int = 300,
) -> dict:
    """Deploy ANIMA to a remote Linux machine via SSH + SCP."""
    plan = resolve_deploy_plan(
        profile=profile,
        edge_mode=edge_mode,
        install_service=install_service,
        install_dir=install_dir,
        python_cmd=python_cmd,
        include_env=include_env,
    )
    secret = network_secret or get("network.secret", "")

    log.info(
        "Deploying to %s (profile=%s, edge=%s)",
        target,
        plan["profile"] or "default",
        plan["edge_mode"],
    )

    package = create_spawn_package(
        network_secret=secret,
        include_env=plan["include_env"],
        profile=plan["profile"],
        edge_mode=plan["edge_mode"],
        install_service=plan["install_service"],
    )

    try:
        remote_dir = plan["install_dir"]
        remote_tmp = f"/tmp/anima-spawn-{os.getpid()}.tar.gz"

        scp_result = await _run_cmd(
            f'scp -o StrictHostKeyChecking=no "{package}" {target}:{remote_tmp}',
            timeout=120,
        )
        if scp_result["returncode"] != 0:
            return {"success": False, "node_id": "", "message": f"SCP failed: {scp_result['stderr']}"}

        bootstrap_cmds = (
            f"mkdir -p {shlex.quote(remote_dir)} && "
            f"cd /tmp && tar xzf {shlex.quote(remote_tmp)} && "
            f"cd /tmp && ANIMA_INSTALL_DIR={shlex.quote(remote_dir)} "
            f"PYTHON_CMD={shlex.quote(plan['python_cmd'])} bash bootstrap.sh && "
            f"rm -f {shlex.quote(remote_tmp)}"
        )
        ssh_result = await _run_cmd(
            f'ssh -o StrictHostKeyChecking=no {target} "{bootstrap_cmds}"',
            timeout=timeout,
        )
        if ssh_result["returncode"] != 0:
            return {
                "success": False,
                "node_id": "",
                "message": f"Bootstrap failed: {ssh_result['stderr'][:300]}",
            }

        return {
            "success": True,
            "node_id": "",
            "message": f"Deployed to {target}. Node will join network via gossip.",
            "stdout": ssh_result["stdout"][:500],
            "profile": plan["profile"] or "default",
            "edge_mode": plan["edge_mode"],
        }
    finally:
        try:
            package.unlink()
        except Exception as exc:
            log.warning("deployer: %s", exc)


async def deploy_to_known_node(
    node_name: str,
    *,
    profile: str = "",
    edge_mode: bool | None = None,
    install_service: bool | None = None,
    install_dir: str = "",
    python_cmd: str = "",
    network_secret: str = "",
    include_env: bool | None = None,
    timeout: int = 600,
) -> dict:
    """Deploy ANIMA to a configured `network.remote_nodes` target."""
    node_cfg = get_known_remote_node(node_name)
    if node_cfg is None:
        return {"success": False, "node_id": "", "message": f"Unknown configured node '{node_name}'"}

    plan = resolve_deploy_plan(
        profile=profile,
        edge_mode=edge_mode,
        install_service=install_service,
        install_dir=install_dir,
        python_cmd=python_cmd,
        include_env=include_env,
        node_cfg=node_cfg,
    )
    secret = network_secret or get("network.secret", "")

    package = create_spawn_package(
        network_secret=secret,
        include_env=plan["include_env"],
        profile=plan["profile"],
        edge_mode=plan["edge_mode"],
        install_service=plan["install_service"],
        local_overrides=plan["local_overrides"],
    )

    hosts = [node_cfg.get("host", "")] + list(node_cfg.get("hosts") or [])
    hosts = [str(host).strip() for host in hosts if str(host).strip()]
    platform = str(plan["platform"] or "linux").lower()

    try:
        last_error = "no hosts configured"
        for host in hosts:
            try:
                if platform == "windows":
                    result = await _deploy_known_windows(
                        package=package,
                        host=host,
                        user=str(node_cfg.get("user", "") or ""),
                        password=str(node_cfg.get("password", "") or ""),
                        plan=plan,
                        timeout=timeout,
                    )
                else:
                    result = await _deploy_known_linux(
                        package=package,
                        host=host,
                        user=str(node_cfg.get("user", "") or ""),
                        password=str(node_cfg.get("password", "") or ""),
                        plan=plan,
                        timeout=timeout,
                    )
                result.setdefault("profile", plan["profile"] or "default")
                result.setdefault("edge_mode", plan["edge_mode"])
                result.setdefault("platform", platform)
                result.setdefault("host", host)
                result.setdefault("node", node_name)
                if result.get("success"):
                    return result
                last_error = str(result.get("message", "") or result.get("stderr", "") or result)
            except Exception as exc:
                last_error = str(exc)
                log.warning("Deploy to %s via %s failed: %s", node_name, host, exc)

        return {
            "success": False,
            "node_id": "",
            "message": f"Deploy to '{node_name}' failed on all hosts: {last_error}",
            "profile": plan["profile"] or "default",
            "edge_mode": plan["edge_mode"],
            "platform": platform,
        }
    finally:
        try:
            package.unlink()
        except Exception as exc:
            log.warning("deployer: %s", exc)


async def deploy_local(
    install_dir: str,
    port: int = 8421,
    network_secret: str = "",
    profile: str = "",
    edge_mode: bool | None = None,
) -> dict:
    """Deploy a second ANIMA node locally (for testing)."""
    plan = resolve_deploy_plan(
        profile=profile,
        edge_mode=edge_mode,
        install_dir=install_dir,
        include_env=True,
    )
    secret = network_secret or get("network.secret", "")

    package = create_spawn_package(
        network_secret=secret,
        include_env=plan["include_env"],
        profile=plan["profile"],
        edge_mode=plan["edge_mode"],
        install_service=False,
    )

    try:
        install = Path(plan["install_dir"])
        install.mkdir(parents=True, exist_ok=True)

        import tarfile
        with tarfile.open(str(package), "r:gz") as tar:
            tar.extractall(str(install))

        import yaml
        config_path = install / "config" / "default.yaml"
        if config_path.exists():
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            cfg["dashboard"] = {"port": port}
            cfg["network"]["listen_port"] = get_free_port()
            config_path.write_text(yaml.dump(cfg, default_flow_style=False), encoding="utf-8")

        python = sys.executable
        launch_args = [python, "-m", "anima"]
        launch_args.append("--edge" if plan["edge_mode"] else "--headless")

        proc = await asyncio.create_subprocess_exec(
            *launch_args,
            cwd=str(install),
            env={
                **os.environ,
                "ANIMA_PROFILE": plan["profile"],
            } if plan["profile"] else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        return {
            "success": True,
            "node_id": "",
            "pid": proc.pid,
            "profile": plan["profile"] or "default",
            "edge_mode": plan["edge_mode"],
        }
    finally:
        try:
            package.unlink()
        except Exception as exc:
            log.warning("deployer: %s", exc)


def get_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


async def _deploy_known_linux(
    *,
    package: Path,
    host: str,
    user: str,
    password: str,
    plan: dict[str, Any],
    timeout: int,
) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: _deploy_known_linux_sync(package=package, host=host, user=user, password=password, plan=plan, timeout=timeout),
    )


async def _deploy_known_windows(
    *,
    package: Path,
    host: str,
    user: str,
    password: str,
    plan: dict[str, Any],
    timeout: int,
) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: _deploy_known_windows_sync(package=package, host=host, user=user, password=password, plan=plan, timeout=timeout),
    )


def _deploy_known_linux_sync(
    *,
    package: Path,
    host: str,
    user: str,
    password: str,
    plan: dict[str, Any],
    timeout: int,
) -> dict:
    client = _connect_ssh(host=host, user=user, password=password)
    stamp = f"{os.getpid()}-{int(time.time())}"
    remote_pkg = f"/tmp/anima-spawn-{stamp}.tar.gz"
    remote_extract = f"/tmp/anima-spawn-{stamp}"

    try:
        sftp = client.open_sftp()
        sftp.put(str(package), remote_pkg)
        sftp.close()

        script = textwrap.dedent(
            f"""
            set -e
            mkdir -p {shlex.quote(remote_extract)}
            tar xzf {shlex.quote(remote_pkg)} -C {shlex.quote(remote_extract)}
            cd {shlex.quote(remote_extract)}
            ANIMA_INSTALL_DIR={shlex.quote(plan['install_dir'])} \
            PYTHON_CMD={shlex.quote(plan['python_cmd'])} \
            bash bootstrap.sh
            rm -f {shlex.quote(remote_pkg)}
            rm -rf {shlex.quote(remote_extract)}
            """
        ).strip()

        result = _exec_remote(client, f"bash -lc {shlex.quote(script)}", timeout=timeout)
        if result["returncode"] != 0:
            return {
                "success": False,
                "message": result["stderr"][:500] or result["stdout"][:500],
                **result,
            }
        return {
            "success": True,
            "message": f"Deployed to {user}@{host}",
            **result,
        }
    finally:
        client.close()


def _deploy_known_windows_sync(
    *,
    package: Path,
    host: str,
    user: str,
    password: str,
    plan: dict[str, Any],
    timeout: int,
) -> dict:
    client = _connect_ssh(host=host, user=user, password=password)
    stamp = f"{os.getpid()}-{int(time.time())}"
    base_dir = rf"C:\Users\{user}\AppData\Local\Temp"
    remote_pkg = rf"{base_dir}\anima-spawn-{stamp}.tar.gz"
    remote_extract = rf"{base_dir}\anima-spawn-{stamp}"
    remote_script = rf"{base_dir}\deploy-anima-spawn-{stamp}.ps1"

    try:
        sftp = client.open_sftp()
        sftp.put(str(package), remote_pkg)
        with sftp.file(remote_script, "w") as fh:
            fh.write(_render_windows_deploy_script(
                remote_pkg=remote_pkg,
                remote_extract=remote_extract,
                install_dir=plan["install_dir"],
                python_cmd=plan["python_cmd"],
            ))
        sftp.close()

        result = _exec_remote(
            client,
            f'powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{remote_script}"',
            timeout=timeout,
        )
        if result["returncode"] != 0:
            return {
                "success": False,
                "message": result["stderr"][:500] or result["stdout"][:500],
                **result,
            }
        return {
            "success": True,
            "message": f"Deployed to {user}@{host}",
            **result,
        }
    finally:
        client.close()


def _render_windows_deploy_script(*, remote_pkg: str, remote_extract: str, install_dir: str, python_cmd: str) -> str:
    return textwrap.dedent(
        f"""
        $ErrorActionPreference = "Stop"
        $ProgressPreference = "SilentlyContinue"
        $pkg = {_ps_quote(remote_pkg)}
        $extract = {_ps_quote(remote_extract)}
        $install = {_ps_quote(install_dir)}
        $python = {_ps_quote(python_cmd)}

        if (Test-Path $extract) {{
          Remove-Item -Recurse -Force $extract
        }}
        New-Item -ItemType Directory -Force -Path $extract | Out-Null
        tar -xzf $pkg -C $extract
        Set-Location $extract
        $env:ANIMA_INSTALL_DIR = $install
        $env:PYTHON_CMD = $python
        powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $extract "bootstrap.ps1")
        if (Test-Path $pkg) {{
          Remove-Item -Force $pkg
        }}
        if (Test-Path $extract) {{
          Remove-Item -Recurse -Force $extract
        }}
        """
    ).strip()


def _connect_ssh(*, host: str, user: str, password: str, timeout: int = 20):
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs = {
        "hostname": host,
        "username": user,
        "timeout": timeout,
        "look_for_keys": not bool(password),
        "allow_agent": not bool(password),
    }
    if password:
        connect_kwargs["password"] = password
    client.connect(**connect_kwargs)
    return client


def _decode_remote_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "gbk", "cp1252"):
        try:
            return data.decode(encoding)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def _exec_remote(client, command: str, *, timeout: int) -> dict[str, Any]:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    returncode = stdout.channel.recv_exit_status()
    out = _decode_remote_bytes(stdout.read()).strip()
    err = _decode_remote_bytes(stderr.read()).strip()
    return {
        "returncode": returncode,
        "stdout": out,
        "stderr": err,
    }


def _ps_quote(value: str) -> str:
    return "'" + str(value or "").replace("'", "''") + "'"


async def _run_cmd(cmd: str, timeout: int = 60) -> dict:
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
