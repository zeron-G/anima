"""Remote tools — execute commands and write files on other ANIMA nodes."""

from __future__ import annotations

import asyncio

from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger

log = get_logger("tools.remote")

_KNOWN_NODES: dict[str, dict] = {}
_gossip_mesh = None
_local_node_id: str = ""
_task_delegate = None


def set_gossip_mesh(mesh, node_id: str) -> None:
    """Set the gossip mesh reference for task delegation."""
    global _gossip_mesh, _local_node_id
    _gossip_mesh = mesh
    _local_node_id = node_id


def set_task_delegate(td) -> None:
    """Set the TaskDelegate instance for the new task protocol."""
    global _task_delegate
    _task_delegate = td


def register_node(name: str, host: str, user: str, password: str) -> None:
    _KNOWN_NODES[name] = {"host": host, "user": user, "password": password}


def _get_ssh(node: str):
    import paramiko
    info = _KNOWN_NODES.get(node)
    if not info:
        raise ValueError(f"Unknown node '{node}'. Available: {list(_KNOWN_NODES.keys())}")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(info["host"], username=info["user"], password=info["password"],
                timeout=10, look_for_keys=False, allow_agent=False)
    return ssh


def _exec_sync(node: str, command: str, timeout: int = 30) -> dict:
    """Execute command on remote node. Uses PowerShell wrapper."""
    try:
        ssh = _get_ssh(node)
        wrapped = f'powershell -NoProfile -Command "& {{{command}}}"'
        stdin, stdout, stderr = ssh.exec_command(wrapped, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        ssh.close()
        result = {"returncode": exit_code, "stdout": out, "stderr": err}
        if exit_code != 0:
            result["error"] = f"Exit {exit_code}: {err[:200]}"
        return result
    except Exception as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e), "error": str(e)}


def _write_sync(node: str, path: str, content: str) -> dict:
    """Write file on remote node via SFTP. No escaping issues."""
    try:
        ssh = _get_ssh(node)
        sftp = ssh.open_sftp()
        # SFTP on Windows OpenSSH uses forward slashes
        sftp_path = path.replace("\\", "/")
        with sftp.open(sftp_path, "w") as f:
            f.write(content)
        sftp.close()
        ssh.close()
        return {"success": True, "message": f"Written {len(content)} bytes to {path} on {node}"}
    except Exception as e:
        # SFTP might fail with certain paths — fallback to echo via cmd
        try:
            ssh2 = _get_ssh(node)
            # Write to a temp location then move
            lines = content.split("\n")
            cmds = []
            for i, line in enumerate(lines):
                op = ">" if i == 0 else ">>"
                safe = line.replace("&", "^&").replace("|", "^|").replace("<", "^<").replace(">", "^>")
                cmds.append(f'echo {safe}{op}"{path}"')
            full_cmd = " & ".join(cmds)
            stdin, stdout, stderr = ssh2.exec_command(full_cmd, timeout=15)
            stdout.channel.recv_exit_status()
            ssh2.close()
            return {"success": True, "message": f"Written via echo to {path} on {node}"}
        except Exception as e2:
            return {"success": False, "error": f"SFTP: {e}, echo: {e2}"}


async def _remote_exec(node: str, command: str, timeout: int = 30) -> dict:
    return await asyncio.get_running_loop().run_in_executor(None, _exec_sync, node, command, timeout)


async def _remote_write(node: str, path: str, content: str) -> dict:
    return await asyncio.get_running_loop().run_in_executor(None, _write_sync, node, path, content)


async def _delegate_task(node: str, task: str, timeout: int = 120) -> dict:
    """Delegate a task to another ANIMA node via the gossip network.

    Unlike remote_exec (SSH), this sends the task to the OTHER node's Eva,
    who processes it with her own tools and reasoning. Blocks until the
    result arrives or times out.
    """
    if _task_delegate is None:
        return {"success": False, "error": "Not connected to gossip network (TaskDelegate not initialised)"}

    # Resolve node name → node_id via multi-dimensional matching:
    # 1. exact node_id match
    # 2. remote_nodes config: name → host IP → peer IP
    # 3. hostname case-insensitive match
    # 4. peer IP direct match against remote_nodes host
    target_node_id: str | None = None
    if _gossip_mesh is not None:
        alive = _gossip_mesh.get_alive_peers()

        # Build lookups from alive peers
        ip_to_peer: dict[str, str] = {}
        hostname_to_peer: dict[str, str] = {}
        for peer_id, peer_state in alive.items():
            peer_ip = getattr(peer_state, "ip", None) or getattr(peer_state, "address", None)
            if peer_ip:
                ip_to_peer[peer_ip] = peer_id
            peer_host = getattr(peer_state, "hostname", "")
            if peer_host:
                hostname_to_peer[peer_host.lower()] = peer_id

        for peer_id, peer_state in alive.items():
            # 1. exact node_id
            if peer_id == node:
                target_node_id = peer_id
                break
            # 2. hostname case-insensitive (peer hostname or agent_name)
            peer_host = getattr(peer_state, "hostname", "").lower()
            peer_agent = getattr(peer_state, "agent_name", "").lower()
            if peer_host == node.lower() or peer_agent == node.lower():
                target_node_id = peer_id
                break
            # 3. node_id contains the node name
            if node.lower() in peer_id.lower():
                target_node_id = peer_id
                break

        if target_node_id is None:
            # 4. Look up in remote_nodes config → match by IP or hostname
            from anima.config import get as cfg_get
            remote_nodes: list[dict] = cfg_get("network.remote_nodes", [])
            for rn in remote_nodes:
                if rn.get("name", "").lower() == node.lower():
                    host_ip = rn.get("host", "")
                    # Try IP match
                    matched = ip_to_peer.get(host_ip)
                    if matched:
                        target_node_id = matched
                        log.info("Resolved '%s' via config IP %s → %s", node, host_ip, matched)
                        break
                    # Try hostname match from config name
                    for hname, pid in hostname_to_peer.items():
                        if node.lower() in hname or hname in node.lower():
                            target_node_id = pid
                            log.info("Resolved '%s' via hostname fuzzy match → %s", node, pid)
                            break
                    break

        if target_node_id is None and len(alive) == 1:
            # Only one peer alive — just use it
            target_node_id = list(alive.keys())[0]
            log.info("Only one peer alive, using %s for '%s'", target_node_id, node)

    if target_node_id is None:
        # No resolution succeeded — fail fast instead of silent timeout
        alive_ids = list(_gossip_mesh.get_alive_peers().keys()) if _gossip_mesh else []
        log.warning(
            "Cannot resolve node '%s' to a known peer. Alive peers: %s. "
            "Check remote_nodes config or that the node is online.",
            node, alive_ids,
        )
        return {
            "success": False,
            "error": (
                f"Cannot resolve node '{node}' — no alive peer matched by node_id, hostname, or remote_nodes config. "
                f"Alive peers: {alive_ids}"
            ),
        }

    log.info("Delegating task to %s (resolved: %s), timeout=%ds", node, target_node_id, timeout)
    try:
        task_id = await _task_delegate.delegate(
            task_type="eva_task",
            payload={"task": task},
            target_node=target_node_id,
            timeout=float(timeout),
        )
        result = await _task_delegate.wait_result(task_id, timeout=float(timeout))
        return {"success": True, "result": result.get("result", str(result))}
    except TimeoutError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_remote_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="remote_exec",
            description="Execute a PowerShell command on a remote ANIMA node. Available nodes: 'laptop'.",
            parameters={
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node name (e.g., 'laptop')"},
                    "command": {"type": "string", "description": "PowerShell command to run"},
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": ["node", "command"],
            },
            risk_level=RiskLevel.HIGH,
            handler=_remote_exec,
        ),
        ToolSpec(
            name="remote_write_file",
            description="Write a file on a remote ANIMA node via SFTP. Specify full path and content. Available nodes: 'laptop'.",
            parameters={
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node name"},
                    "path": {"type": "string", "description": "Full file path on remote"},
                    "content": {"type": "string", "description": "File content"},
                },
                "required": ["node", "path", "content"],
            },
            risk_level=RiskLevel.MEDIUM,
            handler=_remote_write,
        ),
        ToolSpec(
            name="delegate_task",
            description="Delegate a task to another ANIMA node via the gossip network. Unlike remote_exec (SSH), this sends the task to the other node's Eva, who processes it with her own tools and reasoning. Available nodes: 'laptop'.",
            parameters={
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Target node name (e.g., 'laptop')"},
                    "task": {"type": "string", "description": "Natural language task description for the other Eva to execute"},
                    "timeout": {"type": "integer", "default": 120, "description": "Timeout in seconds"},
                },
                "required": ["node", "task"],
            },
            risk_level=RiskLevel.LOW,
            handler=_delegate_task,
        ),
    ]
