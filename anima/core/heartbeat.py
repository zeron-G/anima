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

# Import here to satisfy type hints; actual instance injected via set_scheduler()
from anima.core.scheduler import Scheduler

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
        self._llm_interval = get("heartbeat.llm_interval_s", 180)
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
        self._scheduler: Scheduler | None = None
        self._gossip_mesh = None  # Set via set_gossip_mesh()
        self._evolution_engine = None  # Set via set_evolution_engine()
        self._is_restart = False  # Set via mark_as_restart()
        self._restart_reason = ""

        # File watcher
        watch_paths = get("perception.watch_paths", ["."])
        watch_exts = get("perception.watch_extensions", None)
        self._file_watcher = FileWatcher(watch_paths, watch_exts)

    def mark_as_restart(self, reason: str, tick_count: int = 0) -> None:
        """Mark this startup as an evolution restart."""
        self._is_restart = True
        self._restart_reason = reason
        if tick_count > 0:
            self._tick_count = tick_count  # Preserve uptime counter

    def set_tick_callback(self, callback) -> None:
        """Set callback for heartbeat tick visualization."""
        self._tick_callback = callback

    def set_scheduler(self, scheduler: Scheduler) -> None:
        """Set the cron scheduler instance."""
        self._scheduler = scheduler

    def set_gossip_mesh(self, gossip_mesh) -> None:
        """Set the gossip mesh for distributed heartbeat."""
        self._gossip_mesh = gossip_mesh

    def set_evolution_engine(self, engine) -> None:
        """Set the evolution engine for major heartbeat."""
        self._evolution_engine = engine

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
        startup_payload = {"reason": "ANIMA boot — first heartbeat"}
        if self._is_restart:
            startup_payload = {
                "reason": f"Evolution restart — {self._restart_reason}",
                "is_restart": True,
            }
        await self._event_queue.put(Event(
            type=EventType.STARTUP,
            payload=startup_payload,
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

        # Alert cooldown: same alert type at most once per 5 minutes
        if diff.has_alerts and (time.time() - getattr(self, '_last_alert_time', 0)) > 300:
            self._last_alert_time = time.time()
            await self._event_queue.put(Event(
                type=EventType.SYSTEM_ALERT,
                payload={"diff": {
                    k: {"old": v.old_value, "new": v.new_value, "delta": v.delta}
                    for k, v in diff.field_diffs.items() if v.significant
                }, "system_state": snapshot},
                priority=EventPriority.HIGH,
                source="heartbeat",
            ))

        # Filter noise BEFORE pushing to queue — don't waste LLM calls on cache/log changes
        if changes:
            real_changes = [
                c for c in changes
                if not any(skip in c.get("path", "") for skip in
                          ("__pycache__", ".pyc", "data/notes/", "data/logs/",
                           "anima.db", ".egg-info", ".pytest_cache",
                           "watchdog_heartbeat", "restart_checkpoint",
                           "evolution_state.json", "sync_watermarks"))
            ]
            if real_changes:
                await self._event_queue.put(Event(
                    type=EventType.FILE_CHANGE,
                    payload={"changes": real_changes},
                    priority=EventPriority.NORMAL,
                    source="heartbeat",
                ))

        self._last_snapshot = snapshot

        # Check scheduled jobs (cron)
        if self._scheduler:
            due_jobs = self._scheduler.get_due_jobs()
            for job in due_jobs:
                await self._event_queue.put(Event(
                    type=EventType.SCHEDULED_TASK,
                    payload={"job_id": job.id, "job_name": job.name, "prompt": job.prompt},
                    priority=EventPriority.NORMAL,
                    source="scheduler",
                ))
                log.info("Scheduled job fired: %s (%s)", job.name, job.cron_expr)

        # Update gossip mesh state vector with latest metrics
        if self._gossip_mesh:
            gs = self._gossip_mesh._local_state
            gs.current_load = snapshot.get("cpu_percent", 0) / 100.0
            gs.emotion = self._emotion.to_dict()

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
        """Alive confirmation — log periodically + update watchdog heartbeat."""
        # Update watchdog heartbeat file every tick (15s)
        from anima.watchdog import _update_heartbeat
        _update_heartbeat()

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
        """Always think on LLM heartbeat — cost is acceptable for proactive behavior."""
        return True

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
        """Major heartbeat — triggers evolution via the six-layer pipeline.

        Uses the new evolution engine (anima/evolution/engine.py):
          Proposal → Consensus → Implement → Test → Review → Deploy
        """
        await asyncio.sleep(self._major_interval)
        while self._running:
            try:
                if not self._evolution_engine:
                    await asyncio.sleep(self._major_interval)
                    continue

                status = self._evolution_engine.get_status()
                if status["running"]:
                    log.info("Evolution still running, skipping")
                    await asyncio.sleep(self._major_interval)
                    continue

                if status["cooldown_remaining"] > 0:
                    log.info("Evolution in cooldown (%ds)", status["cooldown_remaining"])
                    await asyncio.sleep(self._major_interval)
                    continue

                # Generate a proposal via LLM self-thinking
                log.info("Major heartbeat — pushing evolution thinking event")
                await self._event_queue.put(Event(
                    type=EventType.SELF_THINKING,
                    payload={
                        "tick_count": self._tick_count,
                        "evolution": True,
                        "evolution_prompt": self._build_evolution_prompt(),
                    },
                    priority=EventPriority.LOW,
                    source="evolution",
                ))

            except asyncio.CancelledError:
                return
            except Exception as e:
                log.error("Major heartbeat/evolution error: %s", e)
            await asyncio.sleep(self._major_interval)

    def _build_evolution_prompt(self) -> str:
        """Build the evolution proposal prompt with context from experience memory."""
        from anima.core.evolution import EvolutionState
        evo_state = EvolutionState()
        history = evo_state.recent_history_text()

        # Add lessons from evolution memory
        memory = self._evolution_engine.memory if self._evolution_engine else None
        anti_patterns = memory.get_anti_patterns_text() if memory else ""
        goal = memory.get_next_goal() if memory else None
        goal_text = f"\nCURRENT GOAL: {goal['title']} (progress: {goal['progress']:.0%})" if goal else ""

        return f"""[EVOLUTION CYCLE]

You are Eva. Analyze your own codebase and propose ONE concrete improvement.

Output a structured proposal in this EXACT format:
```
TYPE: bugfix | feature | refactor | optimization
TITLE: (one line)
PROBLEM: (what's wrong or missing)
SOLUTION: (how to fix it)
FILES: file1.py, file2.py
RISK: low | medium | high
COMPLEXITY: trivial | small | medium | large
```

Then IMPLEMENT it: read files, make changes, run tests, commit.
{goal_text}
{anti_patterns}

Recent evolution history:
{history}

RULES:
- Actually DO it with tools, don't just describe
- Run tests after changes: `shell(command="python -m pytest tests/ --tb=short -q")`
- If tests pass, commit: `shell(command="git add -A && git commit -m \\"Evolution: [title]\\" && git push origin private")`
- If tests fail, fix or revert
- Do NOT modify .env, local/env.yaml, or evolution engine core
"""
