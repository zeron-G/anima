"""Manager for robotics nodes exposed to ANIMA and the desktop UI."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

from anima.robotics.exploration import ExplorationController
from anima.robotics.models import PIDOG_COMMANDS, RobotNodeConfig, RobotNodeSnapshot
from anima.robotics.nlp_supervisor import RobotNlpSupervisor, match_pidog_command_text
from anima.robotics.perception_source import EmbodiedPerceptionSource
from anima.robotics.pidog import PiDogApiClient
from anima.utils.logging import get_logger

log = get_logger("robotics.manager")

# Commands that translate the body across the ground — the only class gated by the
# ANIMA-side safety clamp. Everything else (postures, head, gestures, audio, emotes,
# lights, power, and the safety verbs stop/sit/lie/sleep_mode/emergency_stop/resume)
# is always allowed so a safe response can never be blocked.
_LOCOMOTION = {"walk_forward", "walk_backward", "turn_left", "turn_right", "trot"}
_FORWARD = {"walk_forward", "trot"}


class RoboticsManager:
    """Tracks configured robot nodes, live status, and exploration state."""

    def __init__(self, config: dict[str, Any] | None = None, session: aiohttp.ClientSession | None = None) -> None:
        cfg = dict(config or {})
        self._enabled = bool(cfg.get("enabled", False))
        self._poll_interval_s = float(cfg.get("poll_interval_s", 2.0))
        self._exploration_defaults = dict(cfg.get("exploration") or {})
        self._safety = dict(cfg.get("safety") or {})
        # Embodied perception → cognition (E2). Emits EMBODIED_PERCEPTION events into
        # the cognitive event queue on significant sensor changes. event_sink is wired
        # by main once the event queue exists; until then perception stays dashboard-only.
        self._perception = EmbodiedPerceptionSource(cfg.get("perception") or {})
        self._event_sink = None
        # E3: optional async hook (kind, perception) → embodied emotion coupling +
        # expression. Fire-and-forget off the poll loop, in addition to the cognitive
        # event sink. Set by main once the emotion state exists.
        self._perception_hook = None
        self._session = session
        self._owns_session = session is None
        self._poll_task: asyncio.Task | None = None
        self._nlp_supervisor = RobotNlpSupervisor(cfg.get("nlp_supervisor") or {})
        self._nodes: dict[str, RobotNodeConfig] = {}
        self._clients: dict[str, PiDogApiClient] = {}
        self._snapshots: dict[str, RobotNodeSnapshot] = {}
        self._explorers: dict[str, ExplorationController] = {}

        raw_nodes = cfg.get("nodes", []) or []
        for raw in raw_nodes:
            node = RobotNodeConfig.from_dict(raw, default_poll_interval_s=self._poll_interval_s)
            if not node.node_id or not node.enabled:
                continue
            self._nodes[node.node_id] = node

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> "RoboticsManager":
        return cls(config=config)

    def set_event_sink(self, sink) -> None:
        """Wire the cognitive event queue so significant robot perception reaches
        cognition. *sink* takes an Event; called best-effort from the poll loop."""
        self._event_sink = sink

    def set_perception_hook(self, hook) -> None:
        """Wire a fast async reflex on significant perception (E3 embodied emotion +
        expression). *hook* is an async callable (kind, perception_dict); it runs
        fire-and-forget so it never blocks the poll loop."""
        self._perception_hook = hook

    @property
    def enabled(self) -> bool:
        return self._enabled and bool(self._nodes)

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._owns_session = True

        assert self._session is not None
        for node_id, node in self._nodes.items():
            self._clients[node_id] = PiDogApiClient(node, self._session)
            self._snapshots[node_id] = RobotNodeSnapshot(
                node_id=node.node_id,
                name=node.name,
                kind=node.kind,
                role=node.role,
                base_urls=list(node.base_urls),
                capabilities=list(PIDOG_COMMANDS),
                tags=list(node.tags),
                metadata=dict(node.metadata),
            )
            exploration_cfg = dict(self._exploration_defaults)
            exploration_cfg.update(node.exploration)
            self._explorers[node_id] = ExplorationController(
                node_id=node_id,
                status_getter=lambda node_id=node_id: self.read_status(node_id),
                command_sender=lambda command, params=None, node_id=node_id: self.execute_command(node_id, command, params or {}),
                config=exploration_cfg,
            )

        await self.refresh_all()
        # Boot liveness gate: make an unreachable robot VISIBLE (status errors are
        # otherwise swallowed by read_status → a dead robot used to boot silently).
        online = [nid for nid, s in self._snapshots.items() if s.connected]
        down = [nid for nid, s in self._snapshots.items() if not s.connected]
        if online:
            log.info("Robotics: %d node(s) online: %s", len(online), ", ".join(online))
        if down:
            log.warning(
                "Robotics: %d node(s) UNREACHABLE at boot: %s — running degraded, will keep polling",
                len(down),
                ", ".join(f"{nid} ({self._snapshots[nid].last_error[:60]})" for nid in down))
        self._poll_task = asyncio.create_task(self._poll_loop(), name="robotics_poll")
        log.info("Robotics manager started with %d node(s)", len(self._nodes))

    async def stop(self) -> None:
        for explorer in self._explorers.values():
            await explorer.close()

        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        if self._session and self._owns_session:
            await self._session.close()
        self._session = None

    async def refresh_all(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for node_id in self._nodes:
            results.append(await self.refresh_node(node_id))
        return results

    async def refresh_node(self, node_id: str) -> dict[str, Any]:
        await self.read_status(node_id)
        snapshot = self._require_snapshot(node_id)
        snapshot.exploration = self._explorers[node_id]._state
        return snapshot.to_dict()

    async def read_status(self, node_id: str) -> dict[str, Any]:
        snapshot = self._require_snapshot(node_id)
        client = self._require_client(node_id)
        now = time.time()
        try:
            status = await client.status()
            snapshot.update_from_status(status, connected_url=client.active_base_url, refresh_ts=now)
            self._emit_perception(node_id, snapshot)
            return status
        except Exception as exc:
            snapshot.mark_error(str(exc), refresh_ts=now)
            return dict(snapshot.raw_status)

    def _emit_perception(self, node_id: str, snapshot: RobotNodeSnapshot) -> None:
        """Turn a fresh perception frame into a cognitive event on significant change.
        Best-effort: never let perception plumbing break the poll loop."""
        if self._event_sink is None and self._perception_hook is None:
            return
        try:
            for event in self._perception.observe(node_id, snapshot.perception, snapshot.state):
                if self._event_sink is not None:
                    self._event_sink(event)                 # → cognition (LLM path)
                if self._perception_hook is not None:        # → fast emotion/expression reflex
                    try:
                        asyncio.get_running_loop().create_task(
                            self._perception_hook(event.payload["kind"], event.payload["perception"]))
                    except RuntimeError:
                        pass  # no running loop (not from the poll) — skip the reflex
        except Exception as exc:  # noqa: BLE001
            log.debug("embodied perception emit skipped: %s", exc)

    def list_nodes(self) -> list[dict[str, Any]]:
        return [self._snapshots[node_id].to_dict() for node_id in sorted(self._snapshots)]

    def get_node(self, node_id: str) -> dict[str, Any]:
        return self._require_snapshot(node_id).to_dict()

    def _locomotion_unsafe(self, node_id: str) -> tuple[bool, str]:
        """Coarse 'no locomotion is safe right now' check (lifted / tilted past limit /
        battery-critical / robot unseen) from the latest polled perception. Shared by the
        discrete-command clamp AND the free-text NLP path so neither can walk the robot in
        an unsafe state. A secondary net — the authoritative real-time reflexes are on-chip."""
        if not self._safety.get("enabled", True):
            return (False, "")
        snap = self._snapshots.get(node_id)
        if snap is None:
            return (False, "")
        if not snap.connected:
            return (True, "no live status — refusing locomotion without sensor confirmation")
        p = snap.perception
        tilt = float(self._safety.get("tilt_limit_deg", 60.0))
        crit = float(self._safety.get("critical_battery_v", 6.0))
        if p.is_lifted:
            return (True, "robot is lifted — refusing to walk in the air")
        if abs(p.pitch_deg) > tilt or abs(p.roll_deg) > tilt:
            return (True, f"unstable tilt (pitch={p.pitch_deg:.0f}deg roll={p.roll_deg:.0f}deg)")
        if 0.0 < p.battery_v < crit:   # 0.0 = battery unwired/unknown → don't block on it
            return (True, f"battery critical ({p.battery_v:.1f}V)")
        return (False, "")

    def _safety_gate(self, node_id: str, command: str) -> tuple[bool, str]:
        """Pre-dispatch clamp for a discrete LOCOMOTION command. Only ground-locomotion is
        gated so a safe response (stop/sit/emergency_stop/…) is never blocked."""
        if not self._safety.get("enabled", True) or command not in _LOCOMOTION:
            return (True, "")
        unsafe, reason = self._locomotion_unsafe(node_id)
        if unsafe:
            return (False, reason)
        if command in _FORWARD:
            snap = self._snapshots.get(node_id)
            if snap is not None:
                p = snap.perception
                hard_cm = float(self._safety.get("forward_block_distance_cm", 15.0))
                if p.is_obstacle_near or (0.0 < p.distance_cm <= hard_cm):
                    return (False, f"obstacle ahead ({p.distance_cm:.0f}cm) — refusing forward motion")
        return (True, "")

    async def execute_command(self, node_id: str, command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        client = self._require_client(node_id)
        allowed, reason = self._safety_gate(node_id, command)
        if not allowed:
            log.warning("Safety clamp blocked '%s' on %s: %s", command, node_id, reason)
            return {"ok": False, "blocked": True, "command": command,
                    "reason": reason, "source": "anima_safety_clamp"}
        result = await client.command(command, params or {})
        await self.refresh_node(node_id)
        return result

    async def run_nlp(self, node_id: str, text: str) -> dict[str, Any]:
        client = self._require_client(node_id)
        # Free-text NLP can't be pre-classified, and the robot-local /nlp path executes
        # motion WITHOUT passing through execute_command's per-command clamp. So when no
        # locomotion is safe (lifted/tilted/battery-critical/unseen), refuse the whole
        # NLP request rather than risk walking the robot in the air. Discrete safe verbs
        # (sit/stop/…) remain available via robot_dog_command.
        unsafe, reason = self._locomotion_unsafe(node_id)
        if unsafe:
            log.warning("Safety clamp blocked NLP on %s (%s): %r", node_id, reason, text[:80])
            return {"ok": False, "blocked": True, "reason": f"unsafe for motion: {reason}",
                    "source": "anima_safety_clamp", "text": text}
        robot_result: dict[str, Any] | None = None
        robot_error = ""

        try:
            robot_result = await client.nlp(text)
        except Exception as exc:
            robot_error = str(exc)
            log.warning("Robot NLP request failed for %s: %s", node_id, exc)

        if self._nlp_result_recognized(robot_result):
            assert robot_result is not None
            robot_result.setdefault("source", "robot_local")
            await self.refresh_node(node_id)
            return robot_result

        rule_plan = match_pidog_command_text(text)
        if rule_plan:
            command_result = await self.execute_command(node_id, rule_plan["command"], rule_plan["params"])
            return {
                "parsed": rule_plan["command"],
                "result": command_result,
                "source": "supervisor_rule",
                "planner": rule_plan,
                "robot_nlp": robot_result,
                "robot_error": robot_error,
            }

        llm_plan = await self._nlp_supervisor.plan(
            text,
            node_id=node_id,
            capabilities=self._require_snapshot(node_id).capabilities,
        )
        if llm_plan and llm_plan["confidence"] >= self._nlp_supervisor.min_confidence:
            command_result = await self.execute_command(node_id, llm_plan["command"], llm_plan["params"])
            return {
                "parsed": llm_plan["command"],
                "result": command_result,
                "source": "supervisor_llm",
                "planner": llm_plan,
                "robot_nlp": robot_result,
                "robot_error": robot_error,
            }

        if robot_result is not None:
            await self.refresh_node(node_id)
            if llm_plan:
                robot_result.setdefault("supervisor_plan", llm_plan)
            if robot_error:
                robot_result.setdefault("robot_error", robot_error)
            return robot_result

        return {
            "parsed": None,
            "message": "robot NLP unavailable and supervisor could not resolve the command",
            "robot_error": robot_error,
            "source": "supervisor_unresolved",
        }

    async def speak(self, node_id: str, text: str, blocking: bool = False) -> dict[str, Any]:
        client = self._require_client(node_id)
        result = await client.speak(text, blocking=blocking)
        await self.refresh_node(node_id)
        return result

    async def start_exploration(
        self,
        node_id: str,
        *,
        goal: str = "wander",
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        explorer = self._require_explorer(node_id)
        state = await explorer.start(goal=goal, policy=policy or {})
        self._require_snapshot(node_id).exploration = explorer._state
        return state

    async def stop_exploration(self, node_id: str, reason: str = "manual_stop") -> dict[str, Any]:
        explorer = self._require_explorer(node_id)
        state = await explorer.stop(reason=reason)
        self._require_snapshot(node_id).exploration = explorer._state
        await self.refresh_node(node_id)
        return state

    def get_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "node_count": len(self._snapshots),
            "nodes": self.list_nodes(),
        }

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.refresh_all()
                await asyncio.sleep(self._poll_interval_s)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.warning("Robotics poll loop error: %s", exc)
                await asyncio.sleep(1.0)

    def _require_client(self, node_id: str) -> PiDogApiClient:
        if node_id not in self._clients:
            raise KeyError(f"Unknown robotics node '{node_id}'")
        return self._clients[node_id]

    def _require_snapshot(self, node_id: str) -> RobotNodeSnapshot:
        if node_id not in self._snapshots:
            raise KeyError(f"Unknown robotics node '{node_id}'")
        return self._snapshots[node_id]

    def _require_explorer(self, node_id: str) -> ExplorationController:
        if node_id not in self._explorers:
            raise KeyError(f"Unknown robotics node '{node_id}'")
        return self._explorers[node_id]

    @staticmethod
    def _nlp_result_recognized(result: dict[str, Any] | None) -> bool:
        if not isinstance(result, dict):
            return False

        message = str(result.get("message", "") or "").lower()
        if "未识别" in message or "unrecognized" in message or "not recognized" in message:
            return False

        if result.get("parsed"):
            return True
        if result.get("command"):
            return True

        nested = result.get("result")
        if isinstance(nested, dict) and nested.get("command"):
            return True

        return bool(result.get("ok")) and not bool(result.get("error"))
