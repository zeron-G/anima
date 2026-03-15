"""Gossip mesh — unified heartbeat + state exchange + failure detection.

Uses plain ZMQ (not zmq.asyncio) in threads to avoid Windows compatibility
issues. The gossip loop runs in a daemon thread.
"""

from __future__ import annotations

import asyncio
import math
import random
import threading
import time
from collections import defaultdict
from typing import Callable, Any

import zmq
import msgpack

from anima.network.protocol import NetworkMessage
from anima.network.node import NodeState, NodeStatus, NodeIdentity
from anima.utils.logging import get_logger

log = get_logger("network.gossip")


class PhiAccrualDetector:
    """Phi Accrual failure detector."""

    def __init__(self, window_size: int = 100):
        self._intervals: dict[str, list[float]] = defaultdict(list)
        self._last_seen: dict[str, float] = {}
        self._window = window_size

    def report_heartbeat(self, node_id: str) -> None:
        now = time.time()
        if node_id in self._last_seen:
            interval = now - self._last_seen[node_id]
            history = self._intervals[node_id]
            history.append(interval)
            if len(history) > self._window:
                history.pop(0)
        self._last_seen[node_id] = now

    def phi(self, node_id: str) -> float:
        if node_id not in self._last_seen:
            return 0.0
        history = self._intervals.get(node_id, [])
        if len(history) < 3:
            elapsed = time.time() - self._last_seen[node_id]
            return elapsed / 5.0
        mean = sum(history) / len(history)
        variance = sum((x - mean) ** 2 for x in history) / len(history)
        stddev = max(math.sqrt(variance), 0.1)
        elapsed = time.time() - self._last_seen[node_id]
        if elapsed <= mean:
            return 0.0
        y = (elapsed - mean) / stddev
        return max(0, y * y / 2.0)


class GossipMesh:
    """Unified gossip mesh. Runs in a background thread for Windows compat."""

    GOSSIP_INTERVAL = 5.0
    SUSPECT_PHI = 8.0
    DEAD_PHI = 16.0

    def __init__(
        self,
        identity: NodeIdentity,
        local_state: NodeState,
        network_secret: str = "",
        listen_port: int = 9420,
    ):
        self._identity = identity
        self._local_state = local_state
        self._secret = network_secret
        self._port = listen_port

        self._peers: dict[str, NodeState] = {}
        self._peer_addresses: set[str] = set()
        self._detector = PhiAccrualDetector()
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Pending events from other nodes (consumed by main loop)
        self._incoming_events: list[dict] = []

        # Callbacks (called from the gossip thread)
        self._on_node_alive: Callable | None = None
        self._on_node_suspect: Callable | None = None
        self._on_node_dead: Callable | None = None
        self._on_event: Callable | None = None

    def set_callbacks(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, f"_{k}", v)

    def add_peer(self, address: str) -> None:
        self._peer_addresses.add(address)

    def configure_peers(self, peer_list: list[str]) -> None:
        for addr in peer_list:
            self._peer_addresses.add(addr)

    async def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._gossip_thread, daemon=True)
        self._thread.start()
        log.info("Gossip mesh started on port %d", self._port)

    async def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        log.info("Gossip mesh stopped")

    async def broadcast_event(self, event_data: dict) -> None:
        """Broadcast an event (called from async code, sends via thread-safe queue)."""
        # We need to send via the PUB socket which lives in the gossip thread.
        # Store it and send on next gossip tick.
        with self._lock:
            self._incoming_events.append(event_data)

    def get_peers(self) -> dict[str, NodeState]:
        with self._lock:
            return dict(self._peers)

    def get_alive_peers(self) -> dict[str, NodeState]:
        with self._lock:
            return {nid: s for nid, s in self._peers.items() if s.status == "alive"}

    def get_alive_count(self) -> int:
        with self._lock:
            return 1 + sum(1 for s in self._peers.values() if s.status == "alive")

    # ── Thread ──

    def _gossip_thread(self) -> None:
        ctx = zmq.Context()

        pub = ctx.socket(zmq.PUB)
        pub.setsockopt(zmq.LINGER, 0)
        pub.bind(f"tcp://*:{self._port}")

        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.SUBSCRIBE, b"")
        sub.setsockopt(zmq.RCVTIMEO, 100)
        sub.setsockopt(zmq.LINGER, 0)

        # Connect to all known peers
        for addr in self._peer_addresses:
            sub.connect(f"tcp://{addr}")
            log.info("Connected to peer: %s", addr)

        connected_peers: set[str] = set(self._peer_addresses)

        while self._running:
            try:
                # 1. Bump local state
                self._local_state.bump_version()

                # 2. Broadcast own state
                msg = NetworkMessage(
                    type="gossip",
                    source_node=self._identity.node_id,
                    target_node="*",
                    payload=self._local_state.to_dict(),
                )
                if self._secret:
                    msg.sign(self._secret)
                pub.send(msg.pack())

                # 3. Send any pending events
                with self._lock:
                    pending = list(self._incoming_events)
                    self._incoming_events.clear()
                for evt in pending:
                    emsg = NetworkMessage(
                        type="event",
                        source_node=self._identity.node_id,
                        target_node="*",
                        payload=evt,
                    )
                    if self._secret:
                        emsg.sign(self._secret)
                    pub.send(emsg.pack())

                # 4. Receive messages
                for _ in range(50):  # drain up to 50 messages per tick
                    try:
                        data = sub.recv(zmq.NOBLOCK)
                    except zmq.Again:
                        break

                    try:
                        rmsg = NetworkMessage.unpack(data)
                        if rmsg.source_node == self._identity.node_id:
                            continue
                        if self._secret and not rmsg.verify(self._secret):
                            continue

                        if rmsg.type == "gossip":
                            self._handle_gossip(rmsg, sub, connected_peers)
                        elif rmsg.type == "event":
                            if self._on_event:
                                self._on_event(rmsg.payload)
                    except Exception as e:
                        log.debug("Message processing error: %s", e)

                # 5. Check failures
                self._check_failures()

            except Exception as e:
                log.error("Gossip thread error: %s", e)

            time.sleep(self.GOSSIP_INTERVAL)

        pub.close()
        sub.close()
        ctx.term()

    def _handle_gossip(self, msg: NetworkMessage, sub_socket, connected: set) -> None:
        remote = NodeState.from_dict(msg.payload)
        nid = msg.source_node

        with self._lock:
            old = self._peers.get(nid)
            old_status = old.status if old else None

            if old is None or remote.version > old.version:
                remote.status = "alive"
                self._peers[nid] = remote
                self._identity.register_node(nid)

                # Auto-connect SUB to new peer if we don't have it
                if remote.ip and remote.port:
                    addr = f"{remote.ip}:{remote.port}"
                    if addr not in connected:
                        sub_socket.connect(f"tcp://{addr}")
                        connected.add(addr)
                        log.info("Auto-connected to new peer: %s at %s", nid, addr)

        self._detector.report_heartbeat(nid)

        if old_status is None:
            # First discovery
            if self._on_node_alive:
                self._on_node_alive(nid, remote)
            log.info("Node discovered: %s (%s)", nid, remote.hostname)
        elif old_status in ("suspect", "dead") and remote.status == "alive":
            # Recovery from failure
            if self._on_node_alive:
                self._on_node_alive(nid, remote)
            log.info("Node recovered: %s (%s)", nid, remote.hostname)

    def _check_failures(self) -> None:
        with self._lock:
            for nid, state in list(self._peers.items()):
                if state.status in ("dead", "isolated"):
                    continue
                phi = self._detector.phi(nid)
                if phi >= self.DEAD_PHI and state.status != "dead":
                    state.status = "dead"
                    self._identity.update_node_status(nid, "dead")
                    if self._on_node_dead:
                        self._on_node_dead(nid, state)
                    log.warning("Node DEAD: %s (phi=%.1f)", nid, phi)
                elif phi >= self.SUSPECT_PHI and state.status == "alive":
                    state.status = "suspect"
                    if self._on_node_suspect:
                        self._on_node_suspect(nid, state)
                    log.warning("Node SUSPECT: %s (phi=%.1f)", nid, phi)
