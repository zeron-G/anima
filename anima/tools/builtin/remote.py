"""Remote execution tool — run commands on other ANIMA nodes via SSH."""

from __future__ import annotations

import asyncio

from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger

log = get_logger("tools.remote")

# Known nodes (populated from config/prompts)
_KNOWN_NODES: dict[str, dict] = {}


def register_node(name: str, host: str, user: str, password: str) -> None:
    """Register a known remote node."""
    _KNOWN_NODES[name] = {"host": host, "user": user, "password": password}


def _exec_remote_sync(node: str, command: str, timeout: int = 30) -> dict:
    """Execute command on a remote node via SSH (paramiko)."""
    try:
        import paramiko
    except ImportError:
        return {"success": False, "error": "paramiko not installed"}

    info = _KNOWN_NODES.get(node)
    if not info:
        available = list(_KNOWN_NODES.keys())
        return {"success": False, "error": f"Unknown node '{node}'. Available: {available}"}

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(
            info["host"], username=info["user"], password=info["password"],
            timeout=10, look_for_keys=False, allow_agent=False,
        )
        stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        ssh.close()

        result = {
            "returncode": exit_code,
            "stdout": out,
            "stderr": err,
        }
        if exit_code != 0:
            result["error"] = f"Exit code {exit_code}: {err[:200]}"
        return result
    except Exception as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e), "error": str(e)}


async def _remote_exec(node: str, command: str, timeout: int = 30) -> dict:
    """Execute command on a remote ANIMA node."""
    return await asyncio.get_event_loop().run_in_executor(
        None, _exec_remote_sync, node, command, timeout
    )


def get_remote_tool() -> ToolSpec:
    return ToolSpec(
        name="remote_exec",
        description=(
            "Execute a command on a remote ANIMA node via SSH. "
            "Available nodes: use node name like 'laptop'. "
            "The command runs on the remote machine."
        ),
        parameters={
            "type": "object",
            "properties": {
                "node": {
                    "type": "string",
                    "description": "Node name (e.g., 'laptop')",
                },
                "command": {
                    "type": "string",
                    "description": "Shell command to run on the remote node",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 30)",
                    "default": 30,
                },
            },
            "required": ["node", "command"],
        },
        risk_level=RiskLevel.HIGH,
        handler=_remote_exec,
    )
