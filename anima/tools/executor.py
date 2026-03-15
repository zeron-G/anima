"""Tool executor — runs tools with safety checks."""

from __future__ import annotations

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
            return {"success": False, "error": f"Tool {tool_name} has no handler"}

        try:
            result = await spec.handler(**args)
            # For shell commands, check returncode
            if isinstance(result, dict) and "returncode" in result and result["returncode"] != 0:
                log.debug("Tool %s exited with code %d", tool_name, result["returncode"])
                return {"success": False, "result": result, "error": result.get("error", f"exit code {result['returncode']}")}
            log.debug("Tool %s executed successfully", tool_name)
            return {"success": True, "result": result}
        except Exception as e:
            log.error("Tool %s failed: %s", tool_name, e)
            return {"success": False, "error": str(e)}
