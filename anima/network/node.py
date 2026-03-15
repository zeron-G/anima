"""Node identity and state management."""

import json
import platform
import socket
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path

from anima.config import data_dir, get
from anima.utils.ids import gen_id
from anima.utils.logging import get_logger

log = get_logger("network.node")


class NodeStatus(str, Enum):
    JOINING = "joining"
    ALIVE = "alive"
    SUSPECT = "suspect"
    DEAD = "dead"
    UPDATING = "updating"
    ISOLATED = "isolated"


@dataclass
class NodeState:
    """State vector — exchanged via Gossip."""
    node_id: str = ""
    hostname: str = ""
    ip: str = ""
    port: int = 9420
    version: int = 0           # Incremented on every state change
    heartbeat_ts: float = 0.0  # Last heartbeat timestamp
    status: str = "alive"
    compute_tier: int = 2      # 1=cloud, 2=pc, 3=rpi, 4=micro
    capabilities: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    max_concurrent: int = 5
    current_load: float = 0.0
    agent_name: str = ""
    code_version: str = "0.1.0"
    uptime_s: int = 0
    emotion: dict = field(default_factory=dict)
    active_sessions: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NodeState":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def bump_version(self):
        self.version += 1
        self.heartbeat_ts = time.time()


class NodeIdentity:
    """Manages this node's persistent identity."""

    def __init__(self):
        self._path = data_dir() / "node.json"
        self._data = self._load()

    @property
    def node_id(self) -> str:
        return self._data["self_id"]

    @property
    def registered_nodes(self) -> list[dict]:
        return self._data.get("registered_nodes", [])

    def register_node(self, node_id: str) -> None:
        """Add a node to the registered list."""
        existing = {n["id"] for n in self._data["registered_nodes"]}
        if node_id not in existing:
            self._data["registered_nodes"].append({
                "id": node_id,
                "joined_at": time.time(),
                "status": "alive",
            })
            self._save()

    def update_node_status(self, node_id: str, status: str) -> None:
        for n in self._data["registered_nodes"]:
            if n["id"] == node_id:
                n["status"] = status
                if status == "dead":
                    n["dead_since"] = time.time()
                self._save()
                return

    def unregister_stale_nodes(self, max_dead_hours: float = 1.0) -> list[str]:
        """Remove nodes that have been dead for > max_dead_hours."""
        cutoff = time.time() - max_dead_hours * 3600
        removed = []
        kept = []
        for n in self._data["registered_nodes"]:
            if n.get("status") == "dead" and n.get("dead_since", 0) < cutoff:
                removed.append(n["id"])
            else:
                kept.append(n)
        if removed:
            self._data["registered_nodes"] = kept
            self._save()
            log.info("Unregistered stale nodes: %s", removed)
        return removed

    def get_active_count(self) -> int:
        """Count of registered nodes that are alive or suspect (not dead)."""
        return sum(
            1 for n in self._data["registered_nodes"]
            if n.get("status", "alive") not in ("dead",)
        )

    def is_majority(self, visible_count: int) -> bool:
        """Check if visible_count (including self) is a majority."""
        total = self.get_active_count()
        if total <= 1:
            return True
        return visible_count > total / 2

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                pass
        # First run — generate identity
        hostname = socket.gethostname()
        node_id = f"anima-{hostname.lower()}-{gen_id('')[:8]}"
        data = {
            "self_id": node_id,
            "registered_nodes": [
                {"id": node_id, "joined_at": time.time(), "status": "alive"}
            ],
            "created_at": time.time(),
        }
        self._save(data)
        log.info("Generated node identity: %s", node_id)
        return data

    def _save(self, data: dict | None = None) -> None:
        d = data or self._data
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(d, indent=2), encoding="utf-8")
