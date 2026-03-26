"""Shared robotics models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


PIDOG_COMMANDS: list[str] = [
    "stand",
    "sit",
    "lie",
    "walk_forward",
    "walk_backward",
    "turn_left",
    "turn_right",
    "trot",
    "stop",
    "look_left",
    "look_right",
    "look_forward",
    "look_up",
    "look_down",
    "center_head",
    "head",
    "wag_tail",
    "tail",
    "shake_head",
    "stretch",
    "push_up",
    "bark",
    "howl",
    "be_happy",
    "be_curious",
    "be_alert",
    "be_tired",
    "rgb",
    "lights_off",
    "sleep_mode",
    "wake_mode",
    "emergency_stop",
    "resume",
    "balance_on",
    "balance_off",
    "status",
]


def _normalize_base_urls(values: list[str]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip().rstrip("/")
        if not value or value in seen:
            continue
        seen.add(value)
        urls.append(value)
    return urls


@dataclass(slots=True)
class RobotNodeConfig:
    """Static config for one robotics node."""

    node_id: str
    name: str
    kind: str = "pidog"
    role: str = "robot_dog"
    enabled: bool = True
    base_urls: list[str] = field(default_factory=list)
    status_timeout_s: float = 4.0
    command_timeout_s: float = 6.0
    poll_interval_s: float = 2.0
    exploration: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        default_poll_interval_s: float = 2.0,
    ) -> "RobotNodeConfig":
        base_urls = data.get("base_urls") or []
        if not base_urls:
            legacy = [data.get("base_url"), data.get("lan_url"), data.get("tailscale_url")]
            base_urls = [value for value in legacy if value]
        return cls(
            node_id=str(data.get("id") or data.get("node_id") or "").strip(),
            name=str(data.get("name") or data.get("id") or data.get("node_id") or "").strip(),
            kind=str(data.get("kind") or "pidog"),
            role=str(data.get("role") or "robot_dog"),
            enabled=bool(data.get("enabled", True)),
            base_urls=_normalize_base_urls([str(value) for value in base_urls]),
            status_timeout_s=float(data.get("status_timeout_s", 4.0)),
            command_timeout_s=float(data.get("command_timeout_s", 6.0)),
            poll_interval_s=float(data.get("poll_interval_s", default_poll_interval_s)),
            exploration=dict(data.get("exploration") or {}),
            tags=[str(value) for value in data.get("tags", [])],
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class RobotPerception:
    """Normalized perception frame derived from PiDog status."""

    distance_cm: float = 999.0
    touch: str = "N"
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    battery_v: float = 0.0
    is_lifted: bool = False
    is_obstacle_near: bool = False
    is_obstacle_warn: bool = False
    timestamp: float = 0.0

    @classmethod
    def from_status(cls, status: dict[str, Any]) -> "RobotPerception":
        return cls(
            distance_cm=float(status.get("distance", 999.0) or 999.0),
            touch=str(status.get("touch", "N") or "N"),
            pitch_deg=float(status.get("pitch", 0.0) or 0.0),
            roll_deg=float(status.get("roll", 0.0) or 0.0),
            battery_v=float(status.get("battery", 0.0) or 0.0),
            is_lifted=bool(status.get("is_lifted", False)),
            is_obstacle_near=bool(status.get("is_obstacle_near", False)),
            is_obstacle_warn=bool(status.get("is_obstacle_warn", False)),
            timestamp=float(status.get("timestamp", 0.0) or 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "distance_cm": self.distance_cm,
            "touch": self.touch,
            "pitch_deg": self.pitch_deg,
            "roll_deg": self.roll_deg,
            "battery_v": self.battery_v,
            "is_lifted": self.is_lifted,
            "is_obstacle_near": self.is_obstacle_near,
            "is_obstacle_warn": self.is_obstacle_warn,
            "timestamp": self.timestamp,
        }


@dataclass(slots=True)
class RobotExplorationState:
    """Mutable exploration state for one node."""

    running: bool = False
    mode: str = "idle"
    goal: str = "wander"
    tick_count: int = 0
    last_decision: str = ""
    last_command: str = ""
    last_command_ts: float = 0.0
    last_error: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def append(
        self,
        kind: str,
        detail: str,
        *,
        ts: float,
        extra: dict[str, Any] | None = None,
    ) -> None:
        entry = {"kind": kind, "detail": detail, "timestamp": ts}
        if extra:
            entry.update(extra)
        self.history.append(entry)
        if len(self.history) > 30:
            self.history = self.history[-30:]

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "mode": self.mode,
            "goal": self.goal,
            "tick_count": self.tick_count,
            "last_decision": self.last_decision,
            "last_command": self.last_command,
            "last_command_ts": self.last_command_ts,
            "last_error": self.last_error,
            "history": list(self.history),
        }


@dataclass(slots=True)
class RobotNodeSnapshot:
    """Latest known runtime state for one robotics node."""

    node_id: str
    name: str
    kind: str
    role: str
    base_urls: list[str]
    connected: bool = False
    connected_url: str = ""
    last_ok_ts: float = 0.0
    last_refresh_ts: float = 0.0
    last_error: str = ""
    state: str = "UNKNOWN"
    emotion: str = "unknown"
    queue_size: int = 0
    raw_status: dict[str, Any] = field(default_factory=dict)
    perception: RobotPerception = field(default_factory=RobotPerception)
    exploration: RobotExplorationState = field(default_factory=RobotExplorationState)
    capabilities: list[str] = field(default_factory=lambda: list(PIDOG_COMMANDS))
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def update_from_status(
        self,
        status: dict[str, Any],
        *,
        connected_url: str,
        refresh_ts: float,
    ) -> None:
        self.connected = True
        self.connected_url = connected_url
        self.last_ok_ts = refresh_ts
        self.last_refresh_ts = refresh_ts
        self.last_error = ""
        self.raw_status = dict(status)
        self.state = str(status.get("state", self.state))
        self.emotion = str(status.get("emotion", self.emotion))
        self.queue_size = int(status.get("queue_size", self.queue_size) or 0)
        self.perception = RobotPerception.from_status(status)

    def mark_error(self, message: str, *, refresh_ts: float) -> None:
        self.connected = False
        self.last_refresh_ts = refresh_ts
        self.last_error = message

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "kind": self.kind,
            "role": self.role,
            "base_urls": list(self.base_urls),
            "connected": self.connected,
            "connected_url": self.connected_url,
            "last_ok_ts": self.last_ok_ts,
            "last_refresh_ts": self.last_refresh_ts,
            "last_error": self.last_error,
            "state": self.state,
            "emotion": self.emotion,
            "queue_size": self.queue_size,
            "raw_status": dict(self.raw_status),
            "perception": self.perception.to_dict(),
            "exploration": self.exploration.to_dict(),
            "capabilities": list(self.capabilities),
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }
