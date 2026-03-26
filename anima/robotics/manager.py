"""Manager for robotics nodes exposed to ANIMA and the desktop UI."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

from anima.robotics.exploration import ExplorationController
from anima.robotics.models import PIDOG_COMMANDS, RobotNodeConfig, RobotNodeSnapshot
from anima.robotics.pidog import PiDogApiClient
from anima.utils.logging import get_logger

log = get_logger("robotics.manager")


class RoboticsManager:
    """Tracks configured robot nodes, live status, and exploration state."""

    def __init__(self, config: dict[str, Any] | None = None, session: aiohttp.ClientSession | None = None) -> None:
        cfg = dict(config or {})
        self._enabled = bool(cfg.get("enabled", False))
        self._poll_interval_s = float(cfg.get("poll_interval_s", 2.0))
        self._exploration_defaults = dict(cfg.get("exploration") or {})
        self._session = session
        self._owns_session = session is None
        self._poll_task: asyncio.Task | None = None
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
            return status
        except Exception as exc:
            snapshot.mark_error(str(exc), refresh_ts=now)
            return dict(snapshot.raw_status)

    def list_nodes(self) -> list[dict[str, Any]]:
        return [self._snapshots[node_id].to_dict() for node_id in sorted(self._snapshots)]

    def get_node(self, node_id: str) -> dict[str, Any]:
        return self._require_snapshot(node_id).to_dict()

    async def execute_command(self, node_id: str, command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        client = self._require_client(node_id)
        result = await client.command(command, params or {})
        await self.refresh_node(node_id)
        return result

    async def run_nlp(self, node_id: str, text: str) -> dict[str, Any]:
        client = self._require_client(node_id)
        result = await client.nlp(text)
        await self.refresh_node(node_id)
        return result

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
