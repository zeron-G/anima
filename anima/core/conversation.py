"""Conversation management — buffer, persistence, restoration.

Extracted from cognitive.py to separate concerns:
- conversation.py: conversation buffer lifecycle (save, load, trim, checkpoint)
- cognitive.py: LLM execution loop
"""

from __future__ import annotations

from typing import Any

from anima.memory.store import MemoryStore
from anima.utils.logging import get_logger

log = get_logger("conversation")


class ConversationManager:
    """Manages the conversation buffer with DB persistence."""

    def __init__(self, memory_store: MemoryStore, max_turns: int = 20) -> None:
        self._memory_store = memory_store
        self._max_turns = max_turns
        self._buffer: list[dict[str, Any]] = []

    @property
    def buffer(self) -> list[dict[str, Any]]:
        return self._buffer

    @buffer.setter
    def buffer(self, value: list[dict[str, Any]]) -> None:
        self._buffer = value

    def add(self, role: str, content: str | list) -> None:
        """Add a message to the conversation buffer."""
        self._buffer.append({"role": role, "content": content})
        self.trim()

    def trim(self) -> None:
        """Keep conversation buffer within max size."""
        max_msgs = self._max_turns * 2
        if len(self._buffer) > max_msgs:
            self._buffer = self._buffer[-max_msgs:]

    def save_to_memory(self, role: str, content: str) -> None:
        """Persist a message to episodic memory."""
        self._memory_store.save_memory(
            content=content, type="chat", importance=0.6, metadata={"role": role}
        )

    def get_memory_summary(self, limit: int = 15) -> str:
        """Get a text summary of recent memories for the system prompt."""
        recent = self._memory_store.get_recent_memories(limit=limit)
        if not recent:
            return "(no recent memories)"
        return "\n".join(f"- [{m['type']}] {m['content'][:100]}" for m in recent)

    def load_from_db(self) -> None:
        """Load recent conversation from the database on startup.

        Restores context so Eva remembers what was said before restart.
        """
        recent = self._memory_store.get_recent_memories(limit=30)
        if not recent:
            log.info("No conversation history to restore")
            return

        chat_msgs = [m for m in recent if m.get("type") == "chat"]
        if not chat_msgs:
            return

        chat_msgs.reverse()  # Oldest first

        restored = 0
        for m in chat_msgs:
            role = m.get("metadata", {}).get("role", "assistant")
            content = m.get("content", "")
            if content and role in ("user", "assistant"):
                self._buffer.append({"role": role, "content": content})
                restored += 1

        self.trim()
        log.info("Restored %d conversation messages from DB", restored)

    def restore_from_checkpoint(self, checkpoint: dict) -> None:
        """Restore conversation from an evolution checkpoint."""
        conv = checkpoint.get("conversation", [])
        if conv:
            self._buffer = conv
            self.trim()
            log.info("Restored %d messages from checkpoint", len(self._buffer))
