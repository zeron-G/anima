"""Embodied perception → cognition (E2, DISTRIBUTED_DESIGN §5.5).

The RoboticsManager polls the robot's /status every ~1.5-2s, but that stream only
ever reached the dashboard — Eva could move the dog yet never *perceive* through it.
This source closes that gap the same way the system DiffEngine does: it holds the last
perception frame, detects SIGNIFICANT transitions (a touch, being lifted, an obstacle
appearing, low battery, an emergency), gates them by rising-edge + per-kind cooldown so
the cognitive queue isn't flooded, and turns each into an EMBODIED_PERCEPTION event that
flows through the same 7-stage pipeline as a user message. Steady, unchanging telemetry
produces NO event — only change is worth a thought.
"""

from __future__ import annotations

import time
from typing import Any

from anima.models.event import Event, EventPriority, EventType
from anima.robotics.models import RobotPerception
from anima.utils.logging import get_logger

log = get_logger("robotics.perception")

# Per-kind minimum seconds between emitted events (debounce bouncing sensors + avoid
# flooding cognition while a condition persists — e.g. standing next to a wall).
_COOLDOWN = {
    "touch": 5.0, "lifted": 3.0, "set_down": 3.0, "obstacle_near": 20.0,
    "battery_low": 300.0, "battery_critical": 120.0, "emergency": 10.0,
}
# Only genuine safety conditions preempt a user chat turn (USER_MESSAGE = NORMAL);
# routine sensations sit at NORMAL/LOW so a handled/petted robot can't starve the user
# on the shared priority queue.
_PRIORITY = {
    "touch": EventPriority.NORMAL, "lifted": EventPriority.NORMAL,
    "set_down": EventPriority.LOW, "obstacle_near": EventPriority.NORMAL,
    "battery_low": EventPriority.LOW, "battery_critical": EventPriority.HIGH,
    "emergency": EventPriority.CRITICAL,
}


def _touch_side(touch: str) -> str:
    t = (touch or "").upper()
    if t.startswith("L"):
        return "left"
    if t.startswith("R"):
        return "right"
    return "body"


class EmbodiedPerceptionSource:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self._low_battery_v = float(cfg.get("low_battery_v", 6.4))
        self._critical_battery_v = float(cfg.get("critical_battery_v", 6.0))
        self._last: dict[str, RobotPerception] = {}
        self._last_state: dict[str, str] = {}
        self._last_emit: dict[tuple[str, str], float] = {}

    def observe(self, node_id: str, new: RobotPerception, state: str = "") -> list[Event]:
        """Compare *new* against the last frame for *node_id* and return an
        EMBODIED_PERCEPTION event for EVERY fresh (not-cooled-down) significant change.
        Emitting all of them — not just the top one — means a co-occurring emergency is
        never masked by a touch in the same frame (each transition is rising-edge gated
        and can only re-fire after its cooldown, so this doesn't flood)."""
        prev = self._last.get(node_id)
        prev_state = self._last_state.get(node_id, "")
        self._last[node_id] = new
        self._last_state[node_id] = state

        signals: list[tuple[str, str]] = []   # (kind, summary)
        low_b, crit_b = self._low_battery_v, self._critical_battery_v

        if prev is None:
            # First frame: establish baseline. Only surface conditions that are already
            # dangerous at first sight (booted mid-air / low battery / in emergency) —
            # no startup burst of routine sensations.
            if new.is_lifted:
                signals.append(("lifted", "I'm being held off the ground."))
            if new.battery_v > 0.0 and new.battery_v < crit_b:
                signals.append(("battery_critical", f"I booted with a critically low battery ({new.battery_v:.1f}V)."))
            if state == "EMERGENCY":
                signals.append(("emergency", "I'm in an emergency-stop state."))
        else:
            if new.touch != "N" and prev.touch == "N":
                signals.append(("touch", f"Something just touched my {_touch_side(new.touch)} side."))
            if new.is_lifted and not prev.is_lifted:
                signals.append(("lifted", "I was just picked up off the ground."))
            elif prev.is_lifted and not new.is_lifted:
                signals.append(("set_down", "I was set back down on the ground."))
            if new.is_obstacle_near and not prev.is_obstacle_near:
                signals.append(("obstacle_near", f"An obstacle is right in front of me (~{new.distance_cm:.0f}cm)."))
            if new.battery_v > 0.0:
                was_crit = 0.0 < prev.battery_v < crit_b
                was_low = 0.0 < prev.battery_v < low_b
                if new.battery_v < crit_b and not was_crit:
                    signals.append(("battery_critical", f"My battery is critically low ({new.battery_v:.1f}V)."))
                elif new.battery_v < low_b and not was_low:
                    signals.append(("battery_low", f"My battery is getting low ({new.battery_v:.1f}V)."))
            if state == "EMERGENCY" and prev_state != "EMERGENCY":
                signals.append(("emergency", "A safety reflex just triggered an emergency stop."))

        now = time.time()
        events: list[Event] = []
        for kind, summary in signals:
            if now - self._last_emit.get((node_id, kind), 0.0) < _COOLDOWN.get(kind, 10.0):
                continue
            self._last_emit[(node_id, kind)] = now
            events.append(Event(
                type=EventType.EMBODIED_PERCEPTION,
                priority=_PRIORITY.get(kind, EventPriority.NORMAL),
                source=f"robot:{node_id}",
                payload={
                    "node_id": node_id,
                    "kind": kind,
                    "summary": summary,
                    "perception": new.to_dict(),
                    "state": state,
                },
            ))
        return events
