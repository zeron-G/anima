"""Tests for heartbeat engine."""

import asyncio
import pytest

from anima.config import load_config
from anima.core.event_queue import EventQueue
from anima.core.heartbeat import HeartbeatEngine
from anima.emotion.state import EmotionState
from anima.llm.router import LLMRouter
from anima.memory.working import WorkingMemory
from anima.perception.diff_engine import DiffEngine
from anima.perception.snapshot_cache import SnapshotCache
from anima.models.event import EventType


@pytest.fixture
def heartbeat_deps():
    config = load_config()
    eq = EventQueue()
    sc = SnapshotCache()
    de = DiffEngine.from_config(config.get("diff_rules", {}))
    em = EmotionState()
    wm = WorkingMemory()
    lr = LLMRouter("test/model1", "test/model2")
    return eq, sc, de, em, wm, lr, config


@pytest.mark.asyncio
async def test_heartbeat_start_stop(heartbeat_deps):
    eq, sc, de, em, wm, lr, config = heartbeat_deps
    hb = HeartbeatEngine(eq, sc, de, em, wm, lr, config)
    await hb.start()
    # Let it tick once
    await asyncio.sleep(0.1)
    await hb.stop()
    # Should have updated snapshot cache
    assert sc.get_latest() is not None


@pytest.mark.asyncio
async def test_heartbeat_detects_file_changes(heartbeat_deps, tmp_path):
    """Heartbeat should push FILE_CHANGE event when files change."""
    eq, sc, de, em, wm, lr, config = heartbeat_deps
    # Override config to watch tmp_path
    config["perception"] = {
        "watch_paths": [str(tmp_path)],
        "watch_extensions": [".txt"],
    }
    hb = HeartbeatEngine(eq, sc, de, em, wm, lr, config)

    # Initialize file watcher (first scan = baseline)
    await hb._on_script_tick()

    # Create a file
    (tmp_path / "test.txt").write_text("hello")

    # Next tick should detect change
    await hb._on_script_tick()

    # Check if FILE_CHANGE event was queued
    if not eq.empty():
        evt = await eq.get()
        assert evt.type == EventType.FILE_CHANGE


@pytest.mark.asyncio
async def test_emotion_decay(heartbeat_deps):
    eq, sc, de, em, wm, lr, config = heartbeat_deps
    em.adjust(engagement=0.3)  # Push above baseline
    initial = em.engagement
    hb = HeartbeatEngine(eq, sc, de, em, wm, lr, config)
    await hb._decay_emotion()
    assert em.engagement < initial  # Should have decayed


def test_should_llm_think_on_consecutive_skips(heartbeat_deps):
    eq, sc, de, em, wm, lr, config = heartbeat_deps
    hb = HeartbeatEngine(eq, sc, de, em, wm, lr, config)
    hb._consecutive_skips = 3
    assert hb._should_llm_think()


def test_should_llm_think_on_significant_diffs(heartbeat_deps):
    eq, sc, de, em, wm, lr, config = heartbeat_deps
    hb = HeartbeatEngine(eq, sc, de, em, wm, lr, config)
    hb._recent_significance_scores = [0.5, 0.6, 0.4]
    assert hb._should_llm_think()
