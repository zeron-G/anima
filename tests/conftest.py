"""Shared test fixtures."""

import pytest

from anima.models.event import Event, EventType, EventPriority
from anima.models.perception_frame import DiffRule


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
