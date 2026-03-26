"""Autonomous exploration loop for robot dog nodes."""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Awaitable, Callable

from anima.robotics.models import RobotExplorationState, RobotPerception
from anima.utils.logging import get_logger

log = get_logger("robotics.exploration")

StatusGetter = Callable[[], Awaitable[dict[str, Any]]]
CommandSender = Callable[[str, dict[str, Any] | None], Awaitable[dict[str, Any]]]


class ExplorationController:
    """Simple reactive exploration strategy with obstacle avoidance."""

    _MOVE_COMMANDS = {"walk_forward", "walk_backward", "turn_left", "turn_right", "trot"}

    def __init__(
        self,
        node_id: str,
        status_getter: StatusGetter,
        command_sender: CommandSender,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._node_id = node_id
        self._status_getter = status_getter
        self._command_sender = command_sender
        self._config = {
            "tick_interval_s": 2.0,
            "move_burst_s": 1.2,
            "turn_burst_s": 0.85,
            "scan_pause_s": 0.35,
            "avoid_distance_cm": 32.0,
            "critical_battery_v": 6.0,
            "low_battery_v": 6.4,
            "walk_speed": 45,
            "turn_speed": 55,
            "scan_sequence": ["look_left", "look_right", "look_forward", "look_up", "look_forward"],
        }
        if config:
            self._config.update(config)
        self._random = random.Random(node_id)
        self._state = RobotExplorationState()
        self._task: asyncio.Task | None = None
        self._scan_index = 0

    def get_state(self) -> dict[str, Any]:
        return self._state.to_dict()

    async def start(self, goal: str = "wander", policy: dict[str, Any] | None = None) -> dict[str, Any]:
        if policy:
            self._config.update(policy)
        if self._task and not self._task.done():
            self._state.goal = goal
            self._state.mode = "running"
            return self.get_state()

        self._state.running = True
        self._state.mode = "running"
        self._state.goal = goal
        self._state.last_error = ""
        self._state.append("exploration", f"start goal={goal}", ts=time.time())
        self._task = asyncio.create_task(self._run_loop(), name=f"robotics_explore_{self._node_id}")
        return self.get_state()

    async def stop(self, reason: str = "manual_stop") -> dict[str, Any]:
        self._state.running = False
        self._state.mode = "idle"
        self._state.append("exploration", f"stop reason={reason}", ts=time.time())
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        try:
            await self._command_sender("stop", {})
        except Exception as exc:
            log.debug("Exploration stop: stop command failed: %s", exc)
        return self.get_state()

    async def close(self) -> None:
        await self.stop(reason="shutdown")

    async def _run_loop(self) -> None:
        while self._state.running:
            try:
                status = await self._status_getter()
                self._state.tick_count += 1
                command, params, duration, reason = self._decide(status)
                self._state.last_decision = reason
                self._state.append("decision", reason, ts=time.time(), extra={"command": command})
                if command:
                    await self._execute_burst(command, params, duration, reason)
                else:
                    await asyncio.sleep(float(self._config["tick_interval_s"]))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._state.last_error = str(exc)
                self._state.mode = "error"
                self._state.append("error", str(exc), ts=time.time())
                log.warning("Exploration loop error on %s: %s", self._node_id, exc)
                await asyncio.sleep(1.0)

    def _decide(self, status: dict[str, Any]) -> tuple[str, dict[str, Any], float, str]:
        perception = RobotPerception.from_status(status)
        current_state = str(status.get("state", "UNKNOWN"))
        battery = perception.battery_v

        if current_state in {"SITTING", "LYING", "IDLE"}:
            return "stand", {"speed": 40}, 0.4, f"state={current_state}, restoring standing posture"

        if current_state == "EMERGENCY":
            return "resume", {}, 0.4, "robot is in emergency state, attempting resume"

        if battery and battery < float(self._config["critical_battery_v"]):
            self._state.mode = "blocked_low_battery"
            return "sleep_mode", {}, 0.4, f"critical battery {battery:.2f}V, entering sleep mode"

        if battery and battery < float(self._config["low_battery_v"]):
            return "sit", {"speed": 30}, 0.4, f"low battery {battery:.2f}V, pausing exploration"

        if perception.is_lifted or abs(perception.pitch_deg) > 60 or abs(perception.roll_deg) > 60:
            return "sit", {"speed": 30}, 0.35, "lift or tilt detected, sitting to stabilize"

        if perception.touch == "L":
            return (
                "turn_right",
                {"speed": int(self._config["turn_speed"])},
                float(self._config["turn_burst_s"]),
                "left touch detected, veering right",
            )
        if perception.touch == "R":
            return (
                "turn_left",
                {"speed": int(self._config["turn_speed"])},
                float(self._config["turn_burst_s"]),
                "right touch detected, veering left",
            )

        if perception.is_obstacle_near or perception.distance_cm <= float(self._config["avoid_distance_cm"]):
            if self._state.tick_count % 3 == 0:
                return "walk_backward", {"speed": 35}, 0.65, f"obstacle at {perception.distance_cm:.1f}cm, backing off"
            turn_cmd = "turn_left" if self._random.random() < 0.5 else "turn_right"
            return (
                turn_cmd,
                {"speed": int(self._config["turn_speed"])},
                float(self._config["turn_burst_s"]),
                f"obstacle at {perception.distance_cm:.1f}cm, rotating for a clearer path",
            )

        if self._state.tick_count % 5 == 0:
            sequence = list(self._config["scan_sequence"])
            command = sequence[self._scan_index % len(sequence)]
            self._scan_index += 1
            return command, {}, float(self._config["scan_pause_s"]), f"scan sweep step={self._scan_index}"

        return (
            "walk_forward",
            {"speed": int(self._config["walk_speed"])},
            float(self._config["move_burst_s"]),
            f"moving into open space ({perception.distance_cm:.1f}cm clear)",
        )

    async def _execute_burst(
        self,
        command: str,
        params: dict[str, Any],
        duration: float,
        reason: str,
    ) -> None:
        ts = time.time()
        result = await self._command_sender(command, params)
        self._state.last_command = command
        self._state.last_command_ts = ts
        self._state.mode = "running"
        self._state.append(
            "command",
            reason,
            ts=ts,
            extra={"command": command, "params": dict(params), "result": result},
        )

        if duration > 0:
            await asyncio.sleep(duration)

        if command in self._MOVE_COMMANDS and self._state.running:
            stop_result = await self._command_sender("stop", {})
            self._state.append(
                "command",
                "burst stop",
                ts=time.time(),
                extra={"command": "stop", "result": stop_result},
            )
