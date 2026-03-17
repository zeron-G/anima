"""Base channel — abstract interface for communication channels."""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Callable

from anima.utils.logging import get_logger

log = get_logger("channels")


class BaseChannel(ABC):
    """Abstract base for communication channels (Discord, Webhook, etc.)."""

    def __init__(self, channel_name: str):
        self.name = channel_name
        self._on_message: Callable | None = None
        self._send_fn: Callable | None = None

    def set_message_handler(self, handler: Callable) -> None:
        """Set the handler for incoming messages."""
        self._on_message = handler

    @abstractmethod
    async def start(self) -> None:
        """Start the channel (connect, authenticate, etc.)."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel cleanly."""
        pass

    @abstractmethod
    async def send(self, target: str, text: str) -> bool:
        """Send a message to a target (user/channel/room)."""
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        pass
