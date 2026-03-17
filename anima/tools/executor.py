"""Tool executor — runs tools with safety checks."""

from __future__ import annotations

import inspect

from anima.tools.registry import ToolRegistry
from anima.tools.safety import assess_command_risk
from anima.models.tool_spec import RiskLevel
from anima.utils.logging import get_logger

log = get_logger("tool_executor")


class ToolExecutor:
    """Executes tools with risk assessment and safety guards.

    Safety model:
    - Shell commands: risk is assessed DYNAMICALLY per command (not static).
      `ls`, `cat`, `pwd` → SAFE. `rm file` → MEDIUM. `rm -rf /` → BLOCKED.
    - Other tools: risk is the static level from ToolSpec.
    - BLOCKED commands are always rejected regardless of max_risk.
    """

    def __init__(self, registry: ToolRegistry, max_risk: int = 3) -> None:
        self._registry = registry
        self._max_risk = RiskLevel(max_risk)

    async def execute(self, tool_name: str, args: dict) -> dict:
        """Execute a tool by name with given arguments.

        Returns: {"success": bool, "result": ..., "error": ...}
        """
        spec = self._registry.get(tool_name)
        if spec is None:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

        # Shell commands: dynamic risk assessment per command
        if tool_name == "shell" and "command" in args:
            risk = assess_command_risk(args["command"])
            if risk == RiskLevel.BLOCKED:
                return {"success": False, "error": "Command blocked: too dangerous"}
            if risk > self._max_risk:
                return {
                    "success": False,
                    "error": f"Command risk {risk.name} exceeds max allowed {self._max_risk.name}",
                }
            # Dynamic risk passed — skip static check for shell
        else:
            # Non-shell tools: use static risk level
            if spec.risk_level > self._max_risk:
                return {
                    "success": False,
                    "error": f"Tool risk {spec.risk_level.name} exceeds limit",
                }

        if spec.handler is None:
            # MCP tools have handler=None — route through MCPManager
            if hasattr(spec, "_mcp_server"):
                from anima.mcp.manager import get_mcp_manager
                mgr = get_mcp_manager()
                if mgr:
                    return await mgr.call_tool(tool_name, args)
                return {"success": False, "error": f"MCP manager not initialized for tool {tool_name}"}
            return {"success": False, "error": f"Tool {tool_name} has no handler"}

        try:
            # Filter args and check required params
            filtered_args = args
            if spec.handler is not None:
                try:
                    sig = inspect.signature(spec.handler)
                    valid_params = set(sig.parameters.keys())
                    has_var_keyword = any(
                        p.kind == inspect.Parameter.VAR_KEYWORD
                        for p in sig.parameters.values()
                    )
                    if not has_var_keyword:
                        unknown = set(args.keys()) - valid_params
                        if unknown:
                            log.warning("Tool %s: dropping unknown args %s", tool_name, unknown)
                            filtered_args = {k: v for k, v in args.items() if k in valid_params}

                    # Check required params are present
                    required = [
                        p.name for p in sig.parameters.values()
                        if p.default is inspect.Parameter.empty
                        and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
                    ]
                    missing = [r for r in required if r not in filtered_args]
                    if missing:
                        return {"success": False, "error": f"Missing required arguments: {missing}"}
                except (ValueError, TypeError):
                    pass

            result = await spec.handler(**filtered_args)
            # For shell commands, check returncode
            if isinstance(result, dict) and "returncode" in result and result["returncode"] != 0:
                log.debug("Tool %s exited with code %d", tool_name, result["returncode"])
                return {"success": False, "result": result, "error": result.get("error", f"exit code {result['returncode']}")}
            log.debug("Tool %s executed successfully", tool_name)
            return {"success": True, "result": result}
        except Exception as e:
            log.error("Tool %s failed: %s", tool_name, e)
            return {"success": False, "error": str(e)}
