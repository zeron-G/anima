"""Agent management tools — let the LLM spawn and track sub-agents.

Provides four tools:
  - spawn_agent:  delegate work to a claude_code or shell sub-agent
  - check_agent:  poll a running agent for status/result
  - wait_agent:   block until an agent completes
  - list_agents:  show all sessions with hierarchy
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anima.models.tool_spec import ToolSpec, RiskLevel

if TYPE_CHECKING:
    from anima.core.agents import AgentManager

_agent_manager: AgentManager | None = None


def set_agent_manager(manager: AgentManager) -> None:
    global _agent_manager
    _agent_manager = manager


# ── Handlers ──

async def _spawn_agent(
    type: str, prompt: str,
    working_directory: str = "", timeout: int = 120,
) -> dict:
    """Spawn a sub-agent of the given type."""
    if _agent_manager is None:
        raise RuntimeError("AgentManager not initialized")
    if type not in ("internal", "claude_code", "shell"):
        raise ValueError(f"Unknown type '{type}'. Use 'internal', 'claude_code', or 'shell'.")
    if _agent_manager.get_active_count() >= _agent_manager._max_concurrent:
        raise RuntimeError(f"Max concurrent agents ({_agent_manager._max_concurrent}) reached.")

    if type == "internal":
        session = await _agent_manager.spawn_internal(prompt=prompt, timeout=timeout)
    elif type == "claude_code":
        session = await _agent_manager.spawn_claude_code(
            prompt=prompt, working_dir=working_directory, timeout=timeout,
        )
    else:
        session = await _agent_manager.spawn_shell_task(command=prompt, timeout=timeout)
    return {"session_id": session.id, "type": session.type, "status": session.status}


async def _check_agent(session_id: str) -> dict:
    """Check the status of a sub-agent."""
    if _agent_manager is None:
        raise RuntimeError("AgentManager not initialized")
    session = _agent_manager.get_session(session_id)
    if session is None:
        raise ValueError(f"No agent found with id '{session_id}'")
    return session.to_dict()


async def _wait_agent(session_id: str, timeout: int = 300) -> dict:
    """Wait for a sub-agent to complete and return its result."""
    if _agent_manager is None:
        raise RuntimeError("AgentManager not initialized")
    session = _agent_manager.get_session(session_id)
    if session is None:
        raise ValueError(f"No agent found with id '{session_id}'")
    session = await _agent_manager.wait_for(session_id, timeout=float(timeout))
    return session.to_dict()


async def _list_agents() -> dict:
    """List all agent sessions with hierarchy."""
    if _agent_manager is None:
        raise RuntimeError("AgentManager not initialized")
    return _agent_manager.get_hierarchy()


# ── Tool specs ──

def get_agent_tools() -> list[ToolSpec]:
    """Return all agent management tool specs."""
    return [
        ToolSpec(
            name="spawn_agent",
            description=(
                "Spawn a sub-agent to perform work. "
                "type='internal': LLM agent with tools (best for research, analysis, multi-step tasks). "
                "type='claude_code': Claude Code CLI (best for complex code tasks). "
                "type='shell': simple command."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["internal", "claude_code", "shell"],
                        "description": "Agent type: 'internal' for LLM sub-agent with tools, 'claude_code' for Claude CLI, 'shell' for command",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The task prompt (for claude_code) or command (for shell)",
                    },
                    "working_directory": {
                        "type": "string",
                        "description": "Working directory for the agent (optional)",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 120)",
                    },
                },
                "required": ["type", "prompt"],
            },
            risk_level=RiskLevel.HIGH,
            handler=_spawn_agent,
        ),
        ToolSpec(
            name="check_agent",
            description="Check the status of a previously spawned sub-agent.",
            parameters={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "The agent session ID returned by spawn_agent",
                    },
                },
                "required": ["session_id"],
            },
            risk_level=RiskLevel.SAFE,
            handler=_check_agent,
        ),
        ToolSpec(
            name="wait_agent",
            description="Wait for a sub-agent to complete and return its result.",
            parameters={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "The agent session ID to wait for",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds to wait (default 300)",
                    },
                },
                "required": ["session_id"],
            },
            risk_level=RiskLevel.SAFE,
            handler=_wait_agent,
        ),
        ToolSpec(
            name="list_agents",
            description="List all sub-agent sessions with their status and hierarchy.",
            parameters={
                "type": "object",
                "properties": {},
            },
            risk_level=RiskLevel.SAFE,
            handler=_list_agents,
        ),
    ]
