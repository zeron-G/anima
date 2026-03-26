"""Tools for controlling PiDog-backed robotics nodes."""

from __future__ import annotations

from typing import Any

from anima.models.tool_spec import RiskLevel, ToolSpec

_robotics_manager = None


def set_robotics_manager(manager) -> None:
    global _robotics_manager
    _robotics_manager = manager


def _require_manager():
    if _robotics_manager is None:
        raise RuntimeError("Robotics manager not initialized")
    return _robotics_manager


async def _robot_dog_status(node_id: str = "") -> dict[str, Any]:
    manager = _require_manager()
    if node_id:
        return await manager.refresh_node(node_id)
    await manager.refresh_all()
    return manager.get_snapshot()


async def _robot_dog_command(
    node_id: str,
    command: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manager = _require_manager()
    return await manager.execute_command(node_id, command, params or {})


async def _robot_dog_nlp(node_id: str, text: str) -> dict[str, Any]:
    manager = _require_manager()
    return await manager.run_nlp(node_id, text)


async def _robot_dog_speak(node_id: str, text: str, blocking: bool = False) -> dict[str, Any]:
    manager = _require_manager()
    return await manager.speak(node_id, text, blocking=blocking)


async def _robot_dog_exploration(
    node_id: str,
    action: str = "status",
    goal: str = "wander",
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manager = _require_manager()
    if action == "start":
        return await manager.start_exploration(node_id, goal=goal, policy=policy or {})
    if action == "stop":
        return await manager.stop_exploration(node_id, reason="tool_request")
    return manager.get_node(node_id)["exploration"]


def get_robotics_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="robot_dog_status",
            description="Get live status for configured robot dog nodes, including sensors and exploration state.",
            parameters={
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "Optional robot node id such as 'pidog-eva'. Leave empty to list all nodes.",
                    },
                },
            },
            risk_level=RiskLevel.SAFE,
            handler=_robot_dog_status,
        ),
        ToolSpec(
            name="robot_dog_command",
            description="Send a structured PiDog command such as stand, walk_forward, turn_left, stop, wag_tail, bark, sleep_mode, or emergency_stop.",
            parameters={
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "Robot node id."},
                    "command": {"type": "string", "description": "PiDog command name."},
                    "params": {
                        "type": "object",
                        "description": "Optional command params such as speed, yaw, pitch, duration, angle, mode, or color.",
                        "additionalProperties": True,
                    },
                },
                "required": ["node_id", "command"],
            },
            risk_level=RiskLevel.HIGH,
            handler=_robot_dog_command,
        ),
        ToolSpec(
            name="robot_dog_nlp",
            description="Send a natural-language robot command to the PiDog NLP endpoint.",
            parameters={
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "Robot node id."},
                    "text": {"type": "string", "description": "Natural-language instruction in Chinese or English."},
                },
                "required": ["node_id", "text"],
            },
            risk_level=RiskLevel.MEDIUM,
            handler=_robot_dog_nlp,
        ),
        ToolSpec(
            name="robot_dog_speak",
            description="Make the robot dog speak a line using its onboard TTS service.",
            parameters={
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "Robot node id."},
                    "text": {"type": "string", "description": "Text to synthesize on the robot."},
                    "blocking": {"type": "boolean", "default": False},
                },
                "required": ["node_id", "text"],
            },
            risk_level=RiskLevel.MEDIUM,
            handler=_robot_dog_speak,
        ),
        ToolSpec(
            name="robot_dog_exploration",
            description="Control or inspect autonomous exploration on a robot dog node.",
            parameters={
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "Robot node id."},
                    "action": {
                        "type": "string",
                        "enum": ["status", "start", "stop"],
                        "default": "status",
                    },
                    "goal": {"type": "string", "default": "wander"},
                    "policy": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "Optional exploration overrides such as walk_speed, turn_speed, or avoid_distance_cm.",
                    },
                },
                "required": ["node_id"],
            },
            risk_level=RiskLevel.HIGH,
            handler=_robot_dog_exploration,
        ),
    ]
