# Idle Scheduler（空闲资源调度器）设计文档

> **版本**: v1.0
> **日期**: 2026-03-16
> **状态**: 设计阶段
> **作者**: 主人 + Claude
> **关联模块**: `heartbeat.py`, `event_router.py`, `gossip.py`, `node.py`, `system_monitor.py`, `scheduler.py`

---

## 1. 动机与目标

### 1.1 现状问题

ANIMA 当前的心跳系统是**固定频率**的：

| 心跳层级 | 间隔 | 问题 |
|---------|------|------|
| 脚本心跳 | 15s | 始终运行，无空闲感知 |
| LLM 心跳 | 180s (代码默认) / 600s (配置) | 不管用户在不在，都固定频率触发 SELF_THINKING |
| 大心跳 | 3600s (代码默认) / 1800s (配置) | 固定触发进化，不考虑资源占用 |

**结果**：
- 用户高强度工作时，Eva 的后台任务抢占 CPU 和 API 额度
- 用户睡觉时，大量空闲算力白白浪费
- Eva 对自己运行环境的感知极浅——只有 CPU/内存/磁盘百分比和项目目录的文件变更
- 不知道电脑装了什么软件、项目结构是什么、哪些文件重要

### 1.2 设计目标

构建一个**统一的空闲资源调度器**，将以下需求合并为一个机制：

1. **环境深度探索** — 分层扫描整台电脑，建立细粒度环境认知
2. **错峰机制** — 根据空闲程度动态调整后台任务强度
3. **分布式任务稀释** — idle_score 纳入 Gossip 广播，跨节点调度

**核心理念**：让 ANIMA 在空闲时产生价值，而不是空转。

---

## 2. 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                    Idle Scheduler 架构                        │
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │ IdleDetector  │───▶│ IdleScheduler │───▶│ BackgroundTask │ │
│  │ (空闲检测器)   │    │ (调度核心)     │    │  Pool (任务池)  │ │
│  └──────┬───────┘    └──────┬───────┘    └───────────────┘  │
│         │                   │                                │
│  ┌──────▼───────┐    ┌──────▼───────┐    ┌───────────────┐  │
│  │ 三路信号融合    │    │ 阈值策略引擎  │    │  EnvScanner    │ │
│  │ - 用户活动     │    │ - 四级阈值    │    │  (环境扫描器)   │ │
│  │ - 系统负载     │    │ - 动态调频    │    │  - 三层扫描     │ │
│  │ - 队列深度     │    │ - 任务准入    │    │  - 增量检测     │ │
│  └──────────────┘    └──────────────┘    │  - 内容摘要     │ │
│                                          └───────────────┘  │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              Gossip 广播 idle_score                    │   │
│  │  NodeState.idle_score → 跨节点任务委派决策              │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**新增文件**：

| 文件 | 职责 |
|------|------|
| `anima/core/idle_scheduler.py` | 空闲调度器核心：空闲检测 + 任务分发 + 调频逻辑 |
| `anima/perception/env_scanner.py` | 环境深度扫描器：三层扫描 + 增量检测 + 内容摘要 |
| `anima/perception/user_activity.py` | 用户活动检测：键鼠活跃度、最后交互时间 |

**修改文件**：

| 文件 | 变更 |
|------|------|
| `anima/core/heartbeat.py` | 脚本心跳集成 IdleDetector，计算 idle_score |
| `anima/core/event_router.py` | TASK_POOL 扩展，新增 IDLE_TASK 事件类型 |
| `anima/models/event.py` | 新增 `EventType.IDLE_TASK` |
| `anima/network/node.py` | `NodeState` 新增 `idle_score` 字段 |
| `anima/network/gossip.py` | 广播 idle_score，任务委派考虑 idle_score |
| `anima/network/session_router.py` | 节点选择策略加入 idle_score 权重 |
| `anima/perception/system_monitor.py` | 扩展采集：磁盘 IO、GPU 使用率（可选） |
| `anima/memory/store.py` | 新增 env_catalog 表 |
| `config/default.yaml` | 新增 idle_scheduler 配置节 |

---

## 3. 空闲检测（Idle Detection）

### 3.1 三路信号融合

空闲度不是单一指标，而是三个维度的综合评分：

```python
idle_score = w_user * user_idle + w_system * system_idle + w_queue * queue_idle
```

#### 信号 A：用户活动（权重 0.5）

用户是否在用电脑是最重要的信号。

```python
# anima/perception/user_activity.py

import ctypes
import time
import platform

class UserActivityDetector:
    """检测用户键鼠活动 — Windows 通过 GetLastInputInfo, Linux 通过 /proc。"""

    def __init__(self):
        self._last_message_time: float = 0.0  # 最后一次用户消息时间
        self._is_windows = platform.system() == "Windows"

    def get_system_idle_seconds(self) -> float:
        """获取系统级键鼠空闲时长（秒）。"""
        if self._is_windows:
            return self._win32_idle_seconds()
        return self._linux_idle_seconds()

    def _win32_idle_seconds(self) -> float:
        """Windows: GetLastInputInfo API。"""
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("dwTime", ctypes.c_uint),
            ]
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            return millis / 1000.0
        return 0.0

    def _linux_idle_seconds(self) -> float:
        """Linux: 读取 /proc/interrupts 或 xprintidle。"""
        try:
            import subprocess
            result = subprocess.run(
                ["xprintidle"], capture_output=True, text=True, timeout=2
            )
            return int(result.stdout.strip()) / 1000.0
        except Exception:
            return 0.0

    def record_user_message(self) -> None:
        """记录用户发送消息的时间。"""
        self._last_message_time = time.time()

    def get_message_idle_seconds(self) -> float:
        """距离最后一次用户消息的秒数。"""
        if self._last_message_time == 0:
            return float("inf")
        return time.time() - self._last_message_time

    def compute_user_idle_score(self) -> float:
        """计算用户活动维度的空闲分 (0.0-1.0)。

        综合键鼠空闲和消息空闲。
        - 键鼠活跃 (< 60s) → 0.0
        - 短暂离开 (1-5min) → 0.2-0.5
        - 长时间无操作 (5-30min) → 0.5-0.8
        - 疑似睡觉 (> 30min) → 0.8-1.0
        """
        sys_idle = self.get_system_idle_seconds()
        msg_idle = self.get_message_idle_seconds()

        # 取两个信号中更保守的（更短的空闲时间）
        # 但如果消息空闲很久，键鼠空闲短可能只是屏保唤醒
        effective_idle = min(sys_idle, msg_idle)

        # 分段映射
        if effective_idle < 60:
            return 0.0
        elif effective_idle < 300:      # 1-5 min
            return 0.1 + 0.4 * ((effective_idle - 60) / 240)
        elif effective_idle < 1800:     # 5-30 min
            return 0.5 + 0.3 * ((effective_idle - 300) / 1500)
        else:                           # > 30 min
            return min(1.0, 0.8 + 0.2 * ((effective_idle - 1800) / 3600))
```

#### 信号 B：系统负载（权重 0.3）

```python
def compute_system_idle_score(snapshot: dict) -> float:
    """计算系统负载维度的空闲分 (0.0-1.0)。

    Args:
        snapshot: 来自 sample_system_state() 的系统快照
    """
    cpu = snapshot.get("cpu_percent", 50)
    mem = snapshot.get("memory_percent", 50)
    # 可选：disk_io_percent（需要扩展 system_monitor）

    # CPU 权重更大（CPU 密集任务影响更直接）
    cpu_idle = max(0, 1.0 - cpu / 100.0)
    mem_idle = max(0, 1.0 - mem / 100.0)

    # 加权：CPU 70%, 内存 30%
    system_idle = 0.7 * cpu_idle + 0.3 * mem_idle

    # 硬阈值：如果 CPU > 80% 或内存 > 90%，直接压到 0
    if cpu > 80 or mem > 90:
        return 0.0

    return round(system_idle, 3)
```

#### 信号 C：队列深度（权重 0.2）

```python
def compute_queue_idle_score(event_queue: EventQueue) -> float:
    """计算事件队列维度的空闲分 (0.0-1.0)。

    队列有积压 → 不应该做后台任务。
    队列为空 → 系统空闲。
    """
    pending = event_queue.qsize()
    if pending == 0:
        return 1.0
    elif pending <= 2:
        return 0.5
    elif pending <= 5:
        return 0.2
    else:
        return 0.0
```

### 3.2 idle_score 综合计算

```python
# anima/core/idle_scheduler.py (部分)

class IdleDetector:
    """三路信号融合的空闲检测器。"""

    # 权重配置
    W_USER = 0.5
    W_SYSTEM = 0.3
    W_QUEUE = 0.2

    def __init__(self, user_activity: UserActivityDetector, event_queue: EventQueue):
        self._user_activity = user_activity
        self._event_queue = event_queue
        self._idle_score: float = 0.0
        self._idle_history: list[float] = []  # 最近 20 个采样点 (5 分钟历史)

    def update(self, system_snapshot: dict) -> float:
        """每次脚本心跳调用，更新 idle_score。"""
        user_idle = self._user_activity.compute_user_idle_score()
        system_idle = compute_system_idle_score(system_snapshot)
        queue_idle = compute_queue_idle_score(self._event_queue)

        raw_score = (
            self.W_USER * user_idle +
            self.W_SYSTEM * system_idle +
            self.W_QUEUE * queue_idle
        )

        # 平滑处理：EMA (指数移动平均)，避免瞬时波动
        alpha = 0.3  # 平滑系数，越小越平滑
        self._idle_score = alpha * raw_score + (1 - alpha) * self._idle_score

        # 记录历史
        self._idle_history.append(self._idle_score)
        if len(self._idle_history) > 20:
            self._idle_history.pop(0)

        return self._idle_score

    @property
    def score(self) -> float:
        return round(self._idle_score, 3)

    @property
    def trend(self) -> str:
        """空闲趋势：rising (越来越闲), falling (越来越忙), stable。"""
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
        """当前空闲级别名称。"""
        s = self._idle_score
        if s < 0.3:
            return "busy"
        elif s < 0.6:
            return "light"
        elif s < 0.8:
            return "moderate"
        else:
            return "deep"
```

---

## 4. 四级调度阈值

根据 idle_score 划分四个运行级别，每个级别允许不同强度的后台任务：

| idle_score | 级别 | 允许的任务 | LLM 心跳频率 | API 额度限制 |
|-----------|------|-----------|-------------|-------------|
| `< 0.3` | **BUSY** (忙碌) | 仅脚本心跳，完全不触发后台任务 | 暂停 | 0 |
| `0.3 - 0.6` | **LIGHT** (轻度空闲) | 轻量任务：环境增量扫描、记忆整理、日志清理 | 10min | $0.5/h |
| `0.6 - 0.8` | **MODERATE** (中度空闲) | 中量任务：自进化提案、网络同步、工具审计 | 5min | $1.0/h |
| `≥ 0.8` | **DEEP** (深度空闲) | 全速运行：深度环境扫描、大规模自进化、帮其他节点分担任务 | 3min | $2.0/h |

### 4.1 心跳动态调频

**关键变更**：LLM 心跳和大心跳不再是固定频率，而是由 idle_score 动态控制。

```python
# 在 HeartbeatEngine._llm_heartbeat_loop() 中的变更逻辑

async def _llm_heartbeat_loop(self) -> None:
    await asyncio.sleep(60)  # 首次延迟不变
    self._consecutive_skips = self._max_consecutive_skips
    while self._running:
        try:
            idle_level = self._idle_scheduler.level if self._idle_scheduler else "light"

            if idle_level == "busy":
                # 忙碌时完全跳过 LLM 心跳
                log.debug("LLM heartbeat skipped — system busy (idle_score=%.2f)",
                         self._idle_scheduler.score if self._idle_scheduler else 0)
                await asyncio.sleep(self._llm_interval)
                continue

            # 根据空闲级别调整间隔
            interval_map = {
                "light": self._llm_interval,         # 配置值 (默认 600s)
                "moderate": self._llm_interval * 0.5, # 减半
                "deep": 180,                           # 最小 3 分钟
            }
            effective_interval = interval_map.get(idle_level, self._llm_interval)

            if self._should_llm_think():
                await self._on_llm_tick()
                self._consecutive_skips = 0
            else:
                self._consecutive_skips += 1

            await asyncio.sleep(effective_interval)

        except asyncio.CancelledError:
            return
        except Exception as e:
            log.error("LLM heartbeat error: %s", e)
            await asyncio.sleep(self._llm_interval)
```

### 4.2 大心跳（进化）调频

```python
# 在 HeartbeatEngine._major_heartbeat_loop() 中的变更逻辑

async def _major_heartbeat_loop(self) -> None:
    await asyncio.sleep(self._major_interval)
    while self._running:
        try:
            idle_level = self._idle_scheduler.level if self._idle_scheduler else "moderate"

            # 进化只在 MODERATE 或 DEEP 空闲时触发
            if idle_level in ("busy", "light"):
                log.debug("Evolution skipped — insufficient idle (level=%s)", idle_level)
                await asyncio.sleep(self._major_interval)
                continue

            # ... 原有的 evolution_engine 检查逻辑不变 ...

            # DEEP 空闲时缩短进化间隔
            effective_interval = self._major_interval
            if idle_level == "deep":
                effective_interval = max(900, self._major_interval // 2)  # 最快 15 分钟

            await asyncio.sleep(effective_interval)

        except asyncio.CancelledError:
            return
        except Exception as e:
            log.error("Major heartbeat error: %s", e)
            await asyncio.sleep(self._major_interval)
```

---

## 5. 空闲任务池（Idle Task Pool）

### 5.1 任务定义

将现有 `TASK_POOL` 扩展为分级任务池：

```python
# anima/core/idle_scheduler.py

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Awaitable

class TaskWeight(IntEnum):
    """任务重量级别 — 决定最低空闲要求。"""
    LIGHT = 1      # 轻量：无 LLM 调用，纯本地操作
    MEDIUM = 2     # 中量：可能调用 LLM (Tier2)
    HEAVY = 3      # 重量：LLM 密集 / 长时间运行

@dataclass
class IdleTask:
    """空闲任务定义。"""
    id: str                          # 唯一标识
    name: str                        # 人类可读名称
    description: str                 # 任务描述
    weight: TaskWeight               # 任务重量
    min_idle_level: str              # 最低空闲级别: "light" | "moderate" | "deep"
    cooldown_s: int = 600            # 冷却时间（秒）
    max_duration_s: int = 300        # 最大执行时间（秒）
    last_run: float = 0.0           # 上次执行时间
    priority: int = 5               # 优先级 (1-10, 10 最高)
    handler: str = ""               # 处理器标识（对应具体执行逻辑）
    enabled: bool = True


# 预定义任务池
IDLE_TASK_POOL: list[IdleTask] = [
    # ── LIGHT 级别任务 (idle_score >= 0.3) ──
    IdleTask(
        id="env_incremental_scan",
        name="环境增量扫描",
        description="检测已扫描目录的文件变化，更新 env_catalog",
        weight=TaskWeight.LIGHT,
        min_idle_level="light",
        cooldown_s=300,       # 5 分钟冷却
        max_duration_s=60,
        priority=8,
        handler="env_scanner.incremental_scan",
    ),
    IdleTask(
        id="memory_consolidation",
        name="记忆整理",
        description="整理工作记忆中的旧条目，归档到长期记忆",
        weight=TaskWeight.LIGHT,
        min_idle_level="light",
        cooldown_s=1800,      # 30 分钟冷却
        max_duration_s=30,
        priority=5,
        handler="memory.consolidate",
    ),
    IdleTask(
        id="log_rotation_check",
        name="日志清理检查",
        description="检查日志文件大小，必要时轮转",
        weight=TaskWeight.LIGHT,
        min_idle_level="light",
        cooldown_s=3600,      # 1 小时冷却
        max_duration_s=10,
        priority=3,
        handler="maintenance.log_check",
    ),
    IdleTask(
        id="note_archive",
        name="笔记归档",
        description="归档过多的文件变更笔记和旧笔记",
        weight=TaskWeight.LIGHT,
        min_idle_level="light",
        cooldown_s=3600,
        max_duration_s=15,
        priority=4,
        handler="maintenance.note_archive",
    ),

    # ── MEDIUM 级别任务 (idle_score >= 0.6) ──
    IdleTask(
        id="env_deep_scan_layer2",
        name="环境深度扫描（第二层）",
        description="扫描高价值目录（项目、桌面、文档）的详细内容",
        weight=TaskWeight.MEDIUM,
        min_idle_level="moderate",
        cooldown_s=1800,
        max_duration_s=300,
        priority=7,
        handler="env_scanner.deep_scan_layer2",
    ),
    IdleTask(
        id="self_evolution_proposal",
        name="自进化提案",
        description="分析代码库，提出一个改进方案",
        weight=TaskWeight.MEDIUM,
        min_idle_level="moderate",
        cooldown_s=3600,      # 1 小时冷却
        max_duration_s=600,
        priority=6,
        handler="evolution.propose",
    ),
    IdleTask(
        id="tool_audit",
        name="工具使用审计",
        description="分析日志中的工具调用失败模式，找出改进点",
        weight=TaskWeight.MEDIUM,
        min_idle_level="moderate",
        cooldown_s=7200,      # 2 小时冷却
        max_duration_s=120,
        priority=4,
        handler="audit.tools",
    ),
    IdleTask(
        id="network_health_check",
        name="网络健康检查",
        description="深度检查分布式节点状态、同步延迟、任务成功率",
        weight=TaskWeight.MEDIUM,
        min_idle_level="moderate",
        cooldown_s=3600,
        max_duration_s=60,
        priority=5,
        handler="network.health_check",
    ),

    # ── HEAVY 级别任务 (idle_score >= 0.8) ──
    IdleTask(
        id="env_deep_scan_layer3",
        name="环境深度扫描（第三层）",
        description="逐步扫描不常用目录，建立全盘认知",
        weight=TaskWeight.HEAVY,
        min_idle_level="deep",
        cooldown_s=3600,
        max_duration_s=1800,   # 最长 30 分钟
        priority=6,
        handler="env_scanner.deep_scan_layer3",
    ),
    IdleTask(
        id="env_content_summarize",
        name="关键文件内容摘要",
        description="对重要文件（README、配置、入口）用 LLM 生成一句话摘要",
        weight=TaskWeight.HEAVY,
        min_idle_level="deep",
        cooldown_s=7200,
        max_duration_s=600,
        priority=5,
        handler="env_scanner.summarize_key_files",
    ),
    IdleTask(
        id="distributed_task_assist",
        name="分布式任务协助",
        description="检查其他节点的待处理任务，主动认领可执行的",
        weight=TaskWeight.HEAVY,
        min_idle_level="deep",
        cooldown_s=600,
        max_duration_s=300,
        priority=8,
        handler="network.assist_peers",
    ),
    IdleTask(
        id="full_evolution_cycle",
        name="完整进化循环",
        description="执行完整的六层进化流水线：提案→共识→实现→测试→审查→部署",
        weight=TaskWeight.HEAVY,
        min_idle_level="deep",
        cooldown_s=7200,      # 2 小时冷却
        max_duration_s=1800,
        priority=7,
        handler="evolution.full_cycle",
    ),
]
```

### 5.2 调度核心

```python
class IdleScheduler:
    """空闲资源调度器 — 根据 idle_score 从任务池取任务并分配资源。"""

    LEVEL_THRESHOLDS = {
        "busy": 0.0,
        "light": 0.3,
        "moderate": 0.6,
        "deep": 0.8,
    }

    # 每个级别的 API 额度上限 (USD/hour)
    LEVEL_BUDGET = {
        "busy": 0.0,
        "light": 0.5,
        "moderate": 1.0,
        "deep": 2.0,
    }

    def __init__(
        self,
        idle_detector: IdleDetector,
        event_queue: EventQueue,
        config: dict,
    ):
        self._detector = idle_detector
        self._event_queue = event_queue
        self._task_pool: list[IdleTask] = list(IDLE_TASK_POOL)
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._max_concurrent_idle_tasks = config.get("max_concurrent_tasks", 2)
        self._hourly_spend: float = 0.0
        self._hour_start: float = time.time()
        self._enabled = config.get("enabled", True)

    @property
    def level(self) -> str:
        return self._detector.level

    @property
    def score(self) -> float:
        return self._detector.score

    async def tick(self) -> None:
        """每次脚本心跳调用 — 检查是否应该启动空闲任务。"""
        if not self._enabled:
            return

        level = self._detector.level
        if level == "busy":
            return

        # 检查预算
        self._refresh_hourly_budget()
        if self._hourly_spend >= self.LEVEL_BUDGET.get(level, 0):
            return

        # 清理已完成的任务
        self._cleanup_finished_tasks()

        # 检查并发限制
        if len(self._running_tasks) >= self._max_concurrent_idle_tasks:
            return

        # 选择下一个要执行的任务
        task = self._select_next_task(level)
        if task:
            await self._dispatch_task(task)

    def _select_next_task(self, current_level: str) -> IdleTask | None:
        """从任务池中选择优先级最高的可执行任务。"""
        now = time.time()
        level_order = ["light", "moderate", "deep"]
        current_level_idx = level_order.index(current_level) if current_level in level_order else -1

        candidates = []
        for task in self._task_pool:
            if not task.enabled:
                continue
            # 检查最低空闲级别
            task_level_idx = level_order.index(task.min_idle_level) if task.min_idle_level in level_order else 99
            if current_level_idx < task_level_idx:
                continue
            # 检查冷却
            if (now - task.last_run) < task.cooldown_s:
                continue
            # 检查是否已在运行
            if task.id in self._running_tasks:
                continue
            candidates.append(task)

        if not candidates:
            return None

        # 按优先级排序，同优先级按冷却后等待时间排序
        candidates.sort(key=lambda t: (-t.priority, t.last_run))
        return candidates[0]

    async def _dispatch_task(self, task: IdleTask) -> None:
        """派发空闲任务到事件队列。"""
        task.last_run = time.time()
        log.info("Dispatching idle task: %s (level=%s, score=%.2f)",
                 task.name, self.level, self.score)

        await self._event_queue.put(Event(
            type=EventType.IDLE_TASK,
            payload={
                "task_id": task.id,
                "task_name": task.name,
                "description": task.description,
                "handler": task.handler,
                "max_duration_s": task.max_duration_s,
                "weight": task.weight.name,
            },
            priority=EventPriority.LOW,
            source="idle_scheduler",
        ))

    def _refresh_hourly_budget(self) -> None:
        """每小时重置 API 花费计数。"""
        now = time.time()
        if now - self._hour_start >= 3600:
            self._hourly_spend = 0.0
            self._hour_start = now

    def _cleanup_finished_tasks(self) -> None:
        """清理已完成的异步任务。"""
        finished = [tid for tid, t in self._running_tasks.items() if t.done()]
        for tid in finished:
            del self._running_tasks[tid]

    def register_task(self, task: IdleTask) -> None:
        """注册自定义空闲任务（供 evolution 等模块动态添加）。"""
        existing = {t.id for t in self._task_pool}
        if task.id not in existing:
            self._task_pool.append(task)

    def get_status(self) -> dict:
        """返回调度器状态（供 Dashboard 和日志使用）。"""
        return {
            "enabled": self._enabled,
            "idle_score": self.score,
            "idle_level": self.level,
            "idle_trend": self._detector.trend,
            "running_tasks": list(self._running_tasks.keys()),
            "hourly_spend": round(self._hourly_spend, 4),
            "hourly_budget": self.LEVEL_BUDGET.get(self.level, 0),
            "pool_size": len(self._task_pool),
        }
```

---

## 6. 环境深度探索（Environment Deep Scan）

### 6.1 三层扫描架构

```
第一层（骨架）          第二层（高价值区）        第三层（全盘）
───────────────      ──────────────────      ──────────────
所有顶级目录结构       项目目录内容             其余所有目录
  C:\Users\          D:\data\code\**         C:\Program Files\
  D:\                桌面\**                 C:\Windows\ (只记结构)
  ...                文档\**                 其他挂载盘\**
                     下载\**
耗时: ~5秒           耗时: ~几分钟             耗时: 数小时到数天
触发: 首次启动        触发: light 空闲         触发: deep 空闲
更新: 每次启动        更新: 每 5 分钟增量       更新: 逐步推进
```

### 6.2 数据库 Schema

```sql
-- 新增表：env_catalog（环境目录）
CREATE TABLE IF NOT EXISTS env_catalog (
    id TEXT PRIMARY KEY,              -- 文件路径的 hash
    path TEXT NOT NULL UNIQUE,        -- 绝对路径
    type TEXT NOT NULL,               -- "file" | "directory"
    size_bytes INTEGER DEFAULT 0,     -- 文件大小
    modified_at REAL DEFAULT 0,       -- 最后修改时间
    scanned_at REAL DEFAULT 0,        -- 最后扫描时间
    scan_layer INTEGER DEFAULT 1,     -- 扫描层级 (1/2/3)
    category TEXT DEFAULT "",         -- 分类: "project", "document", "config",
                                      --       "media", "binary", "system", "other"
    extension TEXT DEFAULT "",        -- 文件扩展名
    summary TEXT DEFAULT "",          -- LLM 生成的一句话摘要（关键文件才有）
    parent_dir TEXT DEFAULT "",       -- 父目录路径
    is_important INTEGER DEFAULT 0,   -- 是否为重要文件 (0/1)
    content_hash TEXT DEFAULT "",     -- 内容 hash（用于增量检测）
    metadata_json TEXT DEFAULT "{}"   -- 额外元数据 (JSON)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_env_path ON env_catalog(path);
CREATE INDEX IF NOT EXISTS idx_env_parent ON env_catalog(parent_dir);
CREATE INDEX IF NOT EXISTS idx_env_category ON env_catalog(category);
CREATE INDEX IF NOT EXISTS idx_env_type ON env_catalog(type);
CREATE INDEX IF NOT EXISTS idx_env_important ON env_catalog(is_important);
CREATE INDEX IF NOT EXISTS idx_env_scanned ON env_catalog(scanned_at);
CREATE INDEX IF NOT EXISTS idx_env_extension ON env_catalog(extension);

-- 扫描进度表
CREATE TABLE IF NOT EXISTS env_scan_progress (
    id TEXT PRIMARY KEY,              -- "layer1" | "layer2" | "layer3"
    status TEXT DEFAULT "pending",    -- "pending" | "in_progress" | "completed"
    total_dirs INTEGER DEFAULT 0,     -- 总目录数
    scanned_dirs INTEGER DEFAULT 0,   -- 已扫描目录数
    total_files INTEGER DEFAULT 0,    -- 总文件数
    last_scanned_path TEXT DEFAULT "", -- 断点续传：上次扫描到的路径
    started_at REAL DEFAULT 0,
    completed_at REAL DEFAULT 0,
    updated_at REAL DEFAULT 0
);
```

### 6.3 扫描器实现

```python
# anima/perception/env_scanner.py

"""环境深度扫描器 — 三层扫描 + 增量检测 + 内容摘要。"""

import hashlib
import os
import time
from pathlib import Path
from typing import Generator

from anima.memory.store import MemoryStore
from anima.utils.logging import get_logger

log = get_logger("env_scanner")

# 跳过的目录（系统/缓存/临时文件）
SKIP_DIRS = {
    "$Recycle.Bin", "System Volume Information", "$WinREAgent",
    "Windows.old", "Recovery", "PerfLogs",
    "__pycache__", "node_modules", ".git", ".svn", ".hg",
    "venv", ".venv", "env", ".env",
    ".tox", ".pytest_cache", ".mypy_cache",
    "dist", "build", "target", ".gradle",
    "AppData\\Local\\Temp", "Temp",
}

# 高价值目录（第二层优先扫描）
HIGH_VALUE_PATTERNS = [
    "Desktop", "桌面",
    "Documents", "文档",
    "Downloads", "下载",
    "code", "projects", "repos", "workspace", "dev",
    "data",
]

# 关键文件名（触发 LLM 摘要）
KEY_FILE_NAMES = {
    "README.md", "readme.md", "README.txt",
    "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "Makefile", "CMakeLists.txt", "Dockerfile", "docker-compose.yml",
    ".env.example", "requirements.txt",
    "config.yaml", "config.yml", "config.json", "config.toml",
    "main.py", "main.go", "main.rs", "index.js", "index.ts",
    "App.tsx", "App.vue", "App.svelte",
    "CLAUDE.md",
}

# 文件分类规则
CATEGORY_MAP = {
    # 代码
    ".py": "code", ".js": "code", ".ts": "code", ".jsx": "code", ".tsx": "code",
    ".go": "code", ".rs": "code", ".java": "code", ".c": "code", ".cpp": "code",
    ".h": "code", ".cs": "code", ".rb": "code", ".php": "code", ".swift": "code",
    ".kt": "code", ".scala": "code", ".lua": "code", ".sh": "code", ".ps1": "code",
    # 配置
    ".yaml": "config", ".yml": "config", ".json": "config", ".toml": "config",
    ".ini": "config", ".cfg": "config", ".conf": "config", ".env": "config",
    ".xml": "config", ".properties": "config",
    # 文档
    ".md": "document", ".txt": "document", ".rst": "document", ".org": "document",
    ".doc": "document", ".docx": "document", ".pdf": "document",
    ".odt": "document", ".rtf": "document",
    # 媒体
    ".jpg": "media", ".jpeg": "media", ".png": "media", ".gif": "media",
    ".svg": "media", ".bmp": "media", ".webp": "media",
    ".mp3": "media", ".wav": "media", ".flac": "media", ".ogg": "media",
    ".mp4": "media", ".avi": "media", ".mkv": "media", ".mov": "media",
    # 数据
    ".csv": "data", ".tsv": "data", ".parquet": "data", ".arrow": "data",
    ".db": "data", ".sqlite": "data", ".sql": "data",
    # 二进制/可执行
    ".exe": "binary", ".dll": "binary", ".so": "binary", ".dylib": "binary",
    ".msi": "binary", ".AppImage": "binary",
}


class EnvScanner:
    """分层环境扫描器。

    Layer 1: 骨架扫描 — 所有驱动器的顶级目录结构 (depth=2)
    Layer 2: 高价值扫描 — 项目/桌面/文档的完整文件树
    Layer 3: 全盘扫描 — 逐步扩展到其他目录
    """

    def __init__(self, db: MemoryStore):
        self._db = db
        self._scan_batch_size = 500  # 每批处理文件数（避免长时间阻塞）
        self._cancelled = False

    def cancel(self) -> None:
        """取消当前扫描（用于空闲级别下降时中断）。"""
        self._cancelled = True

    # ── Layer 1: 骨架扫描 ──

    async def scan_layer1(self) -> dict:
        """扫描所有驱动器的顶级目录结构（depth=2）。

        耗时: ~5秒
        触发: 每次启动 / idle_score >= 0.3
        """
        self._cancelled = False
        stats = {"dirs": 0, "files": 0, "drives": []}

        # 检测所有驱动器 (Windows)
        if os.name == "nt":
            import string
            drives = [f"{d}:\\" for d in string.ascii_uppercase
                      if os.path.exists(f"{d}:\\")]
        else:
            drives = ["/"]

        stats["drives"] = drives
        entries = []

        for drive in drives:
            try:
                for entry in os.scandir(drive):
                    if entry.name in SKIP_DIRS:
                        continue
                    record = self._make_record(entry, scan_layer=1)
                    entries.append(record)
                    stats["dirs" if entry.is_dir() else "files"] += 1

                    # depth=2: 扫描一级子目录
                    if entry.is_dir(follow_symlinks=False):
                        try:
                            for sub_entry in os.scandir(entry.path):
                                if sub_entry.name in SKIP_DIRS:
                                    continue
                                sub_record = self._make_record(sub_entry, scan_layer=1)
                                entries.append(sub_record)
                                stats["dirs" if sub_entry.is_dir() else "files"] += 1
                        except PermissionError:
                            continue
            except PermissionError:
                continue

        # 批量写入数据库
        await self._db.upsert_env_catalog_batch(entries)
        await self._db.update_scan_progress("layer1", "completed",
                                             total_files=stats["files"],
                                             scanned_dirs=stats["dirs"])

        log.info("Layer 1 scan complete: %d dirs, %d files across %s",
                 stats["dirs"], stats["files"], drives)
        return stats

    # ── Layer 2: 高价值区域深度扫描 ──

    async def scan_layer2(self, user_home: str | None = None) -> dict:
        """扫描高价值目录的完整文件树。

        耗时: ~几分钟
        触发: idle_score >= 0.6
        支持断点续传。
        """
        self._cancelled = False
        if not user_home:
            user_home = str(Path.home())

        stats = {"dirs": 0, "files": 0, "important": 0}

        # 确定高价值目录
        high_value_dirs = []
        for pattern in HIGH_VALUE_PATTERNS:
            # 在用户主目录下查找
            candidate = Path(user_home) / pattern
            if candidate.exists():
                high_value_dirs.append(str(candidate))
            # 在其他常见位置查找
            for drive_letter in ["D", "E", "F"]:
                candidate = Path(f"{drive_letter}:\\{pattern}")
                if candidate.exists():
                    high_value_dirs.append(str(candidate))

        # 获取断点
        progress = await self._db.get_scan_progress("layer2")
        last_path = progress.get("last_scanned_path", "") if progress else ""
        skip_until = last_path if last_path else None

        await self._db.update_scan_progress("layer2", "in_progress",
                                             total_dirs=len(high_value_dirs))

        for dir_path in high_value_dirs:
            if self._cancelled:
                break
            await self._scan_directory_recursive(
                dir_path, scan_layer=2, stats=stats,
                skip_until=skip_until, max_depth=10
            )
            skip_until = None  # 只在第一个目录使用断点

        status = "completed" if not self._cancelled else "in_progress"
        await self._db.update_scan_progress("layer2", status,
                                             total_files=stats["files"],
                                             scanned_dirs=stats["dirs"])

        log.info("Layer 2 scan %s: %d dirs, %d files, %d important",
                 status, stats["dirs"], stats["files"], stats["important"])
        return stats

    # ── Layer 3: 全盘逐步扫描 ──

    async def scan_layer3_chunk(self, chunk_size: int = 1000) -> dict:
        """全盘扫描的一个片段 — 每次只处理 chunk_size 个文件。

        耗时: 取决于 chunk_size
        触发: idle_score >= 0.8
        设计为可中断、可恢复。
        """
        self._cancelled = False
        stats = {"dirs": 0, "files": 0, "skipped": 0}

        # 获取断点
        progress = await self._db.get_scan_progress("layer3")
        last_path = progress.get("last_scanned_path", "") if progress else ""

        # 获取所有未扫描的顶级目录
        all_dirs = await self._get_unscanned_layer3_dirs()
        if not all_dirs:
            await self._db.update_scan_progress("layer3", "completed")
            return stats

        await self._db.update_scan_progress("layer3", "in_progress")

        processed = 0
        for dir_path in all_dirs:
            if self._cancelled or processed >= chunk_size:
                break
            remaining = chunk_size - processed
            batch_stats = await self._scan_directory_recursive(
                dir_path, scan_layer=3, stats=stats,
                max_depth=5, max_files=remaining
            )
            processed += stats["files"]

        await self._db.update_scan_progress(
            "layer3", "in_progress",
            total_files=stats["files"],
            scanned_dirs=stats["dirs"],
            last_scanned_path=dir_path if all_dirs else ""
        )

        log.info("Layer 3 chunk: %d dirs, %d files processed", stats["dirs"], stats["files"])
        return stats

    # ── 增量扫描 ──

    async def incremental_scan(self) -> dict:
        """增量扫描 — 只检测已知路径的变化。

        快速检查已扫描目录中的文件是否有变化（mtime / size 变化）。
        """
        self._cancelled = False
        stats = {"changed": 0, "deleted": 0, "new": 0}

        # 获取所有已知的高价值目录 (layer 1-2)
        known_dirs = await self._db.get_env_catalog_dirs(max_layer=2)

        for dir_info in known_dirs:
            if self._cancelled:
                break
            dir_path = dir_info["path"]
            if not os.path.exists(dir_path):
                stats["deleted"] += 1
                await self._db.mark_env_entry_deleted(dir_path)
                continue

            # 获取该目录下的已知文件
            known_files = await self._db.get_env_files_in_dir(dir_path)
            known_map = {f["path"]: f for f in known_files}

            try:
                for entry in os.scandir(dir_path):
                    if entry.name in SKIP_DIRS:
                        continue
                    path = entry.path.replace("\\", "/")

                    if path in known_map:
                        # 检查 mtime 变化
                        try:
                            st = entry.stat(follow_symlinks=False)
                            if st.st_mtime != known_map[path].get("modified_at", 0):
                                stats["changed"] += 1
                                await self._db.update_env_entry(path, {
                                    "modified_at": st.st_mtime,
                                    "size_bytes": st.st_size,
                                    "scanned_at": time.time(),
                                })
                        except OSError:
                            pass
                        del known_map[path]
                    else:
                        # 新文件
                        stats["new"] += 1
                        record = self._make_record(entry, scan_layer=2)
                        await self._db.upsert_env_catalog_entry(record)
            except PermissionError:
                continue

            # 剩余的 known_map 中的文件已被删除
            for deleted_path in known_map:
                stats["deleted"] += 1
                await self._db.mark_env_entry_deleted(deleted_path)

        log.info("Incremental scan: %d changed, %d new, %d deleted",
                 stats["changed"], stats["new"], stats["deleted"])
        return stats

    # ── LLM 内容摘要 ──

    async def summarize_key_files(self, llm_router, max_files: int = 10) -> dict:
        """为关键文件生成 LLM 一句话摘要。

        只处理 is_important=1 且 summary="" 的文件。
        """
        stats = {"summarized": 0, "errors": 0}

        unsummarized = await self._db.get_unsummarized_important_files(limit=max_files)

        for file_info in unsummarized:
            if self._cancelled:
                break
            try:
                content = Path(file_info["path"]).read_text(
                    encoding="utf-8", errors="replace"
                )[:2000]  # 最多读 2000 字符

                # 使用 Tier2 (便宜) 生成摘要
                response = await llm_router.complete(
                    tier=2,
                    messages=[{
                        "role": "user",
                        "content": (
                            f"用一句话（不超过 50 字）描述这个文件的用途：\n"
                            f"路径: {file_info['path']}\n"
                            f"内容前 2000 字符:\n{content}"
                        ),
                    }],
                    max_tokens=100,
                )

                summary = response.get("content", "")[:200]
                await self._db.update_env_entry(file_info["path"], {"summary": summary})
                stats["summarized"] += 1

            except Exception as e:
                log.warning("Failed to summarize %s: %s", file_info["path"], e)
                stats["errors"] += 1

        log.info("Summarized %d key files (%d errors)",
                 stats["summarized"], stats["errors"])
        return stats

    # ── 内部方法 ──

    async def _scan_directory_recursive(
        self, root: str, scan_layer: int, stats: dict,
        skip_until: str | None = None, max_depth: int = 10,
        max_files: int | None = None, _depth: int = 0,
    ) -> None:
        """递归扫描目录。"""
        if _depth > max_depth or self._cancelled:
            return
        if max_files and stats["files"] >= max_files:
            return

        try:
            entries = list(os.scandir(root))
        except (PermissionError, OSError):
            return

        batch = []
        skipping = skip_until is not None

        for entry in entries:
            if self._cancelled:
                break
            if entry.name in SKIP_DIRS:
                continue

            if skipping:
                if entry.path == skip_until:
                    skipping = False
                continue

            record = self._make_record(entry, scan_layer)
            batch.append(record)
            stats["dirs" if entry.is_dir() else "files"] += 1

            if record.get("is_important"):
                stats["important"] = stats.get("important", 0) + 1

            # 分批写入（避免内存爆炸）
            if len(batch) >= self._scan_batch_size:
                await self._db.upsert_env_catalog_batch(batch)
                await self._db.update_scan_progress(
                    f"layer{scan_layer}", "in_progress",
                    last_scanned_path=entry.path
                )
                batch.clear()

            # 递归子目录
            if entry.is_dir(follow_symlinks=False):
                await self._scan_directory_recursive(
                    entry.path, scan_layer, stats,
                    max_depth=max_depth, max_files=max_files,
                    _depth=_depth + 1,
                )

        # 写入剩余批次
        if batch:
            await self._db.upsert_env_catalog_batch(batch)

    def _make_record(self, entry: os.DirEntry, scan_layer: int) -> dict:
        """从 DirEntry 构建数据库记录。"""
        try:
            st = entry.stat(follow_symlinks=False)
            mtime = st.st_mtime
            size = st.st_size if not entry.is_dir() else 0
        except OSError:
            mtime = 0
            size = 0

        ext = os.path.splitext(entry.name)[1].lower() if not entry.is_dir() else ""
        category = CATEGORY_MAP.get(ext, "other") if ext else ("directory" if entry.is_dir() else "other")
        is_important = 1 if entry.name in KEY_FILE_NAMES else 0
        path = entry.path.replace("\\", "/")

        return {
            "id": hashlib.md5(path.encode()).hexdigest(),
            "path": path,
            "type": "directory" if entry.is_dir() else "file",
            "size_bytes": size,
            "modified_at": mtime,
            "scanned_at": time.time(),
            "scan_layer": scan_layer,
            "category": category,
            "extension": ext,
            "parent_dir": str(Path(entry.path).parent).replace("\\", "/"),
            "is_important": is_important,
        }

    async def _get_unscanned_layer3_dirs(self) -> list[str]:
        """获取还未被第三层扫描过的目录。"""
        # 获取所有 layer1 的目录
        all_layer1 = await self._db.get_env_catalog_dirs(max_layer=1)
        # 排除已被 layer2/3 扫描的
        scanned = await self._db.get_env_catalog_dirs(min_layer=2)
        scanned_paths = {d["path"] for d in scanned}
        return [d["path"] for d in all_layer1
                if d["path"] not in scanned_paths and d["type"] == "directory"]
```

### 6.4 查询接口

扫描完成后，Eva 可以通过以下方式使用环境知识：

```python
# 新增工具：env_search（注册到 ToolRegistry）

async def env_search(query: str, category: str = "", limit: int = 20) -> list[dict]:
    """搜索环境目录。

    支持：
    - 按文件名模糊搜索: env_search("docker-compose")
    - 按分类过滤: env_search("", category="project")
    - 按摘要搜索: env_search("数据库配置")
    """
    return await db.search_env_catalog(query, category=category, limit=limit)

async def env_stats() -> dict:
    """返回环境扫描统计信息。"""
    return {
        "total_files": await db.count_env_entries(type="file"),
        "total_dirs": await db.count_env_entries(type="directory"),
        "by_category": await db.env_category_stats(),
        "important_files": await db.count_env_entries(is_important=1),
        "summarized": await db.count_env_entries(has_summary=True),
        "scan_progress": {
            "layer1": await db.get_scan_progress("layer1"),
            "layer2": await db.get_scan_progress("layer2"),
            "layer3": await db.get_scan_progress("layer3"),
        },
    }
```

---

## 7. 分布式集成

### 7.1 NodeState 扩展

```python
# anima/network/node.py — NodeState 新增字段

@dataclass
class NodeState:
    # ... 原有字段 ...
    idle_score: float = 0.0       # 新增：空闲评分 (0.0-1.0)
    idle_level: str = "busy"      # 新增：空闲级别
```

### 7.2 Gossip 广播 idle_score

在脚本心跳中已有的 gossip state 更新逻辑扩展：

```python
# heartbeat.py — _on_script_tick() 中的 gossip 更新扩展

if self._gossip_mesh:
    gs = self._gossip_mesh._local_state
    gs.current_load = snapshot.get("cpu_percent", 0) / 100.0
    gs.emotion = self._emotion.to_dict()
    # 新增
    gs.idle_score = self._idle_scheduler.score if self._idle_scheduler else 0.0
    gs.idle_level = self._idle_scheduler.level if self._idle_scheduler else "busy"
```

### 7.3 跨节点任务委派策略

修改 `SessionRouter` 的节点选择逻辑，加入 idle_score 权重：

```python
# anima/network/session_router.py — 节点选择扩展

def select_best_node(self, task_requirements: dict) -> str | None:
    """选择最佳执行节点。

    评分公式:
    node_score = (1 - current_load) * 0.3 + idle_score * 0.5 + capability_match * 0.2

    idle_score 权重最大 — 优先把任务给最空闲的节点。
    """
    candidates = []
    for node_id, state in self._gossip_mesh.get_alive_peers().items():
        # 能力匹配检查
        if not self._has_capability(state, task_requirements):
            continue

        # 计算综合评分
        load_score = 1.0 - state.current_load
        idle = getattr(state, "idle_score", 0.0)
        capability = self._capability_match_score(state, task_requirements)

        score = load_score * 0.3 + idle * 0.5 + capability * 0.2
        candidates.append((node_id, score, state))

    if not candidates:
        return None

    # 选择评分最高的节点
    candidates.sort(key=lambda x: -x[1])
    return candidates[0][0]
```

### 7.4 主动任务认领

当节点处于 DEEP 空闲状态时，可以主动查询其他节点是否有待处理任务：

```python
async def assist_peers(self) -> dict:
    """主动协助其他节点 — 认领可执行任务。

    只在 DEEP 空闲时触发。
    通过 Gossip 的 task_status_query 查询繁忙节点的任务队列。
    """
    stats = {"queried": 0, "claimed": 0}
    peers = self._gossip_mesh.get_alive_peers()

    for node_id, state in peers.items():
        # 只查询忙碌的节点
        idle = getattr(state, "idle_score", 1.0)
        if idle > 0.5:
            continue

        # 发送任务查询
        self._gossip_mesh.send_task_message("task_status_query", {
            "from_node": self._local_node_id,
            "query": "pending_tasks",
        })
        stats["queried"] += 1

    return stats
```

---

## 8. 与现有系统的集成点

### 8.1 HeartbeatEngine 集成

```python
# heartbeat.py — __init__ 新增

class HeartbeatEngine:
    def __init__(self, ...):
        # ... 原有代码 ...
        self._idle_scheduler: IdleScheduler | None = None

    def set_idle_scheduler(self, scheduler: IdleScheduler) -> None:
        """Set the idle scheduler instance."""
        self._idle_scheduler = scheduler

    async def _on_script_tick(self) -> None:
        """Script heartbeat handler — 扩展空闲调度。"""
        self._tick_count += 1

        # 原有四个操作
        snapshot = await self._sample_system()
        changes = await self._detect_file_changes()
        await self._decay_emotion()
        await self._confirm_alive()
        await self._check_agent_timeouts()

        # 新增：更新空闲检测器 + 触发空闲调度
        if self._idle_scheduler:
            self._idle_scheduler._detector.update(snapshot)
            await self._idle_scheduler.tick()

        # ... 原有的 snapshot_cache, diff, event push 逻辑不变 ...

        # 新增：tick_record 加入 idle 信息
        tick_record = {
            # ... 原有字段 ...
            "idle_score": self._idle_scheduler.score if self._idle_scheduler else 0,
            "idle_level": self._idle_scheduler.level if self._idle_scheduler else "unknown",
        }
```

### 8.2 main.py 初始化

```python
# main.py — run() 中的初始化扩展

from anima.perception.user_activity import UserActivityDetector
from anima.core.idle_scheduler import IdleDetector, IdleScheduler

# 在 heartbeat_engine 初始化之后
user_activity = UserActivityDetector()
idle_detector = IdleDetector(user_activity, event_queue)
idle_scheduler = IdleScheduler(
    idle_detector=idle_detector,
    event_queue=event_queue,
    config=get("idle_scheduler", {}),
)
heartbeat_engine.set_idle_scheduler(idle_scheduler)

# 环境扫描器
from anima.perception.env_scanner import EnvScanner
env_scanner = EnvScanner(db=memory_store)

# 在认知循环启动后，立即执行 Layer 1 骨架扫描
asyncio.create_task(env_scanner.scan_layer1(), name="env_scan_layer1")
```

### 8.3 EventRouter 扩展

```python
# event_router.py — 新增 IDLE_TASK 处理

# models/event.py 新增:
# IDLE_TASK = 9  # 空闲任务

def event_to_message(event: Event, self_thinking_ticks: dict[str, int]) -> str:
    # ... 原有代码 ...

    if t == EventType.IDLE_TASK:
        p = event.payload
        return (
            f"[IDLE TASK: {p.get('task_name', 'unknown')}]\n"
            f"{p.get('description', '')}\n"
            f"Handler: {p.get('handler', 'unknown')}\n"
            f"Max duration: {p.get('max_duration_s', 300)}s\n"
            "Execute this background task. Be efficient — you're running in idle time."
        )
```

### 8.4 认知循环中 user_message 回调

当用户发送消息时，需要通知 UserActivityDetector：

```python
# cognitive.py — _handle_event() 中

if event.type == EventType.USER_MESSAGE:
    if self._user_activity:
        self._user_activity.record_user_message()
```

### 8.5 Dashboard 集成

在 tick_record 和 dashboard 数据中展示空闲状态：

```
Dashboard 新增面板：
┌─────────────────────────────────┐
│  Idle Scheduler Status          │
│  ────────────────────────       │
│  Score: 0.72 (MODERATE) ↑       │
│  Trend: rising                  │
│  Running: env_deep_scan_layer2  │
│  Budget: $0.23 / $1.00 (23%)   │
│  Env Scan: L1 ✓  L2 47%  L3 — │
└─────────────────────────────────┘
```

---

## 9. 配置

### 9.1 default.yaml 新增

```yaml
# ── Idle Scheduler ──
idle_scheduler:
  enabled: true
  max_concurrent_tasks: 2          # 最大同时运行的空闲任务数

  # 三路信号权重
  weights:
    user_activity: 0.5
    system_load: 0.3
    queue_depth: 0.2

  # 四级阈值
  thresholds:
    busy: 0.0
    light: 0.3
    moderate: 0.6
    deep: 0.8

  # 每级 API 预算 (USD/hour)
  budget:
    busy: 0.0
    light: 0.5
    moderate: 1.0
    deep: 2.0

  # 用户活动检测
  user_activity:
    # 键鼠空闲多久算"离开" (秒)
    short_absence_s: 300        # 5 分钟
    long_absence_s: 1800        # 30 分钟
    # 是否使用系统 API 检测键鼠（关闭则只看消息时间）
    use_system_api: true

  # 环境扫描
  env_scan:
    enabled: true
    scan_batch_size: 500
    layer1_on_startup: true      # 启动时自动执行 Layer 1
    layer2_cooldown_s: 1800      # Layer 2 冷却
    layer3_chunk_size: 1000      # Layer 3 每片大小
    summarize_max_files: 10      # 每次摘要最多处理文件数
    # 额外的高价值目录
    extra_high_value_dirs: []
    # 额外跳过目录
    extra_skip_dirs: []
```

---

## 10. 实现计划

### Phase 1：本地 idle_score + 错峰调度（1-2 周）

不涉及分布式，只在单节点上实现。

| 步骤 | 任务 | 预估 | 涉及文件 |
|-----|------|------|---------|
| 1.1 | 实现 `UserActivityDetector` — Windows GetLastInputInfo | 0.5d | `perception/user_activity.py` (新) |
| 1.2 | 实现 `IdleDetector` — 三路信号融合 + EMA 平滑 | 0.5d | `core/idle_scheduler.py` (新) |
| 1.3 | 实现 `IdleScheduler` — 四级阈值 + 任务选择 + 调度核心 | 1d | `core/idle_scheduler.py` |
| 1.4 | 集成到 `HeartbeatEngine` — 脚本心跳调用 idle_detector.update() | 0.5d | `core/heartbeat.py` |
| 1.5 | LLM/大心跳动态调频 | 0.5d | `core/heartbeat.py` |
| 1.6 | 新增 `EventType.IDLE_TASK` + EventRouter 处理 | 0.5d | `models/event.py`, `core/event_router.py` |
| 1.7 | 实现 `EnvScanner` Layer 1 + Layer 2 + 增量扫描 | 2d | `perception/env_scanner.py` (新) |
| 1.8 | 数据库 schema: env_catalog + env_scan_progress | 0.5d | `memory/store.py` |
| 1.9 | 新增 `env_search` / `env_stats` 工具 | 0.5d | `tools/builtin/env_tools.py` (新) |
| 1.10 | 配置扩展 + main.py 初始化集成 | 0.5d | `config/default.yaml`, `main.py` |
| 1.11 | Dashboard idle 面板 | 0.5d | `dashboard/` |
| 1.12 | 测试 + 调参 | 1d | `tests/` |

**Phase 1 产出**：
- idle_score 实时计算，显示在 Dashboard
- LLM 心跳/大心跳根据空闲度动态调频
- 用户在电脑前时，Eva 只跑轻量后台
- 用户离开后，Eva 自动开始环境扫描和自进化
- 环境扫描 Layer 1/2 完成后，Eva 能直接从数据库查文件

### Phase 2：分布式 idle_score + 跨节点任务稀释（1 周）

| 步骤 | 任务 | 预估 | 涉及文件 |
|-----|------|------|---------|
| 2.1 | `NodeState` 新增 `idle_score` + `idle_level` 字段 | 0.5d | `network/node.py` |
| 2.2 | Gossip 广播 idle_score | 0.5d | `network/gossip.py`, `core/heartbeat.py` |
| 2.3 | `SessionRouter` 节点选择加入 idle_score 权重 | 1d | `network/session_router.py` |
| 2.4 | Layer 3 全盘扫描 + 断点续传 | 1d | `perception/env_scanner.py` |
| 2.5 | LLM 内容摘要功能 | 1d | `perception/env_scanner.py` |
| 2.6 | 主动任务认领（DEEP 空闲时协助繁忙节点） | 1d | `network/session_router.py` |
| 2.7 | 集成测试 + 双节点实测 | 1d | `tests/` |

**Phase 2 产出**：
- 所有节点通过 Gossip 共享自己的 idle_score
- 主节点忙碌时，任务自动委派给空闲节点
- 空闲节点主动帮忙分担任务
- 全盘环境扫描完成，关键文件有 LLM 摘要

---

## 11. 安全与边界

### 11.1 扫描安全

- **权限尊重**：`PermissionError` 静默跳过，不尝试提权
- **敏感目录**：不读取 `.env`、`credentials`、`ssh/` 等目录内容（只记录存在）
- **内容摘要**：只对 `is_important=1` 的文件调用 LLM，且限制读取 2000 字符
- **系统目录**：`C:\Windows\` 只记录结构，不深入扫描

### 11.2 资源保护

- **CPU 硬上限**：idle_score 中 CPU > 80% 时 system_idle 直接归 0
- **内存保护**：扫描器分批处理（500 条/批），避免内存爆炸
- **API 预算**：每级有独立的 hourly budget，超限立即停止
- **可中断**：所有扫描任务支持 `cancel()`，空闲级别下降时立即中断
- **磁盘写入**：SQLite WAL 模式，不阻塞读取

### 11.3 用户体验

- **用户回来时**：idle_score 下降 → 后台任务被暂停 → CPU/API 让路给用户交互
- **平滑过渡**：EMA 平滑避免瞬时波动导致任务频繁启停
- **透明可控**：Dashboard 显示当前空闲状态、运行任务、预算消耗
- **可关闭**：`idle_scheduler.enabled: false` 一键关闭

---

## 12. 监控与日志

### 日志格式

```
[INFO]  idle_scheduler  idle_score=0.73 level=moderate trend=rising
[INFO]  idle_scheduler  Dispatching idle task: 环境深度扫描（第二层） (level=moderate, score=0.73)
[INFO]  env_scanner     Layer 2 scan complete: 1234 dirs, 8901 files, 45 important
[DEBUG] idle_scheduler  LLM heartbeat adjusted: interval=300s (moderate idle)
[INFO]  idle_scheduler  Budget check: $0.45/$1.00 (moderate level)
[WARN]  idle_scheduler  Task env_deep_scan_layer3 cancelled — idle level dropped to light
```

### Dashboard WebSocket 事件

```json
{
    "type": "idle_status",
    "data": {
        "score": 0.73,
        "level": "moderate",
        "trend": "rising",
        "running_tasks": ["env_deep_scan_layer2"],
        "hourly_spend": 0.45,
        "env_scan": {
            "layer1": "completed",
            "layer2": "in_progress (47%)",
            "layer3": "pending"
        }
    }
}
```

---

## 13. 未来扩展

完成 Phase 1 + Phase 2 后的可能扩展方向：

1. **GPU 空闲检测** — 监控 GPU 使用率，深度空闲时触发本地模型推理任务
2. **网络带宽感知** — 空闲时自动下载更新、同步大文件
3. **学习任务** — 空闲时阅读项目文档/代码提交历史，构建更深的项目理解
4. **用户行为预测** — 基于历史空闲模式预测用户作息，提前规划大任务
5. **能耗优化** — 笔记本节点在电池模式下自动降低空闲任务强度
6. **多用户感知** — 检测是否有其他用户在使用共享电脑

---

## 附录 A：类依赖关系

```
UserActivityDetector  ──┐
                        ├──▶ IdleDetector ──▶ IdleScheduler ──▶ EventQueue
SystemMonitor ──────────┤                         │
EventQueue (qsize) ─────┘                         │
                                                  ▼
                                          ┌───────────────┐
                                          │ IDLE_TASK_POOL │
                                          │  ├─ env_scan   │
                                          │  ├─ evolution  │
                                          │  ├─ memory     │
                                          │  ├─ network    │
                                          │  └─ ...        │
                                          └───────┬───────┘
                                                  │
                              ┌────────────────────┼────────────────────┐
                              ▼                    ▼                    ▼
                        EnvScanner          EvolutionEngine      SessionRouter
                        (环境扫描)           (自进化)              (分布式协助)
```

## 附录 B：idle_score 状态机

```
                 score >= 0.3           score >= 0.6           score >= 0.8
    ┌───────┐  ──────────▶  ┌──────┐  ──────────▶  ┌─────────┐  ────────▶  ┌──────┐
    │ BUSY  │               │LIGHT │               │MODERATE │              │ DEEP │
    │< 0.3  │  ◀──────────  │0.3-  │  ◀──────────  │ 0.6-    │  ◀────────  │≥ 0.8│
    └───────┘  score < 0.3  │ 0.6  │  score < 0.6  │  0.8    │  score<0.8  └──────┘
                            └──────┘               └─────────┘

    允许:        允许:                   允许:                   允许:
    - 脚本心跳   - 增量扫描              - Layer 2 扫描          - Layer 3 扫描
                - 记忆整理              - 自进化提案            - LLM 内容摘要
                - 日志清理              - 工具审计              - 分布式协助
                                       - 网络健康检查          - 完整进化循环
```
