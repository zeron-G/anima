"""Per-session state management for multi-user isolation.

Each session (identified by source like "discord:12345" or "telegram:67890")
gets its own conversation buffer and emotion state. Sessions expire after
a configurable TTL.

When SessionManager is not wired (single-user mode), the system uses the
global CognitiveContext conversation/emotion as before.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from anima.emotion.state import EmotionState
from anima.utils.logging import get_logger

log = get_logger("session_manager")


@dataclass
class SessionState:
    """Per-session isolated state."""
    session_id: str
    user_id: str = ""
    channel: str = ""
    conversation: list[dict] = field(default_factory=list)
    emotion: EmotionState = field(default_factory=lambda: EmotionState())
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    message_count: int = 0

    def trim_conversation(self, max_turns: int = 100) -> None:
        if len(self.conversation) > max_turns:
            self.conversation = self.conversation[-max_turns:]


class SessionManager:
    """Manages per-session state with TTL-based expiration."""

    def __init__(
        self,
        max_sessions: int = 50,
        session_ttl_s: int = 3600,
    ) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._max_sessions = max_sessions
        self._session_ttl = session_ttl_s

    def get_or_create(
        self,
        session_id: str,
        user_id: str = "",
        channel: str = "",
    ) -> SessionState:
        """Get existing session or create a new one."""
        if session_id in self._sessions:
            session = self._sessions[session_id]
            session.last_active = time.time()
            return session

        # Evict oldest if at capacity
        if len(self._sessions) >= self._max_sessions:
            self._evict_oldest()

        session = SessionState(
            session_id=session_id,
            user_id=user_id,
            channel=channel,
        )
        self._sessions[session_id] = session
        log.info("New session: %s (user=%s, channel=%s)", session_id, user_id, channel)
        return session

    def get(self, session_id: str) -> SessionState | None:
        """Get session without creating."""
        return self._sessions.get(session_id)

    def cleanup_expired(self) -> int:
        """Remove expired sessions. Returns count removed."""
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items()
            if now - s.last_active > self._session_ttl
        ]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            log.info("Cleaned up %d expired sessions", len(expired))
        return len(expired)

    def _evict_oldest(self) -> None:
        """Remove the least recently active session."""
        if not self._sessions:
            return
        oldest = min(self._sessions.values(), key=lambda s: s.last_active)
        del self._sessions[oldest.session_id]
        log.info("Evicted oldest session: %s", oldest.session_id)

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    def list_sessions(self) -> list[dict]:
        """List active sessions for dashboard."""
        return [
            {
                "session_id": s.session_id,
                "user_id": s.user_id,
                "channel": s.channel,
                "messages": s.message_count,
                "last_active": s.last_active,
                "age_s": int(time.time() - s.created_at),
            }
            for s in self._sessions.values()
        ]
