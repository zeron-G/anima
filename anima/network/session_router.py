"""Distributed session routing with locking."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Any

from anima.utils.ids import gen_id
from anima.utils.logging import get_logger

log = get_logger("network.session")


@dataclass
class Session:
    id: str = ""
    channel: str = ""         # "terminal", "dashboard", "discord", "webhook"
    user_id: str = ""         # channel-specific user identifier
    owner_node: str = ""      # node_id that owns this session
    locked_at: float = 0.0
    last_activity: float = 0.0

    def to_dict(self) -> dict:
        return {"id": self.id, "channel": self.channel, "user_id": self.user_id,
                "owner_node": self.owner_node, "locked_at": self.locked_at,
                "last_activity": self.last_activity}


class SessionRouter:
    """Routes sessions to nodes with distributed locking."""

    SESSION_TIMEOUT = 120.0  # seconds idle before auto-release

    def __init__(self, local_node_id: str):
        self._local_node_id = local_node_id
        self._sessions: dict[str, Session] = {}
        self._broadcast_fn: Callable | None = None  # Set to gossip_mesh.broadcast_event

    def set_broadcast(self, fn: Callable) -> None:
        """Set the broadcast function for session lock/release events."""
        self._broadcast_fn = fn

    def try_lock(self, session_id: str, channel: str = "", user_id: str = "") -> bool:
        """Try to lock a session for this node. Returns True if successful."""
        existing = self._sessions.get(session_id)

        if existing and existing.owner_node:
            if existing.owner_node == self._local_node_id:
                # Already own it
                existing.last_activity = time.time()
                return True
            # Another node owns it — check if timed out
            if time.time() - existing.last_activity > self.SESSION_TIMEOUT:
                log.info("Session %s timed out from %s, claiming", session_id, existing.owner_node)
            else:
                return False  # Another node actively owns it

        # Lock it
        session = Session(
            id=session_id, channel=channel, user_id=user_id,
            owner_node=self._local_node_id,
            locked_at=time.time(), last_activity=time.time(),
        )
        self._sessions[session_id] = session

        # Broadcast lock
        if self._broadcast_fn:
            asyncio.ensure_future(self._broadcast_fn({
                "type": "session_lock",
                "session_id": session_id,
                "node_id": self._local_node_id,
                "channel": channel,
                "timestamp": time.time(),
            }))

        log.info("Session locked: %s → %s", session_id, self._local_node_id)
        return True

    def release(self, session_id: str) -> None:
        """Release a session lock."""
        session = self._sessions.get(session_id)
        if session and session.owner_node == self._local_node_id:
            session.owner_node = ""
            if self._broadcast_fn:
                asyncio.ensure_future(self._broadcast_fn({
                    "type": "session_release",
                    "session_id": session_id,
                    "node_id": self._local_node_id,
                }))
            log.info("Session released: %s", session_id)

    def handle_remote_lock(self, session_id: str, remote_node_id: str, channel: str = "", timestamp: float = 0) -> None:
        """Handle a session lock from another node."""
        existing = self._sessions.get(session_id)

        if existing and existing.owner_node == self._local_node_id:
            # Conflict! Deterministic tiebreaker: lower node_id wins
            if remote_node_id < self._local_node_id:
                log.info("Session conflict %s: yielding to %s (lower ID)", session_id, remote_node_id)
                existing.owner_node = remote_node_id
                existing.last_activity = timestamp or time.time()
            else:
                log.info("Session conflict %s: keeping (our ID is lower)", session_id)
                return
        else:
            self._sessions[session_id] = Session(
                id=session_id, channel=channel, owner_node=remote_node_id,
                locked_at=timestamp or time.time(), last_activity=timestamp or time.time(),
            )

    def handle_remote_release(self, session_id: str, remote_node_id: str) -> None:
        """Handle a session release from another node."""
        session = self._sessions.get(session_id)
        if session and session.owner_node == remote_node_id:
            session.owner_node = ""

    def release_all_for_node(self, dead_node_id: str) -> list[str]:
        """Release all sessions owned by a dead node. Returns released session IDs."""
        released = []
        for sid, session in self._sessions.items():
            if session.owner_node == dead_node_id:
                session.owner_node = ""
                released.append(sid)
        if released:
            log.info("Released %d sessions from dead node %s", len(released), dead_node_id)
        return released

    def is_mine(self, session_id: str) -> bool:
        """Check if this node owns the given session."""
        session = self._sessions.get(session_id)
        return session is not None and session.owner_node == self._local_node_id

    def get_owner(self, session_id: str) -> str:
        """Get the owner node_id of a session, or '' if unowned."""
        session = self._sessions.get(session_id)
        return session.owner_node if session else ""

    def get_session_id(self, channel: str, user_id: str = "") -> str:
        """Generate a deterministic session ID from channel and user."""
        return f"{channel}:{user_id}" if user_id else f"{channel}:default"

    def list_sessions(self) -> list[dict]:
        return [s.to_dict() for s in self._sessions.values()]

    def cleanup_expired(self) -> list[str]:
        """Remove expired sessions. Called periodically."""
        now = time.time()
        expired = []
        for sid, session in list(self._sessions.items()):
            if session.owner_node and (now - session.last_activity) > self.SESSION_TIMEOUT:
                session.owner_node = ""
                expired.append(sid)
        return expired
