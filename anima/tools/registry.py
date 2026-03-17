"""Tool registry — registers and looks up available tools.

Supports hot-reload: after evolution modifies tool code,
call reload_tools() to re-import modules and re-register
without restarting the process.
"""

from __future__ import annotations

import importlib
import sys

from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger

log = get_logger("tool_registry")

# All builtin tool modules — reload these on hot-reload
_BUILTIN_MODULES = [
    "anima.tools.builtin.shell",
    "anima.tools.builtin.file_ops",
    "anima.tools.builtin.system_info",
    "anima.tools.builtin.note",
    "anima.tools.builtin.datetime_tool",
    "anima.tools.builtin.web_fetch",
    "anima.tools.builtin.claude_code",
    "anima.tools.builtin.agent_tools",
    "anima.tools.builtin.search",
    "anima.tools.builtin.edit",
    "anima.tools.builtin.scheduler_tools",
    "anima.tools.builtin.remote",
    "anima.tools.builtin.github_tool",
    "anima.tools.builtin.email_tool",
    "anima.tools.builtin.google_tool",
    "anima.tools.builtin.env_tools",
    "anima.tools.builtin.memory_tools",
    "anima.tools.builtin.evolution_tools",
]


class ToolRegistry:
    """Central registry for all available tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        """Register a tool (overwrites if exists)."""
        self._tools[spec.name] = spec
        log.debug("Registered tool: %s (risk=%s)", spec.name, spec.risk_level.name)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def list_tools(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def to_llm_schemas(self) -> list[dict]:
        """Get all tool schemas for LLM function calling."""
        return [t.to_llm_schema() for t in self._tools.values()]

    def register_builtins(self) -> None:
        """Register all built-in tools."""
        self._load_and_register()

    def reload_tools(self) -> int:
        """Hot-reload all tool modules and re-register.

        Called after evolution modifies tool code. Uses importlib.reload()
        so new code takes effect without process restart.

        Returns number of tools registered.
        """
        # Reload all builtin modules
        reloaded = []
        for mod_name in _BUILTIN_MODULES:
            if mod_name in sys.modules:
                try:
                    importlib.reload(sys.modules[mod_name])
                    reloaded.append(mod_name.split(".")[-1])
                except Exception as e:
                    log.error("Failed to reload %s: %s", mod_name, e)

        # Re-register all tools (overwrites old references)
        old_count = len(self._tools)
        self._load_and_register()
        new_count = len(self._tools)

        if reloaded:
            log.info("Hot-reloaded %d modules, %d tools registered", len(reloaded), new_count)
        return new_count

    def _load_and_register(self) -> None:
        """Import tool modules and register all tools."""
        from anima.tools.builtin.shell import get_shell_tool
        from anima.tools.builtin.file_ops import get_file_tools
        from anima.tools.builtin.system_info import get_system_info_tool
        from anima.tools.builtin.note import get_note_tool
        from anima.tools.builtin.datetime_tool import get_datetime_tool
        from anima.tools.builtin.web_fetch import get_web_fetch_tool
        from anima.tools.builtin.claude_code import get_claude_code_tools
        from anima.tools.builtin.agent_tools import get_agent_tools
        from anima.tools.builtin.search import get_search_tools
        from anima.tools.builtin.edit import get_edit_tool
        from anima.tools.builtin.scheduler_tools import get_scheduler_tools
        from anima.tools.builtin.remote import get_remote_tools
        from anima.tools.builtin.github_tool import get_github_tool
        from anima.tools.builtin.email_tool import get_email_tools
        from anima.tools.builtin.google_tool import get_google_tool
        from anima.tools.builtin.env_tools import get_env_tools

        self.register(get_shell_tool())
        for tool in get_file_tools():
            self.register(tool)
        self.register(get_system_info_tool())
        self.register(get_note_tool())
        self.register(get_datetime_tool())
        self.register(get_web_fetch_tool())
        for tool in get_claude_code_tools():
            self.register(tool)
        for tool in get_agent_tools():
            self.register(tool)
        for tool in get_search_tools():
            self.register(tool)
        self.register(get_edit_tool())
        for tool in get_scheduler_tools():
            self.register(tool)
        for tool in get_remote_tools():
            self.register(tool)
        self.register(get_github_tool())
        for tool in get_email_tools():
            self.register(tool)
        self.register(get_google_tool())
        for tool in get_env_tools():
            self.register(tool)
