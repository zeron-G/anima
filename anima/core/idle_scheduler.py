"""Idle Scheduler — unified idle detection + off-peak task scheduling.

Combines three signals (user activity, system load, queue depth) into
a single idle_score (0.0-1.0), then dispatches background tasks from a
priority pool when resources are available.

Tightly integrated with the evolution engine: evolution frequency and
complexity scale with idle_score.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any

from anima.config import get
from anima.models.event import Event, EventType, EventPriority
from anima.utils.logging import get_logger

if TYPE_CHECKING:
    from anima.core.event_queue import EventQueue
    from anima.perception.user_activity import UserActivityDetector

log = get_logger("idle_scheduler")


# ── Module-level memory decay wiring ──
# Set from main.py so idle tasks can trigger memory consolidation
# without idle_scheduler importing MemoryDecay directly.

_memory_decay = None
_llm_router = None
_memory_store = None


def set_memory_decay(decay, llm_router, store=None):
    """Wire MemoryDecay + LLMRouter + MemoryStore for deep memory consolidation tasks."""
    global _memory_decay, _llm_router, _memory_store
    _memory_decay = decay
    _llm_router = llm_router
    _memory_store = store


# ── Idle score helpers ──

def compute_system_idle_score(snapshot: dict) -> float:
    """System load dimension (0.0-1.0)."""
    cpu = snapshot.get("cpu_percent", 50)
    mem = snapshot.get("memory_percent", 50)
    if cpu > 80 or mem > 90:
        return 0.0
    cpu_idle = max(0, 1.0 - cpu / 100.0)
    mem_idle = max(0, 1.0 - mem / 100.0)
    return round(0.7 * cpu_idle + 0.3 * mem_idle, 3)


def compute_queue_idle_score(event_queue: EventQueue) -> float:
    """Event queue depth dimension (0.0-1.0)."""
    pending = event_queue.qsize()
    if pending == 0:
        return 1.0
    elif pending <= 2:
        return 0.5
    elif pending <= 5:
        return 0.2
    return 0.0


# ── Idle Detector ──

class IdleDetector:
    """Three-signal fusion idle detector with EMA smoothing."""

    def __init__(
        self,
        user_activity: UserActivityDetector,
        event_queue: EventQueue,
        weights: dict | None = None,
    ):
        self._user_activity = user_activity
        self._event_queue = event_queue
        w = weights or {}
        self._w_user = w.get("user_activity", 0.5)
        self._w_system = w.get("system_load", 0.3)
        self._w_queue = w.get("queue_depth", 0.2)
        self._idle_score: float = 0.0
        self._idle_history: list[float] = []
        self._alpha = 0.3  # EMA smoothing factor

    def update(self, system_snapshot: dict) -> float:
        """Called every script heartbeat tick. Returns smoothed idle_score."""
        user_idle = self._user_activity.compute_user_idle_score()
        system_idle = compute_system_idle_score(system_snapshot)
        queue_idle = compute_queue_idle_score(self._event_queue)

        raw = (
            self._w_user * user_idle
            + self._w_system * system_idle
            + self._w_queue * queue_idle
        )
        self._idle_score = self._alpha * raw + (1 - self._alpha) * self._idle_score
        self._idle_history.append(self._idle_score)
        if len(self._idle_history) > 40:
            self._idle_history.pop(0)
        return self._idle_score

    @property
    def score(self) -> float:
        return round(self._idle_score, 3)

    @property
    def trend(self) -> str:
        if len(self._idle_history) < 5:
            return "stable"
        recent = self._idle_history[-5:]
        delta = recent[-1] - recent[0]
        if delta > 0.1:
            return "rising"
        elif delta < -0.1:
            return "falling"
        return "stable"

    @property
    def level(self) -> str:
        s = self._idle_score
        if s < 0.3:
            return "busy"
        elif s < 0.6:
            return "light"
        elif s < 0.8:
            return "moderate"
        return "deep"


# ── Task definitions ──

class TaskWeight(IntEnum):
    LIGHT = 1   # No LLM, local-only
    MEDIUM = 2  # May use Tier2 LLM
    HEAVY = 3   # LLM-intensive / long-running


@dataclass
class IdleTask:
    id: str
    name: str
    description: str
    weight: TaskWeight
    min_idle_level: str          # "light" | "moderate" | "deep"
    cooldown_s: int = 600
    max_duration_s: int = 300
    last_run: float = 0.0
    priority: int = 5           # 1-10, higher = more important
    handler: str = ""
    enabled: bool = True


# ── Pre-defined task pool ──
# Integrates with evolution: evolution tasks are part of the idle pool.

def _default_task_pool() -> list[IdleTask]:
    return [
        # ── LIGHT (idle >= 0.3) ──
        IdleTask(
            id="env_incremental_scan",
            name="环境增量扫描",
            description="检测已扫描目录的文件变化，更新 env_catalog",
            weight=TaskWeight.LIGHT, min_idle_level="light",
            cooldown_s=300, max_duration_s=60, priority=8,
            handler="env_scanner.incremental",
        ),
        IdleTask(
            id="memory_consolidation",
            name="记忆整理",
            description="Review and consolidate working memory — archive low-importance items",
            weight=TaskWeight.LIGHT, min_idle_level="light",
            cooldown_s=1800, max_duration_s=30, priority=5,
            handler="memory.consolidate",
        ),
        IdleTask(
            id="log_cleanup",
            name="日志清理",
            description="检查日志文件大小和过期笔记，必要时清理",
            weight=TaskWeight.LIGHT, min_idle_level="light",
            cooldown_s=3600, max_duration_s=15, priority=3,
            handler="maintenance.log_check",
        ),
        IdleTask(
            id="note_archive",
            name="笔记归档",
            description="归档过多的 File_Changes_Detected 笔记",
            weight=TaskWeight.LIGHT, min_idle_level="light",
            cooldown_s=3600, max_duration_s=15, priority=4,
            handler="maintenance.note_archive",
        ),
        # ── MEDIUM (idle >= 0.6) — evolution proposals start here ──
        IdleTask(
            id="env_deep_scan_layer2",
            name="环境深度扫描（高价值区）",
            description="扫描项目目录、桌面、文档等高价值区域的详细内容",
            weight=TaskWeight.MEDIUM, min_idle_level="moderate",
            cooldown_s=1800, max_duration_s=300, priority=7,
            handler="env_scanner.layer2",
        ),
        IdleTask(
            id="evolution_proposal",
            name="自进化提案（中量）",
            description="分析代码库，提出一个 low-risk 改进方案（fix/optimization）",
            weight=TaskWeight.MEDIUM, min_idle_level="moderate",
            cooldown_s=2400, max_duration_s=600, priority=6,
            handler="evolution.propose_light",
        ),
        IdleTask(
            id="tool_audit",
            name="工具使用审计",
            description="分析日志中工具调用失败模式，找出改进点",
            weight=TaskWeight.MEDIUM, min_idle_level="moderate",
            cooldown_s=7200, max_duration_s=120, priority=4,
            handler="audit.tools",
        ),
        IdleTask(
            id="network_health",
            name="网络健康检查",
            description="深度检查分布式节点状态、同步延迟、任务成功率",
            weight=TaskWeight.MEDIUM, min_idle_level="moderate",
            cooldown_s=3600, max_duration_s=60, priority=5,
            handler="network.health",
        ),
        # ── HEAVY (idle >= 0.8) — full evolution + deep scan ──
        IdleTask(
            id="memory_deep_consolidation",
            name="深度记忆整合",
            description="Run MemoryDecay.consolidate() — merge similar memories, prune stale entries, compress episodic chains",
            weight=TaskWeight.HEAVY, min_idle_level="deep",
            cooldown_s=3600, max_duration_s=600, priority=7,
            handler="memory.deep_consolidation",
        ),
        IdleTask(
            id="env_deep_scan_layer3",
            name="环境全盘扫描（第三层）",
            description="逐步扫描不常用目录，建立全盘认知",
            weight=TaskWeight.HEAVY, min_idle_level="deep",
            cooldown_s=3600, max_duration_s=1800, priority=6,
            handler="env_scanner.layer3",
        ),
        IdleTask(
            id="env_content_summarize",
            name="关键文件 LLM 摘要",
            description="对重要文件（README、配置、入口）用 LLM 生成一句话摘要",
            weight=TaskWeight.HEAVY, min_idle_level="deep",
            cooldown_s=7200, max_duration_s=600, priority=5,
            handler="env_scanner.summarize",
        ),
        IdleTask(
            id="full_evolution_cycle",
            name="完整进化循环",
            description="执行完整六层进化流水线 — 可尝试 feature/architecture 级别改进",
            weight=TaskWeight.HEAVY, min_idle_level="deep",
            cooldown_s=3600, max_duration_s=1800, priority=8,
            handler="evolution.full_cycle",
        ),
        IdleTask(
            id="distributed_assist",
            name="分布式任务协助",
            description="主动认领其他空闲节点的待处理任务",
            weight=TaskWeight.HEAVY, min_idle_level="deep",
            cooldown_s=600, max_duration_s=300, priority=7,
            handler="network.assist_peers",
        ),
    ]


# ── Idle Scheduler (core) ──

_LEVEL_ORDER = ["light", "moderate", "deep"]
_LEVEL_THRESHOLDS = {"busy": 0.0, "light": 0.3, "moderate": 0.6, "deep": 0.8}


class IdleScheduler:
    """Central idle-resource scheduler.

    Called every script heartbeat tick. Computes idle_score, then
    decides whether to dispatch background tasks.

    Evolution integration:
    - BUSY: evolution heartbeat suppressed entirely
    - LIGHT: only lightweight self-thinking tasks
    - MODERATE: evolution proposals (low-risk) triggered here
    - DEEP: full evolution cycles + deep environment scan
    """

    LEVEL_BUDGET: dict[str, float] = {
        "busy": 0.0,
        "light": 0.5,
        "moderate": 1.0,
        "deep": 2.0,
    }

    def __init__(
        self,
        idle_detector: IdleDetector,
        event_queue: EventQueue,
        config: dict | None = None,
    ):
        cfg = config or {}
        self._detector = idle_detector
        self._event_queue = event_queue
        self._task_pool: list[IdleTask] = _default_task_pool()
        self._running_task_ids: set[str] = set()
        self._max_concurrent = cfg.get("max_concurrent_tasks", 2)
        self._hourly_spend: float = 0.0
        self._hour_start: float = time.time()
        self._enabled = cfg.get("enabled", True)
        self._dispatch_count: int = 0
        # Evolution engine reference (set externally)
        self._evolution_engine = None

    def set_evolution_engine(self, engine) -> None:
        self._evolution_engine = engine

    # ── Properties ──

    @property
    def level(self) -> str:
        return self._detector.level

    @property
    def score(self) -> float:
        return self._detector.score

    # ── Heartbeat frequency helpers (used by HeartbeatEngine) ──

    def get_llm_heartbeat_interval(self, base_interval: float) -> float:
        """Dynamic LLM heartbeat interval based on idle level.

        BUSY   → skip (return very large number)
        LIGHT  → base interval (default 600s)
        MODERATE → half
        DEEP   → 180s minimum
        """
        lvl = self.level
        if lvl == "busy":
            return 99999  # effectively skip
        if lvl == "moderate":
            return max(180, base_interval * 0.5)
        if lvl == "deep":
            return 180
        return base_interval  # light

    def should_trigger_evolution(self) -> bool:
        """Whether the major heartbeat should trigger evolution.

        Only in MODERATE or DEEP idle. Also checks evolution engine
        cooldown and running state.
        """
        if self.level in ("busy", "light"):
            return False
        if not self._evolution_engine:
            return True  # let the heartbeat decide
        status = self._evolution_engine.get_status()
        if status.get("running"):
            return False
        if status.get("cooldown_remaining", 0) > 0:
            return False
        return True

    def get_major_heartbeat_interval(self, base_interval: float) -> float:
        """Dynamic major heartbeat (evolution) interval.

        BUSY/LIGHT → skip
        MODERATE   → base interval
        DEEP       → halved (min 900s)
        """
        lvl = self.level
        if lvl in ("busy", "light"):
            return 99999
        if lvl == "deep":
            return max(900, base_interval * 0.5)
        return base_interval

    # ── Main tick ──

    async def tick(self, system_snapshot: dict) -> None:
        """Called every script heartbeat. Updates idle score and dispatches tasks."""
        if not self._enabled:
            return

        # Update idle detection
        self._detector.update(system_snapshot)
        lvl = self.level

        if lvl == "busy":
            return

        # Budget check
        self._refresh_hourly_budget()
        budget = self.LEVEL_BUDGET.get(lvl, 0)
        if budget > 0 and self._hourly_spend >= budget:
            return

        # Clean finished tasks
        self._running_task_ids = {
            tid for tid in self._running_task_ids
            # We don't track asyncio.Task objects here — just IDs.
            # Cleanup is implicit: tasks are re-eligible after cooldown.
        }

        # Concurrency check
        if len(self._running_task_ids) >= self._max_concurrent:
            return

        # Select and dispatch
        task = self._select_next_task(lvl)
        if task:
            await self._dispatch_task(task)

    def _select_next_task(self, current_level: str) -> IdleTask | None:
        now = time.time()
        current_idx = _LEVEL_ORDER.index(current_level) if current_level in _LEVEL_ORDER else -1

        candidates = []
        for task in self._task_pool:
            if not task.enabled:
                continue
            task_idx = _LEVEL_ORDER.index(task.min_idle_level) if task.min_idle_level in _LEVEL_ORDER else 99
            if current_idx < task_idx:
                continue
            if (now - task.last_run) < task.cooldown_s:
                continue
            if task.id in self._running_task_ids:
                continue
            # Skip evolution tasks if engine is busy/cooldown
            if task.handler.startswith("evolution.") and self._evolution_engine:
                status = self._evolution_engine.get_status()
                if status.get("running") or status.get("cooldown_remaining", 0) > 0:
                    continue
            candidates.append(task)

        if not candidates:
            return None
        candidates.sort(key=lambda t: (-t.priority, t.last_run))
        return candidates[0]

    async def _dispatch_task(self, task: IdleTask) -> None:
        task.last_run = time.time()
        self._running_task_ids.add(task.id)
        self._dispatch_count += 1

        log.info(
            "Dispatching idle task: %s (level=%s, score=%.2f, handler=%s)",
            task.name, self.level, self.score, task.handler,
        )

        # Direct execution for memory consolidation (no LLM event needed)
        if task.handler == "memory.deep_consolidation":
            asyncio.ensure_future(self._run_memory_consolidation(task.id))
            return

        await self._event_queue.put(Event(
            type=EventType.IDLE_TASK,
            payload={
                "task_id": task.id,
                "task_name": task.name,
                "description": task.description,
                "handler": task.handler,
                "max_duration_s": task.max_duration_s,
                "weight": task.weight.name,
                "idle_score": self.score,
                "idle_level": self.level,
            },
            priority=EventPriority.LOW,
            source="idle_scheduler",
        ))

    async def _run_memory_consolidation(self, task_id: str) -> None:
        """Execute deep memory consolidation via MemoryDecay.consolidate().

        Uses the module-level _memory_decay, _llm_router, and _memory_store
        set from main.py via set_memory_decay().
        """
        try:
            if not _memory_decay or not _llm_router or not _memory_store:
                log.debug("Memory consolidation skipped — MemoryDecay/LLMRouter/MemoryStore not wired")
                return
            budget_ok = _llm_router.check_budget(estimated_cost=0.005)
            if not budget_ok:
                log.debug("Memory consolidation skipped — LLM budget exceeded")
                return
            log.info("Running deep memory consolidation (MemoryDecay.consolidate)")
            await _memory_decay.consolidate(_memory_store, _llm_router, budget_ok)
            log.info("Deep memory consolidation completed")
        except Exception as e:
            log.warning("Deep memory consolidation failed: %s", e)
        finally:
            self.mark_task_done(task_id)

    def mark_task_done(self, task_id: str) -> None:
        """Called when an idle task finishes (from cognitive loop)."""
        self._running_task_ids.discard(task_id)

    def _refresh_hourly_budget(self) -> None:
        now = time.time()
        if now - self._hour_start >= 3600:
            self._hourly_spend = 0.0
            self._hour_start = now

    def record_spend(self, cost_usd: float) -> None:
        """Record API cost from an idle task."""
        self._hourly_spend += cost_usd

    def register_task(self, task: IdleTask) -> None:
        existing_ids = {t.id for t in self._task_pool}
        if task.id not in existing_ids:
            self._task_pool.append(task)

    def get_status(self) -> dict:
        return {
            "enabled": self._enabled,
            "idle_score": self.score,
            "idle_level": self.level,
            "idle_trend": self._detector.trend,
            "running_tasks": list(self._running_task_ids),
            "hourly_spend": round(self._hourly_spend, 4),
            "hourly_budget": self.LEVEL_BUDGET.get(self.level, 0),
            "pool_size": len(self._task_pool),
            "total_dispatched": self._dispatch_count,
        }
