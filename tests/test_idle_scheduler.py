"""Tests for idle scheduler — idle detection, task selection, heartbeat integration."""

import asyncio
import time

import pytest

from anima.core.idle_scheduler import (
    IdleDetector, IdleScheduler, IdleTask, TaskWeight,
    compute_system_idle_score, compute_queue_idle_score,
    _default_task_pool,
)
from anima.perception.user_activity import UserActivityDetector


# ── Helpers ──

class FakeEventQueue:
    """Minimal event queue stub."""
    def __init__(self, size=0):
        self._size = size
        self._events = []

    def qsize(self):
        return self._size

    async def put(self, event):
        self._events.append(event)


# ── Tests: system idle score ──

def test_system_idle_high_cpu():
    # CPU > 90 → 0.0, CPU 80-90 → 0.2 (lenient: games shouldn't block Eva)
    assert compute_system_idle_score({"cpu_percent": 95, "memory_percent": 50}) == 0.0
    assert compute_system_idle_score({"cpu_percent": 85, "memory_percent": 50}) == 0.2

def test_system_idle_high_mem():
    # MEM > 95 → 0.0 (raised from 90)
    assert compute_system_idle_score({"cpu_percent": 50, "memory_percent": 96}) == 0.0

def test_system_idle_low_usage():
    score = compute_system_idle_score({"cpu_percent": 10, "memory_percent": 30})
    assert score > 0.5

def test_system_idle_empty():
    score = compute_system_idle_score({})
    assert 0 <= score <= 1


# ── Tests: queue idle score ──

def test_queue_idle_empty():
    q = FakeEventQueue(0)
    assert compute_queue_idle_score(q) == 1.0

def test_queue_idle_full():
    q = FakeEventQueue(10)
    assert compute_queue_idle_score(q) == 0.0

def test_queue_idle_partial():
    q = FakeEventQueue(3)
    assert compute_queue_idle_score(q) == 0.2


# ── Tests: UserActivityDetector ──

def test_user_activity_message_idle():
    ua = UserActivityDetector(use_system_api=False)
    # No messages yet → infinite idle
    assert ua.get_message_idle_seconds() == float("inf")
    ua.record_user_message()
    assert ua.get_message_idle_seconds() < 1.0

def test_user_activity_score_active():
    ua = UserActivityDetector(use_system_api=False)
    ua.record_user_message()
    score = ua.compute_user_idle_score()
    assert score == 0.0  # Just messaged → active


# ── Tests: IdleDetector ──

def test_idle_detector_update():
    ua = UserActivityDetector(use_system_api=False)
    ua.record_user_message()
    q = FakeEventQueue(0)
    detector = IdleDetector(ua, q)
    score = detector.update({"cpu_percent": 10, "memory_percent": 30})
    # First update with active user → low score
    assert 0 <= score <= 1

def test_idle_detector_level():
    ua = UserActivityDetector(use_system_api=False)
    q = FakeEventQueue(0)
    detector = IdleDetector(ua, q)
    # Force idle scores — thresholds: busy<0.15, light<0.35, moderate<0.55, deep>=0.55
    detector._idle_score = 0.6
    assert detector.level == "deep"
    detector._idle_score = 0.45
    assert detector.level == "moderate"
    detector._idle_score = 0.25
    assert detector.level == "light"
    detector._idle_score = 0.1
    assert detector.level == "busy"

def test_idle_detector_trend():
    ua = UserActivityDetector(use_system_api=False)
    q = FakeEventQueue(0)
    detector = IdleDetector(ua, q)
    # Not enough history
    assert detector.trend == "stable"
    # Add rising history
    detector._idle_history = [0.1, 0.2, 0.3, 0.4, 0.5]
    assert detector.trend == "rising"
    # Add falling history
    detector._idle_history = [0.5, 0.4, 0.3, 0.2, 0.1]
    assert detector.trend == "falling"


# ── Tests: IdleScheduler ──

@pytest.mark.asyncio
async def test_scheduler_busy_skips():
    ua = UserActivityDetector(use_system_api=False)
    ua.record_user_message()  # just chatted → user_idle ≈ 0
    q = FakeEventQueue(10)    # full queue → queue_idle = 0
    detector = IdleDetector(ua, q)
    detector._idle_score = 0.0
    detector._alpha = 0  # disable EMA smoothing so tick doesn't change score
    scheduler = IdleScheduler(detector, q)
    await scheduler.tick({"cpu_percent": 90, "memory_percent": 90})  # high load
    assert len(q._events) == 0  # busy → no tasks dispatched

@pytest.mark.asyncio
async def test_scheduler_dispatches_light():
    ua = UserActivityDetector(use_system_api=False)
    q = FakeEventQueue(0)
    detector = IdleDetector(ua, q)
    detector._idle_score = 0.25  # light (0.15-0.35)
    scheduler = IdleScheduler(detector, q)
    # Force detector to return light level
    detector._alpha = 0  # disable smoothing so update doesn't change score
    await scheduler.tick({"cpu_percent": 10, "memory_percent": 30})
    # Should have dispatched a light-level task
    assert len(q._events) >= 1
    event = q._events[0]
    assert event.payload["idle_level"] == "light"

def test_scheduler_task_cooldown():
    ua = UserActivityDetector(use_system_api=False)
    q = FakeEventQueue(0)
    detector = IdleDetector(ua, q)
    detector._idle_score = 0.5
    scheduler = IdleScheduler(detector, q)
    # Mark all tasks as recently run
    for task in scheduler._task_pool:
        task.last_run = time.time()
    result = scheduler._select_next_task("light")
    assert result is None  # all on cooldown

def test_scheduler_get_status():
    ua = UserActivityDetector(use_system_api=False)
    q = FakeEventQueue(0)
    detector = IdleDetector(ua, q)
    scheduler = IdleScheduler(detector, q)
    status = scheduler.get_status()
    assert "idle_score" in status
    assert "idle_level" in status
    assert "pool_size" in status
    assert status["pool_size"] > 0


# ── Tests: heartbeat integration helpers ──

def test_llm_heartbeat_interval_busy():
    ua = UserActivityDetector(use_system_api=False)
    q = FakeEventQueue(0)
    detector = IdleDetector(ua, q)
    detector._idle_score = 0.05  # busy (< 0.15)
    scheduler = IdleScheduler(detector, q)
    interval = scheduler.get_llm_heartbeat_interval(600)
    assert interval > 90000  # skip

def test_llm_heartbeat_interval_deep():
    ua = UserActivityDetector(use_system_api=False)
    q = FakeEventQueue(0)
    detector = IdleDetector(ua, q)
    detector._idle_score = 0.6  # deep (>= 0.55)
    scheduler = IdleScheduler(detector, q)
    interval = scheduler.get_llm_heartbeat_interval(600)
    assert interval == 180

def test_major_heartbeat_interval_light():
    ua = UserActivityDetector(use_system_api=False)
    q = FakeEventQueue(0)
    detector = IdleDetector(ua, q)
    detector._idle_score = 0.25  # light (0.15-0.35) → skip major
    scheduler = IdleScheduler(detector, q)
    interval = scheduler.get_major_heartbeat_interval(1800)
    assert interval > 90000  # skip

def test_major_heartbeat_interval_deep():
    ua = UserActivityDetector(use_system_api=False)
    q = FakeEventQueue(0)
    detector = IdleDetector(ua, q)
    detector._idle_score = 0.6  # deep (>= 0.55)
    scheduler = IdleScheduler(detector, q)
    interval = scheduler.get_major_heartbeat_interval(1800)
    assert interval == 900  # halved


# ── Tests: task pool ──

def test_default_task_pool_not_empty():
    pool = _default_task_pool()
    assert len(pool) >= 10

def test_task_levels_ordered():
    pool = _default_task_pool()
    levels = {"light", "moderate", "deep"}
    for task in pool:
        assert task.min_idle_level in levels

def test_evolution_tasks_in_pool():
    pool = _default_task_pool()
    evo_tasks = [t for t in pool if t.handler.startswith("evolution.")]
    assert len(evo_tasks) >= 2  # proposal + full_cycle
