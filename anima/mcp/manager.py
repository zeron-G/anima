"""MCP Manager — orchestrates all MCP server connections.

Reads config, spawns servers, aggregates tools into ANIMA's ToolRegistry,
routes tool calls to the correct server.

Usage in main.py:
    mcp_manager = MCPManager(tool_registry)
    await mcp_manager.start_from_config(config)
    # Tools are now registered and callable via ToolExecutor
    ...
    await mcp_manager.stop_all()
"""

from __future__ import annotations

import os
from typing import Any

from anima.config import get
from anima.mcp.client import MCPServerConfig, MCPServerConnection, MCPTool
from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger

log = get_logger("mcp.manager")

# Module-level reference for MCP tool routing
_mcp_manager: MCPManager | None = None


def get_mcp_manager() -> MCPManager | None:
    return _mcp_manager


class MCPManager:
    """Manages all MCP server connections and tool registration.

    Architecture:
    - Reads mcp_servers config from default.yaml / local/env.yaml
    - Spawns each server as subprocess (stdio transport)
    - Discovers tools via MCP tools/list
    - Registers each MCP tool into ANIMA's ToolRegistry as a ToolSpec
      with handler=None and a special _mcp_server attribute
    - ToolExecutor routes handler=None tools through MCPManager.call_tool()
    - Tool names are prefixed with server name: "mcp_{server}_{tool}"
    """

    def __init__(self, tool_registry=None) -> None:
        global _mcp_manager
        self._servers: dict[str, MCPServerConnection] = {}
        self._tool_map: dict[str, tuple[str, str]] = {}  # anima_tool_name → (server_name, mcp_tool_name)
        self._registry = tool_registry
        _mcp_manager = self

    async def start_from_config(self, config: dict | None = None) -> int:
        """Load MCP server configs and start all enabled servers.

        Config format (in default.yaml or local/env.yaml):
        ```yaml
        mcp_servers:
          filesystem:
            command: "npx"
            args: ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
            env:
              NODE_PATH: "/usr/lib/node_modules"
            enabled: true
          github:
            command: "npx"
            args: ["-y", "@modelcontextprotocol/server-github"]
            env:
              GITHUB_TOKEN: "${GITHUB_TOKEN}"
            enabled: true
        ```

        Returns total number of tools discovered across all servers.
        """
        servers_cfg = get("mcp_servers", {})
        if not servers_cfg:
            log.debug("No MCP servers configured")
            return 0

        total_tools = 0
        for name, cfg in servers_cfg.items():
            if not isinstance(cfg, dict):
                continue

            # Resolve environment variables in env values
            env = cfg.get("env")
            if env:
                env = self._resolve_env(env)

            server_config = MCPServerConfig(
                name=name,
                command=cfg.get("command", ""),
                args=cfg.get("args", []),
                env=env,
                cwd=cfg.get("cwd"),
                enabled=cfg.get("enabled", True),
            )

            if not server_config.command:
                log.warning("MCP server '%s' has no command, skipping", name)
                continue

            conn = MCPServerConnection(server_config)
            self._servers[name] = conn

            if await conn.start():
                # Register discovered tools into ANIMA's ToolRegistry
                count = self._register_server_tools(conn)
                total_tools += count

        if total_tools > 0:
            log.info("MCP: %d servers started, %d total tools registered",
                     len([s for s in self._servers.values() if s.state.value == "running"]),
                     total_tools)
        return total_tools

    def _register_server_tools(self, conn: MCPServerConnection) -> int:
        """Register a server's tools into ANIMA's ToolRegistry."""
        count = 0
        for mcp_tool in conn.tools:
            # Prefix tool name to avoid collisions: mcp_{server}_{tool}
            anima_name = f"mcp_{conn.name}_{mcp_tool.name}"

            # Map risk level from MCP annotations
            risk = self._infer_risk(mcp_tool)

            spec = ToolSpec(
                name=anima_name,
                description=f"[MCP:{conn.name}] {mcp_tool.description}",
                parameters=mcp_tool.input_schema,
                risk_level=risk,
                handler=None,  # MCP tools have no local handler
            )
            # Tag as MCP tool for executor routing
            spec._mcp_server = conn.name  # type: ignore[attr-defined]
            spec._mcp_tool_name = mcp_tool.name  # type: ignore[attr-defined]

            if self._registry:
                self._registry.register(spec)

            self._tool_map[anima_name] = (conn.name, mcp_tool.name)
            count += 1

        return count

    async def call_tool(self, anima_tool_name: str, arguments: dict[str, Any]) -> dict:
        """Route a tool call to the correct MCP server.

        Called by ToolExecutor when it encounters a tool with handler=None
        and _mcp_server attribute.
        """
        mapping = self._tool_map.get(anima_tool_name)
        if not mapping:
            return {"success": False, "error": f"Unknown MCP tool: {anima_tool_name}"}

        server_name, mcp_tool_name = mapping
        conn = self._servers.get(server_name)
        if not conn:
            return {"success": False, "error": f"MCP server '{server_name}' not found"}

        return await conn.call_tool(mcp_tool_name, arguments)

    async def stop_all(self) -> None:
        """Stop all MCP servers gracefully."""
        for name, conn in self._servers.items():
            await conn.stop()
        self._servers.clear()
        self._tool_map.clear()
        log.info("All MCP servers stopped")

    async def restart_server(self, name: str) -> bool:
        """Restart a specific MCP server."""
        conn = self._servers.get(name)
        if not conn:
            return False
        await conn.stop()
        success = await conn.start()
        if success:
            self._register_server_tools(conn)
        return success

    def get_status(self) -> dict:
        """Get status of all MCP servers."""
        return {
            "servers": {name: conn.get_status() for name, conn in self._servers.items()},
            "total_tools": len(self._tool_map),
        }

    def list_mcp_tools(self) -> list[dict]:
        """List all MCP tools with their server origin."""
        result = []
        for anima_name, (server, mcp_name) in self._tool_map.items():
            result.append({
                "anima_name": anima_name,
                "mcp_name": mcp_name,
                "server": server,
            })
        return result

    @staticmethod
    def _infer_risk(tool: MCPTool) -> RiskLevel:
        """Infer ANIMA risk level from MCP tool annotations."""
        ann = tool.annotations
        if ann.get("readOnlyHint", False):
            return RiskLevel.SAFE
        if ann.get("destructiveHint", True):
            return RiskLevel.HIGH
        return RiskLevel.MEDIUM

    @staticmethod
    def _resolve_env(env: dict[str, str]) -> dict[str, str]:
        """Resolve ${VAR} references in env values from os.environ."""
        resolved = {}
        for k, v in env.items():
            if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
                var_name = v[2:-1]
                resolved[k] = os.environ.get(var_name, "")
            else:
                resolved[k] = str(v)
        return resolved
