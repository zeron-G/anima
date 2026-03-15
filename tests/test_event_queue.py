"""Tests for the event queue."""

import asyncio
import pytest

from anima.core.event_queue import EventQueue
from anima.models.event import Event, EventType, EventPriority


@pytest.mark.asyncio
async def test_basic_put_get():
    q = EventQueue()
    evt = Event(type=EventType.USER_MESSAGE, payload={"text": "hi"})
    await q.put(evt)
    assert q.qsize() == 1
    got = await q.get()
    assert got.id == evt.id
    assert q.empty()


@pytest.mark.asyncio
async def test_priority_ordering():
    """Higher priority events should be dequeued first."""
    q = EventQueue()
    low = Event(type=EventType.TIMER, priority=EventPriority.LOW)
    high = Event(type=EventType.USER_MESSAGE, priority=EventPriority.HIGH)
    normal = Event(type=EventType.FILE_CHANGE, priority=EventPriority.NORMAL)

    await q.put(low)
    await q.put(high)
    await q.put(normal)

    first = await q.get()
    second = await q.get()
    third = await q.get()

    assert first.priority == EventPriority.HIGH
    assert second.priority == EventPriority.NORMAL
    assert third.priority == EventPriority.LOW


@pytest.mark.asyncio
async def test_get_timeout_returns_none():
    q = EventQueue()
    result = await q.get_timeout(timeout=0.05)
    assert result is None


@pytest.mark.asyncio
async def test_close_sends_shutdown():
    q = EventQueue()
    q.close()
    evt = await q.get()
    assert evt.type == EventType.SHUTDOWN


@pytest.mark.asyncio
async def test_close_drops_new_events():
    q = EventQueue()
    q.close()
    await q.put(Event(type=EventType.USER_MESSAGE))
    # Shutdown event from close + nothing else meaningful
    # The queue should have the shutdown event
    evt = await q.get()
    assert evt.type == EventType.SHUTDOWN


@pytest.mark.asyncio
async def test_drain():
    q = EventQueue()
    await q.put(Event(type=EventType.FILE_CHANGE))
    await q.put(Event(type=EventType.SYSTEM_ALERT))
    events = await q.drain()
    assert len(events) == 2
    assert q.empty()


@pytest.mark.asyncio
async def test_iter_events_stops_on_shutdown():
    q = EventQueue()
    await q.put(Event(type=EventType.FILE_CHANGE, payload={"path": "a.py"}))
    await q.put(Event(type=EventType.USER_MESSAGE, payload={"text": "hi"}, priority=EventPriority.HIGH))

    collected = []

    async def consume():
        async for evt in q.iter_events():
            collected.append(evt)

    task = asyncio.create_task(consume())
    # Let the consumer process the two events first
    await asyncio.sleep(0.05)
    q.close()  # sends SHUTDOWN — consumer will stop
    await task

    assert len(collected) == 2
    assert collected[0].type == EventType.USER_MESSAGE  # higher priority
