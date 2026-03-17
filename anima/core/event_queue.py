"""Unified event queue — only for external events (user messages, file changes, alerts)."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from anima.models.event import Event, EventType
from anima.utils.logging import get_logger

log = get_logger("event_queue")


class EventQueue:
    """Priority-based async event queue for external events.

    Heartbeats do NOT push through this queue — they call handlers directly.
    Only external events (user messages, file changes, system alerts) go here.
    """

    def __init__(self, maxsize: int = 256) -> None:
        self._queue: asyncio.PriorityQueue[Event] = asyncio.PriorityQueue(
            maxsize=maxsize
        )
        self._closed = False

    async def put(self, event: Event) -> None:
        """Add an event to the queue."""
        if self._closed:
            log.warning("Queue closed, dropping event: %s", event.type.name)
            return
        await self._queue.put(event)
        log.debug("Event queued: %s (priority=%d)", event.type.name, event.priority)

    def put_nowait(self, event: Event) -> None:
        """Add an event without waiting. Raises QueueFull if full."""
        if self._closed:
            return
        self._queue.put_nowait(event)

    async def get(self) -> Event:
        """Get the highest-priority event. Blocks until available."""
        return await self._queue.get()

    async def get_timeout(self, timeout: float) -> Event | None:
        """Get an event with timeout. Returns None on timeout."""
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()

    def close(self) -> None:
        """Mark queue as closed. Pending gets will see SHUTDOWN event."""
        self._closed = True
        # Push a shutdown event to unblock any waiting consumer
        try:
            self._queue.put_nowait(
                Event(type=EventType.SHUTDOWN, priority=10)  # type: ignore[arg-type]
            )
        except asyncio.QueueFull as e:
            log.debug("close: %s", e)

    async def drain(self) -> list[Event]:
        """Drain all pending events (for shutdown)."""
        events = []
        while not self._queue.empty():
            try:
                events.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events

    async def iter_events(self) -> AsyncIterator[Event]:
        """Async iterator that yields events until SHUTDOWN."""
        while True:
            event = await self.get()
            if event.type == EventType.SHUTDOWN:
                return
            yield event
