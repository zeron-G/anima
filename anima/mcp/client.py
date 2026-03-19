"""MCP client — manages a single MCP server connection over stdio.

Handles lifecycle: spawn → initialize → discover tools → call tools → shutdown.
Uses the official `mcp` Python SDK (pip install mcp).
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from anima.utils.logging import get_logger

log = get_logger("mcp.client")


class ServerState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"


@dataclass
class MCPTool:
    """A tool discovered from an MCP server."""
    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str  # which server this tool belongs to
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server."""
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    enabled: bool = True


class MCPServerConnection:
    """Manages a single MCP server process and its session.

    Lifecycle:
    1. start() → spawn subprocess, handshake, discover tools
    2. call_tool(name, args) → execute tool call via session
    3. stop() → graceful shutdown (close stdin, SIGTERM, SIGKILL)

    Automatically restarts on crash (up to max_retries).
    """

    def __init__(self, config: MCPServerConfig, max_retries: int = 3) -> None:
        self._config = config
        self._max_retries = max_retries
        self._retry_count = 0
        self._state = ServerState.STOPPED
        self._exit_stack: AsyncExitStack | None = None
        self._session = None  # mcp.ClientSession
        self._tools: list[MCPTool] = []
        self._server_info: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def state(self) -> ServerState:
        return self._state

    @property
    def tools(self) -> list[MCPTool]:
        return self._tools

    async def start(self) -> bool:
        """Start the MCP server and perform handshake.

        Returns True if successfully connected and tools discovered.
        """
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError:
            log.error("MCP SDK not installed. Run: pip install mcp")
            self._state = ServerState.ERROR
            return False

        if not self._config.enabled:
            log.info("MCP server '%s' is disabled", self.name)
            return False

        self._state = ServerState.STARTING
        log.info("Starting MCP server '%s': %s %s",
                 self.name, self._config.command, " ".join(self._config.args))

        try:
            self._exit_stack = AsyncExitStack()

            server_params = StdioServerParameters(
                command=self._config.command,
                args=self._config.args,
                env=self._config.env,
                cwd=self._config.cwd,
            )

            # Spawn subprocess and create transport
            stdio_transport = await self._exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            read_stream, write_stream = stdio_transport

            # Create session and perform handshake
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )

            init_result = await self._session.initialize()
            self._server_info = {
                "name": getattr(init_result.serverInfo, "name", self.name),
                "version": getattr(init_result.serverInfo, "version", "?"),
                "protocol": getattr(init_result, "protocolVersion", "?"),
            }

            # Discover tools
            await self._discover_tools()

            self._state = ServerState.RUNNING
            self._retry_count = 0
            log.info("MCP server '%s' connected: %s v%s, %d tools",
                     self.name, self._server_info["name"],
                     self._server_info["version"], len(self._tools))
            return True

        except Exception as e:
            log.error("MCP server '%s' failed to start: %s", self.name, e)
            self._state = ServerState.ERROR
            await self._cleanup()
            return False

    async def _discover_tools(self) -> None:
        """Discover all tools from the server via tools/list."""
        if not self._session:
            return

        self._tools = []
        try:
            response = await self._session.list_tools()
            for tool in response.tools:
                mcp_tool = MCPTool(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=tool.inputSchema if hasattr(tool, "inputSchema") else {},
                    server_name=self.name,
                    annotations=dict(tool.annotations) if hasattr(tool, "annotations") and tool.annotations else {},
                )
                self._tools.append(mcp_tool)
        except Exception as e:
            log.warning("Failed to list tools from '%s': %s", self.name, e)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict:
        """Call a tool on this MCP server.

        Returns: {"success": bool, "result": str, "error": str?, "is_error": bool}
        """
        if self._state != ServerState.RUNNING or not self._session:
            return {"success": False, "error": f"MCP server '{self.name}' is not running"}

        try:
            result = await self._session.call_tool(tool_name, arguments or {})

            # Extract text content from result
            texts = []
            for content in result.content:
                if hasattr(content, "text"):
                    texts.append(content.text)
                elif hasattr(content, "data") and hasattr(content, "mimeType"):
                    texts.append(f"[Binary: {content.mimeType}, {len(content.data)} bytes]")
                elif hasattr(content, "resource"):
                    res = content.resource
                    if hasattr(res, "text"):
                        texts.append(res.text)
                    elif hasattr(res, "blob"):
                        texts.append(f"[Resource blob: {len(res.blob)} bytes]")

            result_text = "\n".join(texts) if texts else "(no output)"
            is_error = getattr(result, "isError", False)

            return {
                "success": not is_error,
                "result": result_text,
                "is_error": is_error,
            }

        except Exception as e:
            log.error("MCP tool call '%s.%s' failed: %s", self.name, tool_name, e)
            # Check if server crashed
            if "closed" in str(e).lower() or "broken" in str(e).lower():
                self._state = ServerState.ERROR
                if self._retry_count < self._max_retries:
                    log.info("Attempting to restart MCP server '%s'...", self.name)
                    self._retry_count += 1
                    await self._cleanup()
                    # M-46: Exponential backoff before restart
                    import asyncio
                    backoff = min(2 ** self._retry_count, 30)  # 1, 2, 4, 8, 16, 30 seconds
                    await asyncio.sleep(backoff)
                    await self.start()
            return {"success": False, "error": str(e)}

    async def stop(self) -> None:
        """Gracefully shut down the MCP server."""
        if self._state == ServerState.STOPPED:
            return
        log.info("Stopping MCP server '%s'", self.name)
        await self._cleanup()
        self._state = ServerState.STOPPED

    async def _cleanup(self) -> None:
        """Clean up the exit stack (closes session + kills subprocess)."""
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception as e:
                log.debug("MCP cleanup for '%s': %s", self.name, e)
            self._exit_stack = None
            self._session = None

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "state": self._state.value,
            "server_info": self._server_info,
            "tool_count": len(self._tools),
            "tools": [t.name for t in self._tools],
            "retry_count": self._retry_count,
        }
