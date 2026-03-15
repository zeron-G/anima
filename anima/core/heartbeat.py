"""Heartbeat engine — ANIMA's heart. Three classes of heartbeat."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from anima.config import get
from anima.core.event_queue import EventQueue
from anima.emotion.state import EmotionState
from anima.llm.prompts import PromptBuilder
from anima.llm.router import LLMRouter
from anima.memory.working import WorkingMemory
from anima.models.event import Event, EventType, EventPriority
from anima.models.memory_item import MemoryItem, MemoryType
from anima.perception.diff_engine import DiffEngine
from anima.perception.file_watcher import FileWatcher
from anima.perception.snapshot_cache import SnapshotCache
from anima.perception.system_monitor import sample_system_state
from anima.utils.logging import get_logger

log = get_logger("heartbeat")


class HeartbeatEngine:
    """Three-class heartbeat engine.

    - Script heartbeat (15s): system sampling, file detection, emotion decay, alive confirmation
    - LLM heartbeat (5min): working memory aggregation, trend analysis, proactive thinking
    - Major heartbeat (1h): global self-check, memory compression (Phase 0 stub)
    """

    def __init__(
        self,
        event_queue: EventQueue,
        snapshot_cache: SnapshotCache,
        diff_engine: DiffEngine,
        emotion_state: EmotionState,
        working_memory: WorkingMemory,
        llm_router: LLMRouter,
        prompt_builder: PromptBuilder,
        config: dict,
    ) -> None:
        self._event_queue = event_queue
        self._snapshot_cache = snapshot_cache
        self._diff_engine = diff_engine
        self._emotion = emotion_state
        self._working_memory = working_memory
        self._llm_router = llm_router
        self._prompt_builder = prompt_builder

        self._script_interval = get("heartbeat.script_interval_s", 15)
        self._llm_interval = get("heartbeat.llm_interval_s", 300)
        self._major_interval = get("heartbeat.major_interval_s", 3600)

        # LLM trigger conditions
        self._min_significant_diffs = get("llm_trigger.min_significant_diffs", 3)
        self._significance_threshold = get("llm_trigger.significance_threshold", 0.3)
        self._max_consecutive_skips = get("llm_trigger.max_consecutive_skips", 3)

        # State
        self._last_snapshot: dict | None = None
        self._recent_significance_scores: list[float] = []
        self._consecutive_skips = 0
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._tick_count = 0
        self._tick_callback = None  # Dashboard heartbeat visualization
        self._tick_history: list[dict] = []  # Last 30 tick records

        # File watcher
        watch_paths = get("perception.watch_paths", ["."])
        watch_exts = get("perception.watch_extensions", None)
        self._file_watcher = FileWatcher(watch_paths, watch_exts)

    def set_tick_callback(self, callback) -> None:
        """Set callback for heartbeat tick visualization."""
        self._tick_callback = callback

    async def start(self) -> None:
        """Start all heartbeat loops."""
        self._running = True
        self._tasks = [
            asyncio.create_task(self._script_heartbeat_loop(), name="script_hb"),
            asyncio.create_task(self._llm_heartbeat_loop(), name="llm_hb"),
            asyncio.create_task(self._major_heartbeat_loop(), name="major_hb"),
        ]
        log.info("Heartbeat engine started (script=%ds, llm=%ds, major=%ds)",
                 self._script_interval, self._llm_interval, self._major_interval)

        # Push STARTUP event — agent introduces itself and scans environment
        await self._event_queue.put(Event(
            type=EventType.STARTUP,
            payload={"reason": "ANIMA boot — first heartbeat"},
            priority=EventPriority.NORMAL,
            source="heartbeat",
        ))

    async def stop(self) -> None:
        """Stop all heartbeat loops."""
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        log.info("Heartbeat engine stopped.")

    # ---- Script Heartbeat (15s) ----

    async def _script_heartbeat_loop(self) -> None:
        while self._running:
            try:
                await self._on_script_tick()
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.error("Script heartbeat error: %s", e)
            await asyncio.sleep(self._script_interval)

    async def _on_script_tick(self) -> None:
        """Script heartbeat handler — four independent operations."""
        self._tick_count += 1

        # Four independent methods
        snapshot = await self._sample_system()
        changes = await self._detect_file_changes()
        await self._decay_emotion()
        await self._confirm_alive()

        # Update snapshot cache (bridge to cognitive cycle)
        self._snapshot_cache.update(snapshot, changes)

        # Diff detection → push events on anomalies
        diff = self._diff_engine.compute_diff(snapshot, self._last_snapshot)

        if diff.significance_score > 0:
            self._recent_significance_scores.append(diff.significance_score)
            # Keep only last 5 minutes worth (20 ticks at 15s)
            if len(self._recent_significance_scores) > 20:
                self._recent_significance_scores.pop(0)

        if diff.has_alerts:
            await self._event_queue.put(Event(
                type=EventType.SYSTEM_ALERT,
                payload={"diff": {
                    k: {"old": v.old_value, "new": v.new_value, "delta": v.delta}
                    for k, v in diff.field_diffs.items() if v.significant
                }, "system_state": snapshot},
                priority=EventPriority.HIGH,
                source="heartbeat",
            ))

        if changes:
            await self._event_queue.put(Event(
                type=EventType.FILE_CHANGE,
                payload={"changes": changes},
                priority=EventPriority.NORMAL,
                source="heartbeat",
            ))

        self._last_snapshot = snapshot

        # Emit tick record for dashboard visualization
        tick_record = {
            "tick": self._tick_count,
            "tier": "script",
            "timestamp": time.time(),
            "cpu": snapshot.get("cpu_percent", 0),
            "mem": snapshot.get("memory_percent", 0),
            "significance": diff.significance_score,
            "has_alerts": diff.has_alerts,
            "file_changes": len(changes),
            "significant_fields": diff.significant_fields if diff.significance_score > 0 else [],
        }
        self._tick_history.append(tick_record)
        if len(self._tick_history) > 30:
            self._tick_history = self._tick_history[-30:]
        if self._tick_callback:
            try:
                self._tick_callback(tick_record)
            except Exception:
                pass

    async def _sample_system(self) -> dict:
        """System resource sampling."""
        return sample_system_state()

    async def _detect_file_changes(self) -> list[dict]:
        """File change detection."""
        return self._file_watcher.detect_changes()

    async def _decay_emotion(self) -> None:
        """Emotion decay toward baseline."""
        decay_rate = get("emotion.decay_rate", 0.05)
        self._emotion.decay(rate=decay_rate)

    async def _confirm_alive(self) -> None:
        """Alive confirmation — just log periodically."""
        if self._tick_count % 20 == 0:  # Every ~5 minutes
            log.info("♥ Alive (tick #%d, memory=%d/%d)",
                     self._tick_count,
                     self._working_memory.size,
                     self._working_memory.capacity)

    # ---- LLM Heartbeat (5min) ----

    async def _llm_heartbeat_loop(self) -> None:
        # First LLM tick after 60s (not full 5min) — so Eva wakes up faster
        await asyncio.sleep(60)
        # Force first tick to be a proactive "startup scan"
        self._consecutive_skips = self._max_consecutive_skips
        while self._running:
            try:
                if self._should_llm_think():
                    await self._on_llm_tick()
                    self._consecutive_skips = 0
                else:
                    self._consecutive_skips += 1
                    log.debug("LLM heartbeat skipped (consecutive=%d)",
                              self._consecutive_skips)
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.error("LLM heartbeat error: %s", e)
            await asyncio.sleep(self._llm_interval)

    def _should_llm_think(self) -> bool:
        """Check if LLM heartbeat should trigger thinking."""
        # Condition 1: enough significant diffs recently
        sig_count = sum(
            1 for s in self._recent_significance_scores
            if s > self._significance_threshold
        )
        if sig_count >= self._min_significant_diffs:
            return True

        # Condition 2: consecutive skips exceeded → forced scan
        if self._consecutive_skips >= self._max_consecutive_skips:
            return True

        return False

    async def _on_llm_tick(self) -> None:
        """LLM heartbeat: push SELF_THINKING event into cognitive cycle.

        Instead of doing a standalone LLM call (old architecture), push an
        event so the cognitive cycle processes it with full tool access.
        This is the key architectural fix — the agent can now take action
        during proactive thinking, not just produce text.
        """
        log.info("LLM heartbeat triggered — pushing SELF_THINKING event")

        # Build a summary of what happened since last think
        recent_sigs = [f"{s:.2f}" for s in self._recent_significance_scores[-5:]]
        wm_summary = self._working_memory.get_summary()

        await self._event_queue.put(Event(
            type=EventType.SELF_THINKING,
            payload={
                "reason": "periodic proactive thinking",
                "recent_significance": recent_sigs,
                "working_memory_summary": wm_summary,
                "tick_count": self._tick_count,
                "consecutive_skips": self._consecutive_skips,
            },
            priority=EventPriority.LOW,  # lower than user messages
            source="heartbeat",
        ))

        # Clear recent significance after pushing
        self._recent_significance_scores.clear()

    # ---- Major Heartbeat (1h) ----

    async def _major_heartbeat_loop(self) -> None:
        """Major heartbeat — Phase 0 stub."""
        await asyncio.sleep(self._major_interval)
        while self._running:
            try:
                log.info("Major heartbeat — global self-check (stub)")
                # Phase 0: just log
                # Future: memory compression, full self-check
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.error("Major heartbeat error: %s", e)
            await asyncio.sleep(self._major_interval)
