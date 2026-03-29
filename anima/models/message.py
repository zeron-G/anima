"""Unified message model for cross-channel communication.

All channel adapters construct a MessagePayload instead of raw dicts,
then call .to_dict() for backward-compatible handler invocation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class MessagePayload:
    """Normalized message from any channel."""

    text: str
    """Message text content."""

    user: str
    """User identifier (platform-specific ID)."""

    user_name: str = ""
    """Human-readable display name."""

    channel: str = ""
    """Channel type: 'discord', 'telegram', 'webhook', 'terminal'."""

    source: str = ""
    """Session key, e.g. 'discord:12345' or 'telegram:67890'."""

    channel_id: str = ""
    """Channel/chat ID within the platform."""

    guild: str = ""
    """Server/group name (Discord guild, Telegram group, etc.)."""

    metadata: dict = field(default_factory=dict)
    """Extra platform-specific data."""

    def to_dict(self) -> dict:
        """Convert to dict for backward-compatible handler interface."""
        d = asdict(self)
        # Merge metadata into top level for backward compat
        extra = d.pop("metadata", {})
        d.update(extra)
        return d
