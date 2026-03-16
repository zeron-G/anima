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
        self._on_task_delegate: Callable | None = None      # async(task_dict) — called when a task arrives
        self._on_task_result: Callable | None = None      # sync(result_dict) — called when a result arrives
        self._on_task_cancel: Callable | None = None      # async(task_id) — called when a cancel arrives
        self._on_task_status_query: Callable | None = None  # async(msg) — status query from remote node
        self._on_task_status_reply: Callable | None = None  # sync(msg) — status reply to a query we sent

        # Pending outbound task messages (task_delegate / task_result)
        self._outbound_task_msgs: list[dict] = []

        # Event loop reference captured at start() time so the gossip thread
        # can safely post callbacks via call_soon_threadsafe.
        self._event_loop: asyncio.AbstractEventLoop | None = None

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
        # Capture the running event loop so the gossip thread can post
        # callbacks back onto it safely via call_soon_threadsafe.
        try:
            self._event_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._event_loop = None
        self._thread = threading.Thread(target=self._gossip_thread, daemon=True)
        self._thread.start()
        log.info("Gossip mesh started on port %d", self._port)

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Explicitly set the event loop (useful when start() is not awaited
        inside a running loop, e.g. during testing)."""
        self._event_loop = loop

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

    async def send_task_message(self, msg_type: str, payload: dict) -> None:
        """Queue a task protocol message for broadcast on the next gossip tick.

        Supported types: task_delegate, task_result, task_cancel,
                         task_status_query, task_status_reply.
        """
        with self._lock:
            self._outbound_task_msgs.append({"_msg_type": msg_type, **payload})

    def attach_task_delegate(self, delegate: "Any") -> None:
        """Wire a TaskDelegate instance into the gossip mesh.

        This sets up:
          - delegate.broadcast_fn  → gossip send_task_message (so the delegate
            can send task_delegate / task_result messages over the network)
          - gossip._on_task_delegate → delegate.handle_incoming_task
          - gossip._on_task_result  → delegate.handle_task_result
          - delegate._loop          → the captured asyncio event loop so that
            future resolution from the gossip thread works correctly

        Call this after constructing both objects, before start().
        """

        async def _broadcast(msg: dict) -> None:
            # Allow task_delegate, task_result, and task_cancel
            msg_type = msg.pop("type", "task_delegate")
            await self.send_task_message(msg_type, msg)

        delegate.set_broadcast(_broadcast)
        # Share the event loop reference so handle_task_result can safely
        # resolve futures from the gossip background thread.
        if self._event_loop is not None:
            delegate.set_loop(self._event_loop)
        self._on_task_delegate = delegate.handle_incoming_task      # async coroutine
        self._on_task_result = delegate.handle_task_result           # sync callback
        self._on_task_cancel = delegate.handle_cancel                # async coroutine
        self._on_task_status_query = delegate.handle_status_query    # async coroutine
        self._on_task_status_reply = delegate.handle_status_reply    # sync callback
        log.debug("TaskDelegate attached to GossipMesh")

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
                    pending_tasks = list(self._outbound_task_msgs)
                    self._outbound_task_msgs.clear()
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

                # 3b. Send pending task messages (task_delegate / task_result / task_cancel)
                for task_msg in pending_tasks:
                    msg_type = task_msg.pop("_msg_type", "task_delegate")
                    # Resolve target node: check nested task dict first, then
                    # top-level target_node (used by status query/reply and cancel).
                    target = (
                        task_msg.get("task", {}).get("target_node")
                        or task_msg.get("target_node")
                        or "*"
                    )
                    tmsg = NetworkMessage(
                        type=msg_type,
                        source_node=self._identity.node_id,
                        target_node=target,
                        payload=task_msg,
                    )
                    if self._secret:
                        tmsg.sign(self._secret)
                    pub.send(tmsg.pack())
                    log.debug("Sent %s to %s", msg_type, target)

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
                        elif rmsg.type == "task_delegate":
                            self._handle_task_delegate(rmsg)
                        elif rmsg.type == "task_result":
                            self._handle_task_result(rmsg)
                        elif rmsg.type == "task_cancel":
                            self._handle_task_cancel(rmsg)
                        elif rmsg.type == "task_status_query":
                            self._handle_task_status_query(rmsg)
                        elif rmsg.type == "task_status_reply":
                            self._handle_task_status_reply(rmsg)
                    except Exception as e:
                        log.debug("Message processing error: %s", e)

                # 5. Check failures
                self._check_failures()

            except Exception as e:
                log.error("Gossip thread error: %s", e)

            # Sleep to the next absolute tick — prevents interval drift
            next_tick = time.time() + self.GOSSIP_INTERVAL
            elapsed = time.time() - (next_tick - self.GOSSIP_INTERVAL)
            sleep_time = max(0, self.GOSSIP_INTERVAL - elapsed)
            time.sleep(sleep_time)

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

    def _handle_task_delegate(self, msg: NetworkMessage) -> None:
        """Handle an incoming task_delegate message from a remote node."""
        target_node = msg.target_node
        if target_node and target_node != "*" and target_node != self._identity.node_id:
            return  # Not for us

        if self._on_task_delegate:
            task_dict = msg.payload.get("task", msg.payload)
            loop = self._event_loop
            if loop is not None and loop.is_running():
                # Post the coroutine onto the main event loop from this thread.
                asyncio.run_coroutine_threadsafe(
                    self._on_task_delegate(task_dict), loop
                )
            else:
                log.debug(
                    "No running event loop for task_delegate; dropping task %s",
                    task_dict.get("task_id"),
                )
        log.debug(
            "Received task_delegate from %s: %s",
            msg.source_node, msg.payload.get("task", {}).get("task_id"),
        )

    def _handle_task_result(self, msg: NetworkMessage) -> None:
        """Handle an incoming task_result message from a remote node.

        ``source_node`` in the payload is the original task requester.
        We only act on results destined for this node; results for other nodes
        are forwarded by the PUB/SUB topology naturally (every subscriber sees
        every message) so we simply ignore ones not addressed to us.
        """
        # Ignore our own re-broadcast
        if msg.source_node == self._identity.node_id:
            return

        # Only process results that were requested by this node
        requester = msg.payload.get("source_node", "")
        if requester and requester != self._identity.node_id:
            return  # Result is for a different node

        if self._on_task_result:
            self._on_task_result(msg.payload)
        log.debug(
            "Received task_result from %s: task=%s status=%s",
            msg.source_node,
            msg.payload.get("task_id"),
            msg.payload.get("status"),
        )

    def _handle_task_cancel(self, msg: NetworkMessage) -> None:
        """Handle an incoming task_cancel message from the originating node.

        Forwards the cancellation to the TaskDelegate so it can stop executing
        (or mark as cancelled if not yet started).
        """
        # Only process if this node is the target executor
        target_node = msg.payload.get("target_node", "")
        if target_node and target_node != "*" and target_node != self._identity.node_id:
            return

        if self._on_task_cancel:
            task_id = msg.payload.get("task_id", "")
            loop = self._event_loop
            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._on_task_cancel(task_id), loop
                )
            else:
                log.debug("No running event loop for task_cancel; dropping cancel for %s", task_id)
        log.debug("Received task_cancel from %s: task=%s", msg.source_node, msg.payload.get("task_id"))

    def _handle_task_status_query(self, msg: NetworkMessage) -> None:
        """Handle an incoming task_status_query from a remote node.

        Only act on queries addressed to this node (or broadcast queries).
        Forwards to the TaskDelegate which sends back a task_status_reply.
        """
        target_node = msg.payload.get("target_node", "")
        if target_node and target_node != "*" and target_node != self._identity.node_id:
            return

        if self._on_task_status_query:
            loop = self._event_loop
            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._on_task_status_query(msg.payload), loop
                )
            else:
                log.debug(
                    "No running event loop for task_status_query; dropping query %s",
                    msg.payload.get("correlation_id"),
                )
        log.debug(
            "Received task_status_query from %s: task=%s",
            msg.source_node, msg.payload.get("task_id"),
        )

    def _handle_task_status_reply(self, msg: NetworkMessage) -> None:
        """Handle an incoming task_status_reply (sync — resolves a Future).

        Only act on replies addressed to this node.
        """
        target_node = msg.payload.get("target_node", "")
        if target_node and target_node != "*" and target_node != self._identity.node_id:
            return

        if self._on_task_status_reply:
            self._on_task_status_reply(msg.payload)
        log.debug(
            "Received task_status_reply from %s: task=%s status=%s",
            msg.source_node,
            msg.payload.get("task_id"),
            msg.payload.get("status"),
        )

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

            # Mark registered nodes as dead if they never appeared in gossip
            # (e.g. stale entries from previous runs that never sent a heartbeat)
            my_id = self._identity.node_id
            known_peer_ids = set(self._peers.keys())
            for reg_node in self._identity.registered_nodes:
                rid = reg_node.get("id", "")
                if rid == my_id:
                    continue
                if reg_node.get("status") == "alive" and rid not in known_peer_ids:
                    # Never seen in gossip this session — mark dead
                    self._identity.update_node_status(rid, "dead")
                    log.warning("Node DEAD (never seen in gossip): %s", rid)
