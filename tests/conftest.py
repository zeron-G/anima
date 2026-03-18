"""Shared test fixtures."""

import asyncio
import asyncio.runners as _runners
import os
import sys

import pytest

from anima.models.event import Event, EventType, EventPriority
from anima.models.perception_frame import DiffRule


# ── Fix: Windows Python 3.14 event loop teardown hang ──
# Two-layer fix for asyncio cleanup hangs on Windows:
# 1. Patch _cancel_all_tasks with a hard timeout (handles per-test teardown)
# 2. Force os._exit at session end (catches any remaining hangs)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    _orig_cancel = _runners._cancel_all_tasks

    def _cancel_with_timeout(loop):
        """Cancel tasks with 2s timeout — prevents indefinite hang."""
        try:
            pending = {t for t in asyncio.all_tasks(loop) if not t.done()}
            if not pending:
                return
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.wait(pending, timeout=2.0))
        except Exception:
            pass

    _runners._cancel_all_tasks = _cancel_with_timeout


def pytest_sessionfinish(session, exitstatus):
    """Force-exit on Windows after tests ran to avoid cleanup hangs."""
    if sys.platform == "win32" and session.testscollected > 0:
        os._exit(exitstatus)


@pytest.fixture
def sample_event():
    return Event(
        type=EventType.USER_MESSAGE,
        payload={"text": "hello"},
        priority=EventPriority.HIGH,
    )


@pytest.fixture
def default_diff_rules():
    return [
        DiffRule(field="cpu_percent", threshold=15),
        DiffRule(field="memory_percent", threshold=10),
        DiffRule(field="disk_percent", threshold=5),
        DiffRule(field="file_changes", threshold=0),
        DiffRule(field="process_count", threshold=5),
    ]
