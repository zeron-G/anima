"""Gossip mesh — unified heartbeat + state exchange + failure detection.

Uses plain ZMQ (not zmq.asyncio) in threads to avoid Windows compatibility
issues. The gossip loop runs in a daemon thread.
"""

from __future__ import annotations

import asyncio
import collections
import math
import random
import threading
import time
from collections import defaultdict
from typing import Callable, Any

import zmq

from anima.network.protocol import NetworkMessage
from anima.network.node import NodeState, NodeIdentity
from anima.utils.logging import get_logger

log = get_logger("network.gossip")


class PhiAccrualDetector:
    """Phi Accrual failure detector."""

    def __init__(self, window_size: int = 100):
        self._intervals: dict[str, list[float]] = defaultdict(list)
        self._last_seen: dict[str, float] = {}
        self._window = window_size
        self._lock = threading.Lock()

    def report_heartbeat(self, node_id: str) -> None:
        now = time.time()
        with self._lock:
            if node_id in self._last_seen:
                interval = now - self._last_seen[node_id]
                history = self._intervals[node_id]
                history.append(interval)
                if len(history) > self._window:
                    history.pop(0)
            self._last_seen[node_id] = now

    def phi(self, node_id: str) -> float:
        with self._lock:
            if node_id not in self._last_seen:
                return 0.0
            history = list(self._intervals.get(node_id, []))
            last_seen = self._last_seen[node_id]
        # Computation outside lock (pure CPU, no shared state)
        if len(history) < 3:
            elapsed = time.time() - last_seen
            return elapsed / 5.0
        mean = sum(history) / len(history)
        n = len(history)
        denom = n - 1 if n >= 2 else n
        variance = sum((x - mean) ** 2 for x in history) / denom
        stddev = max(math.sqrt(variance), 0.5)
        elapsed = time.time() - last_seen
        if elapsed <= mean:
            return 0.0
        y = (elapsed - mean) / stddev
        p = 0.5 * math.erfc(y / math.sqrt(2))
        if p <= 0:
            return 99.0  # node is definitely dead
        phi = -math.log10(p)
        return max(0.0, phi)

    def get_last_seen(self, node_id: str) -> float:
        with self._lock:
            return self._last_seen.get(node_id, 0.0)


class GossipMesh:
    """Unified gossip mesh. Runs in a background thread for Windows compat."""

    STARTUP_GRACE = 30.0  # seconds after start before marking never-seen nodes as dead
    RECONNECT_INTERVAL = 30.0  # seconds between reconnect attempts for dead/suspect peers

    def __init__(
        self,
        identity: NodeIdentity,
        local_state: NodeState,
        network_secret: str = "",
        listen_port: int = 9420,
        gossip_interval: float = 5.0,
        suspect_phi: float = 8.0,
        dead_phi: float = 16.0,
    ):
        self._identity = identity
        self._local_state = local_state
        self._secret = network_secret
        self._port = listen_port

        # L-24: configurable gossip params (were class constants)
        self.GOSSIP_INTERVAL = gossip_interval
        self.SUSPECT_PHI = suspect_phi
        self.DEAD_PHI = dead_phi

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
        self._on_task_heartbeat: Callable | None = None   # sync(msg) — periodic heartbeat from executor

        # Pending outbound task messages (task_delegate / task_result)
        self._outbound_task_msgs: list[dict] = []

        # Event that wakes the gossip thread immediately when outbound messages
        # are queued (avoids waiting up to GOSSIP_INTERVAL for task messages).
        self._send_now = threading.Event()

        self._node_addr_map: dict[str, str] = {}  # node_id → currently connected addr
        self._reconnect_fail_counts: dict[str, int] = {}   # node_id → consecutive reconnect failures
        self._last_reconnect_per_peer: dict[str, float] = {}  # node_id → last reconnect attempt ts
        self._started_at: float = time.time()

        # Seen-cache for deduplication: msg_id → timestamp, expires after 60s
        self._seen_msgs: collections.OrderedDict = collections.OrderedDict()
        self._seen_ttl: float = 60.0  # seconds before a seen entry expires
        self._seen_max_entries: int = 5000  # hard cap to prevent unbounded growth

        # Event loop reference captured at start() time so the gossip thread
        # can safely post callbacks via call_soon_threadsafe.
        self._event_loop: asyncio.AbstractEventLoop | None = None

        # Stored reference so start() can sync the event loop to the delegate
        # even when attach_task_delegate() was called before start().
        self._task_delegate_ref: Any | None = None

    def set_callbacks(self, **kwargs: Any) -> None:
        with self._lock:
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
        # Sync the event loop to a previously attached TaskDelegate
        if self._task_delegate_ref is not None and self._event_loop is not None:
            self._task_delegate_ref.set_loop(self._event_loop)
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
        self._send_now.set()  # wake gossip thread immediately

    async def send_task_message(self, msg_type: str, payload: dict) -> None:
        """Queue a task protocol message for broadcast on the next gossip tick.

        Supported types: task_delegate, task_result, task_cancel,
                         task_status_query, task_status_reply.
        """
        with self._lock:
            self._outbound_task_msgs.append({"_msg_type": msg_type, **payload})
        self._send_now.set()  # wake gossip thread immediately

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
        self._task_delegate_ref = delegate
        # Share the event loop reference so handle_task_result can safely
        # resolve futures from the gossip background thread.
        if self._event_loop is not None:
            delegate.set_loop(self._event_loop)
        self._on_task_delegate = delegate.handle_incoming_task      # async coroutine
        self._on_task_result = delegate.handle_task_result           # sync callback
        self._on_task_cancel = delegate.handle_cancel                # async coroutine
        self._on_task_status_query = delegate.handle_status_query    # async coroutine
        self._on_task_status_reply = delegate.handle_status_reply    # sync callback
        self._on_task_heartbeat = delegate.handle_task_heartbeat     # sync callback
        log.debug("TaskDelegate attached to GossipMesh")

    def get_peers(self) -> dict[str, NodeState]:
        with self._lock:
            return dict(self._peers)

    def get_peer_state(self, node_id: str) -> "NodeState | None":
        """Return a shallow copy of a single peer's state, or None if unknown."""
        with self._lock:
            state = self._peers.get(node_id)
            if state is None:
                return None
            import copy
            return copy.copy(state)

    def get_alive_peers(self) -> dict[str, NodeState]:
        with self._lock:
            return {nid: s for nid, s in self._peers.items() if s.status == "alive"}

    def get_alive_count(self) -> int:
        with self._lock:
            return 1 + sum(1 for s in self._peers.values() if s.status == "alive")

    def get_diagnostics(self) -> dict:
        """Return structured health snapshot for the dashboard.

        Returns:
            {
                "self": {node_id, hostname, status, ip, port},
                "peers": [
                    {node_id, hostname, status, phi, last_seen, ip, port},
                    ...
                ],
            }
        """
        with self._lock:
            peers = []
            for nid, state in self._peers.items():
                phi = self._detector.phi(nid)
                last_seen = self._detector.get_last_seen(nid)
                peers.append({
                    "node_id": nid,
                    "hostname": getattr(state, "hostname", ""),
                    "status": state.status,
                    "phi": round(phi, 2),
                    "last_seen": last_seen,
                    "seconds_ago": round(time.time() - last_seen, 1) if last_seen else None,
                    "ip": getattr(state, "ip", ""),
                    "port": getattr(state, "port", 0),
                })
            alive_count = 1 + sum(1 for p in peers if p["status"] == "alive")
            return {
                "self": {
                    "node_id": self._identity.node_id,
                    "hostname": getattr(self._local_state, "hostname", ""),
                    "status": "alive",
                    "ip": getattr(self._local_state, "ip", ""),
                    "port": self._port,
                },
                "peers": peers,
                "alive_count": alive_count,
                "pending_events": len(self._incoming_events),
                "pending_task_msgs": len(self._outbound_task_msgs),
                "seen_cache_size": len(self._seen_msgs),
                "seen_cache_ttl": self._seen_ttl,
                "seen_cache_max_entries": self._seen_max_entries,
            }

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

        # Connect to all known peers (snapshot under lock to avoid races)
        with self._lock:
            addrs = list(self._peer_addresses)
        for addr in addrs:
            sub.connect(f"tcp://{addr}")
            log.info("Connected to peer: %s", addr)

        connected_peers: set[str] = set(addrs)
        last_gossip_time: float = 0.0
        last_reconnect_time: float = 0.0

        while self._running:
            # Clear before computing wait to avoid missing a concurrent set().
            self._send_now.clear()
            elapsed = time.time() - last_gossip_time
            wait_time = max(0.0, self.GOSSIP_INTERVAL - elapsed)
            self._send_now.wait(timeout=wait_time)

            now = time.time()
            do_gossip = (now - last_gossip_time) >= self.GOSSIP_INTERVAL

            if do_gossip:
                last_gossip_time = now

                # 1. Bump local state
                self._local_state.bump_version()

            # Drain outbound queues before entering send block
            with self._lock:
                pending = list(self._incoming_events)
                self._incoming_events.clear()
                pending_tasks = list(self._outbound_task_msgs)
                self._outbound_task_msgs.clear()

            # --- Block 1: Send (isolated — failure won't skip receive) ---
            sent_task_count = 0
            saved_type = "task_delegate"
            try:
                if do_gossip:
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

                # 3. Send any pending events (always — not just on gossip ticks)
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
                    saved_type = task_msg.get("_msg_type", "task_delegate")
                    task_msg.pop("_msg_type", None)
                    msg_type = saved_type
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
                    sent_task_count += 1
                    log.debug("Sent %s to %s", msg_type, target)
            except zmq.ZMQError as e:
                log.warning("Gossip send error (will retry task msgs next tick): %s", e)
                # Re-queue unsent task messages
                unsent = pending_tasks[sent_task_count:]
                if unsent:
                    # Restore _msg_type on the message that failed mid-send
                    # (already popped but send didn't complete)
                    if "_msg_type" not in unsent[0]:
                        unsent[0]["_msg_type"] = saved_type
                    with self._lock:
                        self._outbound_task_msgs[:0] = unsent

            # --- Block 2: Receive + failure detection (independent of send) ---
            try:
                # 4. Receive messages (always — drain on every wakeup)
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

                        # Dedup non-gossip messages by ID; drop expired TTL
                        if rmsg.type != "gossip":
                            if rmsg.ttl <= 0:
                                continue
                            rmsg.ttl -= 1
                            msg_now = time.time()
                            if rmsg.id in self._seen_msgs:
                                continue
                            self._seen_msgs[rmsg.id] = msg_now
                            # Evict entries older than _seen_ttl
                            while self._seen_msgs:
                                _oldest_id, _oldest_ts = next(iter(self._seen_msgs.items()))
                                if msg_now - _oldest_ts > self._seen_ttl:
                                    self._seen_msgs.popitem(last=False)
                                else:
                                    break
                            # Hard cap: evict oldest entries when over capacity
                            while len(self._seen_msgs) > self._seen_max_entries:
                                self._seen_msgs.popitem(last=False)

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
                        elif rmsg.type == "task_heartbeat":
                            self._handle_task_heartbeat(rmsg)
                    except Exception as e:
                        log.debug("Message processing error: %s", e)

                # 5. Check failures (only on gossip ticks — phi needs real intervals)
                if do_gossip:
                    self._check_failures()
                    # Per-peer backoff is handled inside _reconnect_dead_peers,
                    # so call on every gossip tick instead of gating globally.
                    self._reconnect_dead_peers(sub, connected_peers)
                    if (now - last_reconnect_time) >= self.RECONNECT_INTERVAL:
                        last_reconnect_time = now
                        self._identity.unregister_stale_nodes(max_dead_hours=1.0)
            except Exception as e:
                log.error("Gossip receive/check error: %s", e)

        pub.close()
        sub.close()
        ctx.term()

    def _safe_callback(self, cb: Callable | None, *args: Any) -> None:
        """Invoke a node-state callback safely from the gossip thread.

        Handles both sync and async callbacks:
        - async (coroutine function): scheduled via run_coroutine_threadsafe
        - sync: called directly
        """
        if cb is None:
            return
        if asyncio.iscoroutinefunction(cb):
            loop = self._event_loop
            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(cb(*args), loop)
            else:
                log.debug("No running event loop for async callback %s; dropping", getattr(cb, "__name__", cb))
        else:
            cb(*args)

    def _handle_gossip(self, msg: NetworkMessage, sub_socket, connected: set) -> None:
        remote = NodeState.from_dict(msg.payload)
        nid = msg.source_node

        with self._lock:
            old = self._peers.get(nid)
            old_status = old.status if old else None
            old_addr = f"{old.ip}:{old.port}" if (old and old.ip and old.port) else None

            if old is None or remote.version > old.version:
                remote.status = "alive"
                self._peers[nid] = remote
                self._identity.register_node(nid)

                # Auto-connect SUB to new peer; handle address change on reconnect
                if remote.ip and remote.port:
                    new_addr = f"{remote.ip}:{remote.port}"
                    if new_addr not in connected:
                        # Disconnect stale address if the peer restarted elsewhere
                        if old_addr and old_addr != new_addr and old_addr in connected:
                            sub_socket.disconnect(f"tcp://{old_addr}")
                            connected.discard(old_addr)
                            log.info("Disconnected stale address for %s: %s", nid, old_addr)
                        sub_socket.connect(f"tcp://{new_addr}")
                        connected.add(new_addr)
                        log.info("Auto-connected to new peer: %s at %s", nid, new_addr)
                    self._node_addr_map[nid] = new_addr

        self._detector.report_heartbeat(nid)

        if old_status is None:
            # First discovery
            self._safe_callback(self._on_node_alive, nid, remote)
            log.info("Node discovered: %s (%s)", nid, remote.hostname)
        elif old_status in ("suspect", "dead"):
            # Recovery from failure — clear reconnect backoff
            self._reconnect_fail_counts.pop(nid, None)
            self._last_reconnect_per_peer.pop(nid, None)
            self._safe_callback(self._on_node_alive, nid, remote)
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

    def _handle_task_heartbeat(self, msg: NetworkMessage) -> None:
        """Handle an incoming task_heartbeat from an executing node.

        Only acts on heartbeats for tasks this node originated
        (source_node == our node_id).  Forwards to the sync TaskDelegate
        callback so it can refresh the last-heartbeat timestamp.
        """
        source_node = msg.payload.get("source_node", "")
        if source_node and source_node != self._identity.node_id:
            return  # Heartbeat is for a different requester

        if self._on_task_heartbeat:
            self._on_task_heartbeat(msg.payload)
        log.debug(
            "Received task_heartbeat from %s: task=%s elapsed=%.1fs",
            msg.source_node,
            msg.payload.get("task_id"),
            msg.payload.get("elapsed_time", 0),
        )

    def _check_failures(self) -> None:
        pending_callbacks: list[tuple] = []
        pending_status_updates: list[tuple[str, str]] = []

        with self._lock:
            for nid, state in list(self._peers.items()):
                if state.status in ("dead", "isolated"):
                    continue
                phi = self._detector.phi(nid)
                if phi >= self.DEAD_PHI and state.status != "dead":
                    state.status = "dead"
                    pending_status_updates.append((nid, "dead"))
                    pending_callbacks.append((self._on_node_dead, nid, state))
                    log.warning("Node DEAD: %s (phi=%.1f)", nid, phi)
                elif phi >= self.SUSPECT_PHI and state.status == "alive":
                    state.status = "suspect"
                    pending_status_updates.append((nid, "suspect"))
                    pending_callbacks.append((self._on_node_suspect, nid, state))
                    log.warning("Node SUSPECT: %s (phi=%.1f)", nid, phi)

            # Mark registered nodes as dead if they never appeared in gossip
            # (e.g. stale entries from previous runs that never sent a heartbeat)
            my_id = self._identity.node_id
            known_peer_ids = set(self._peers.keys())
            grace_passed = (time.time() - self._started_at) > self.STARTUP_GRACE
            for reg_node in self._identity.registered_nodes:
                rid = reg_node.get("id", "")
                if rid == my_id:
                    continue
                if reg_node.get("status") == "alive" and rid not in known_peer_ids:
                    if grace_passed:
                        # Never seen in gossip this session — mark dead
                        pending_status_updates.append((rid, "dead"))
                        pending_callbacks.append((self._on_node_dead, rid, NodeState(node_id=rid)))
                        log.warning("Node DEAD (never seen in gossip): %s", rid)
                    # else: still within startup grace period — wait for first heartbeat

        # Fire callbacks FIRST (session cleanup, etc.) outside the lock to
        # prevent deadlock when sync callbacks acquire other locks.  If a
        # callback crashes, the stale status won't be permanently recorded.
        for cb, *args in pending_callbacks:
            self._safe_callback(cb, *args)

        # Then persist status (only after callbacks succeeded)
        for node_id, status in pending_status_updates:
            self._identity.update_node_status(node_id, status)

    def _reconnect_dead_peers(self, sub_socket, connected: set) -> None:
        """Ensure TCP connections exist for dead/suspect peers with exponential backoff.

        Called every RECONNECT_INTERVAL seconds (from _gossip_thread on gossip
        ticks).  Does not change peer state — status is managed by phi detector.
        Backoff: min(RECONNECT_INTERVAL * 2^fail_count, 300) seconds between attempts.
        """
        now = time.time()
        with self._lock:
            peers_snapshot = list(self._peers.items())
        for nid, state in peers_snapshot:
            if state.status not in ("dead", "suspect"):
                continue
            ip = getattr(state, "ip", "")
            port = getattr(state, "port", 0)
            if not ip or not port:
                continue

            # Exponential backoff + random jitter to prevent reconnect storms
            fail_count = self._reconnect_fail_counts.get(nid, 0)
            base_backoff = min(self.RECONNECT_INTERVAL * (2 ** fail_count), 300.0)
            backoff = base_backoff * random.uniform(0.8, 1.2)
            last_attempt = self._last_reconnect_per_peer.get(nid, 0.0)
            if (now - last_attempt) < backoff:
                continue

            self._last_reconnect_per_peer[nid] = now
            addr = f"{ip}:{port}"
            try:
                old_addr = self._node_addr_map.get(nid)
                if old_addr and old_addr != addr and old_addr in connected:
                    try:
                        sub_socket.disconnect(f"tcp://{old_addr}")
                        connected.discard(old_addr)
                        log.debug("Disconnected stale addr for %s: %s", nid, old_addr)
                    except zmq.ZMQError:
                        pass
                if addr not in connected:
                    sub_socket.connect(f"tcp://{addr}")
                    connected.add(addr)
                    log.debug("Re-connected to %s peer %s at %s (backoff=%.0fs)", state.status, nid, addr, backoff)
                else:
                    log.debug("Skip reconnect for %s peer %s at %s (already connected)", state.status, nid, addr)
                self._node_addr_map[nid] = addr
                # Connection succeeded — reset fail count
                self._reconnect_fail_counts.pop(nid, None)
            except zmq.ZMQError as e:
                self._reconnect_fail_counts[nid] = fail_count + 1
                log.debug("Reconnect failed for %s at %s (fail_count=%d, next_backoff=%.0fs): %s",
                          nid, addr, fail_count + 1,
                          min(self.RECONNECT_INTERVAL * (2 ** (fail_count + 1)), 300.0), e)
