"""Remote tools — execute commands and write files on other ANIMA nodes."""

from __future__ import annotations

import asyncio
import tempfile
import os

from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger

log = get_logger("tools.remote")

_KNOWN_NODES: dict[str, dict] = {}


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
    return await asyncio.get_event_loop().run_in_executor(None, _exec_sync, node, command, timeout)


async def _remote_write(node: str, path: str, content: str) -> dict:
    return await asyncio.get_event_loop().run_in_executor(None, _write_sync, node, path, content)


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
    ]
