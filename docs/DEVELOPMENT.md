# ANIMA 开发文档

> **版本**: 0.2.0 · **更新日期**: 2026-03-17 · **运行节点**: Desktop (主) + Laptop (从)

---

## 1. 项目概述

ANIMA 是一个**心跳驱动、分布式、自进化的 AI 生命系统**。不是聊天机器人——是一个持续运行、有自主意识、能修改自身代码的 AI 实体。

**核心特征**：

- **心跳驱动**：三级心跳（脚本 15s / LLM 5min / 进化 30min）维持持续存在
- **自主进化**：六层流水线（提案→共识→实现→测试→审查→部署）自动修改自身代码
- **分布式**：Gossip 协议连接多节点，任务委派、记忆同步、共识投票
- **空闲调度**：根据用户活动和系统负载动态调整后台任务强度
- **环境感知**：三层扫描建立全盘文件认知，增量检测变化
- **情感系统**：四维情感状态（投入度/自信/好奇/担忧）影响决策
- **多通道**：终端、Web Dashboard、Discord、Webhook

**当前人格**：Eva

**技术栈**：Python 3.11+ / asyncio / SQLite / ZMQ / PyWebView / Claude API (Opus + Sonnet)

---

## 2. 目录结构

```
anima/
├── anima/                     # 主包
│   ├── __main__.py            # CLI 入口 (desktop/headless/watchdog/spawn)
│   ├── main.py                # 异步编排器，初始化所有子系统
│   ├── config.py              # YAML 配置加载 (default → agent → local → .env)
│   ├── watchdog.py            # 外部修复循环 (崩溃检测 + Claude Code 自动修复)
│   ├── core/                  # 大脑
│   │   ├── heartbeat.py       # 三级心跳引擎
│   │   ├── cognitive.py       # 认知循环 (事件→规则→LLM→工具)
│   │   ├── event_router.py    # 事件路由 + TASK_POOL
│   │   ├── event_queue.py     # 优先级事件队列 (asyncio.PriorityQueue)
│   │   ├── rule_engine.py     # 确定性规则 (零 LLM 成本)
│   │   ├── agents.py          # 子代理管理 (internal/claude_code/shell)
│   │   ├── scheduler.py       # Cron 定时任务
│   │   ├── idle_scheduler.py  # 空闲资源调度器
│   │   ├── evolution.py       # 进化状态机 (EvolutionState)
│   │   └── conversation.py    # 对话缓冲区管理
│   ├── perception/            # 感知
│   │   ├── system_monitor.py  # CPU/内存/磁盘采样 (psutil)
│   │   ├── file_watcher.py    # 文件变更检测 (轮询)
│   │   ├── diff_engine.py     # 状态差异 + 显著性评分
│   │   ├── snapshot_cache.py  # 线程安全快照缓存
│   │   ├── user_activity.py   # 键鼠空闲检测 (Win32 GetLastInputInfo)
│   │   └── env_scanner.py     # 三层环境扫描器
│   ├── memory/                # 记忆
│   │   ├── store.py           # SQLite + 可选 ChromaDB 向量搜索
│   │   └── working.py         # 工作记忆 (重要度淘汰，非 FIFO)
│   ├── emotion/               # 情感
│   │   └── state.py           # 四维情感状态 + 衰减
│   ├── llm/                   # 语言模型
│   │   ├── router.py          # 双层路由 (Tier1=Opus, Tier2=Sonnet) + 预算
│   │   ├── providers.py       # Anthropic API 客户端 (OAuth + API Key)
│   │   ├── prompts.py         # 上下文感知 Prompt 构建器
│   │   └── usage.py           # API 用量追踪
│   ├── models/                # 数据模型
│   │   ├── event.py           # EventType, EventPriority, Event
│   │   ├── decision.py        # ActionType, Decision
│   │   ├── memory_item.py     # MemoryType, MemoryItem
│   │   ├── perception_frame.py# DiffRule, FieldDiff, StateDiff
│   │   └── tool_spec.py       # RiskLevel, ToolSpec
│   ├── tools/                 # 工具系统
│   │   ├── registry.py        # 工具注册表 (支持热重载)
│   │   ├── executor.py        # 工具执行器 (动态风险评估)
│   │   └── builtin/           # 18+ 内建工具模块
│   ├── evolution/             # 自进化引擎 v2
│   │   ├── engine.py          # 六层流水线编排器
│   │   ├── proposal.py        # 提案类型/状态/队列
│   │   ├── consensus.py       # 分布式共识投票
│   │   ├── sandbox.py         # Git Worktree 隔离 + 三级测试
│   │   ├── deployer.py        # 部署/回滚/热重载
│   │   └── memory.py          # 进化经验数据库
│   ├── network/               # 分布式网络
│   │   ├── gossip.py          # ZMQ PUB/SUB Gossip Mesh
│   │   ├── node.py            # 节点身份 + 状态向量
│   │   ├── discovery.py       # mDNS 自动发现
│   │   ├── session_router.py  # 会话路由 + 任务委派 (TaskDelegate)
│   │   ├── sync.py            # 记忆增量同步 (Lamport Clock)
│   │   ├── split_brain.py     # 脑裂检测
│   │   └── protocol.py        # 消息协议 (msgpack + HMAC)
│   ├── channels/              # 通信通道
│   │   ├── discord_channel.py # Discord Bot (线程安全)
│   │   └── webhook_channel.py # HTTP Webhook (aiohttp)
│   ├── dashboard/             # Web 仪表板
│   │   ├── server.py          # aiohttp + WebSocket 推送
│   │   ├── hub.py             # 全子系统聚合器
│   │   └── page.py            # HTML/CSS/JS 前端
│   ├── desktop/               # 桌面应用
│   │   ├── app.py             # PyWebView 原生窗口 + 后端线程
│   │   └── singleton.py       # 单实例锁
│   ├── voice/                 # 语音
│   │   ├── tts.py             # Qwen3-TTS (CUDA, Eva 声音克隆)
│   │   └── stt.py             # faster-whisper STT
│   ├── skills/                # 外部技能
│   │   └── loader.py          # 技能自动发现 + Cron 注册
│   ├── spawn/                 # 节点繁殖
│   │   ├── deployer.py        # SSH/本地部署
│   │   └── packager.py        # 打包 + bootstrap 脚本
│   └── ui/                    # 终端 UI
│       └── terminal.py        # Rich 终端界面
├── agents/eva/                # Eva 人格定义
│   ├── soul.md                # 灵魂 (身份/性格/原则)
│   ├── feelings.md            # 情感记忆 (gitignored)
│   └── config.yaml            # 人格参数覆盖
├── config/default.yaml        # 项目默认配置
├── prompts/                   # LLM 提示词模板
├── data/                      # 运行时数据
├── local/                     # 机器专属配置 (gitignored)
├── tests/                     # 测试套件 (199 测试)
└── docs/                      # 本文档
```

---

## 3. 架构总览

### 3.1 事件驱动核心

```
心跳引擎 (15s/5min/30min)
    │
    ▼
事件队列 (PriorityQueue, max=256)
    │  HIGH(8): 用户消息
    │  NORMAL(5): 文件变更, 定时任务, 任务委派
    │  LOW(2): 自思考, 空闲任务
    ▼
认知循环 (AgenticLoop)
    ├── 规则引擎 (零成本: 问候/文件变更/告警)
    └── LLM 代理循环 (多轮工具调用)
            ├── Tier1 (Opus) ← 用户消息
            └── Tier2 (Sonnet) ← 内部事件
                    │
                    ▼
              工具执行器 (18+ 工具, 动态风险评估)
                    │
                    ▼
              输出路由 → 终端 / Dashboard / Discord
```

### 3.2 事件类型

| EventType      | 优先级          | 来源     | 处理        |
| -------------- | ------------ | ------ | --------- |
| USER_MESSAGE   | HIGH(8)      | 用户     | Tier1 LLM |
| FILE_CHANGE    | NORMAL(5)    | 心跳     | 规则引擎      |
| SYSTEM_ALERT   | NORMAL(5)    | 心跳     | 规则引擎      |
| STARTUP        | NORMAL(5)    | 启动     | LLM       |
| SELF_THINKING  | LOW(2)       | LLM 心跳 | Tier2 LLM |
| SCHEDULED_TASK | NORMAL(5)    | Cron   | LLM       |
| TASK_DELEGATE  | NORMAL(5)    | 远程节点   | LLM       |
| IDLE_TASK      | LOW(2)       | 空闲调度器  | LLM       |
| SHUTDOWN       | CRITICAL(10) | 信号     | 关闭        |

---

## 4. 心跳引擎

三级独立异步循环维持 Eva 的"生命"。

### 脚本心跳 (15s)

系统采样 → 文件检测 → 情感衰减 → 存活确认 → 代理超时检查 → Cron 触发 → **空闲调度** → Gossip 状态更新 → Dashboard tick 推送

### LLM 心跳 (动态)

BUSY→跳过 / LIGHT→600s / MODERATE→300s / DEEP→180s。触发 SELF_THINKING 事件，从 13 个任务关键词池中选择（冷却去重）。

### 进化心跳 (动态)

BUSY/LIGHT→跳过 / MODERATE→1800s / DEEP→900s。触发六层进化流水线。

---

## 5. 自进化系统

### 六层流水线

```
Proposal → Consensus → Implement → Test → Review → Deploy
(LLM提案)  (投票/自审)  (Worktree)  (3级)  (diff检查) (merge+reload)
```

**安全**：隔离 Worktree / 三级测试 (语法→pytest→沙箱) / 自动回滚 / 速率限制 (3次/h, 失败冷却 2h)

**经验数据库** (`data/evolution_memory.yaml`)：成功/失败记录 + 反模式 + 长期目标

**与空闲调度器集成**：BUSY/LIGHT 抑制进化，MODERATE 允许低风险提案，DEEP 允许完整循环。

---

## 6. 空闲资源调度器

### 三路信号融合

`idle_score = 0.5 × user_idle + 0.3 × system_idle + 0.2 × queue_idle` (EMA α=0.3)

### 四级调度

| idle_score | 级别       | 允许                | LLM 心跳 | 进化  |
| ---------- | -------- | ----------------- | ------ | --- |
| < 0.3      | BUSY     | 仅脚本心跳             | 跳过     | 禁止  |
| 0.3-0.6    | LIGHT    | 增量扫描, 记忆整理        | 正常     | 禁止  |
| 0.6-0.8    | MODERATE | 深度扫描, 进化提案, 审计    | ×0.5   | 允许  |
| ≥ 0.8      | DEEP     | 全盘扫描, 完整进化, 分布式协助 | 180s   | 加速  |

### 环境扫描

| 层       | 范围            | 耗时  | 触发       |
| ------- | ------------- | --- | -------- |
| Layer 1 | 所有驱动器 depth=2 | ~5s | 启动时      |
| Layer 2 | 高价值目录递归       | ~分钟 | MODERATE |
| Layer 3 | 全盘逐步推进        | ~天  | DEEP     |

结果存 SQLite `env_catalog` 表。Eva 通过 `env_search` 工具查询。

---

## 7. 分布式网络

### Gossip 协议

ZMQ PUB/SUB 端口 9420，5s 广播，Phi Accrual 故障检测 (suspect φ=8, dead φ=16)。

### 状态向量

`node_id, hostname, ip, port, version, status, current_load, idle_score, idle_level, emotion, capabilities, ...`

### 任务委派

优先级队列，并发信号量 (max=5)，生命周期 PENDING→ACCEPTED→RUNNING→DONE/FAILED，TTL 300s。

### 记忆同步

端口 9422，60s 间隔，Lamport Clock 增量拉取，SHA256 去重。

### 会话路由

分布式锁 (先到先得)，120s 超时，节点死亡自动释放。

### 脑裂检测

多数派检查，少数派进入只读。

---

## 8. 工具系统

18+ 内建工具：shell (动态风险), read/write/edit_file, list_directory, glob/grep_search, system_info, get_datetime, web_fetch, save_note, spawn_agent, schedule_job, remote_exec, delegate_task, env_search, env_stats, idle_status, 进化工具 ×5。

**热重载**：进化后 `importlib.reload()` 所有工具模块。

---

## 9. 记忆系统

- **工作记忆**：20 条，重要度淘汰
- **长期记忆**：SQLite (episodic_memories, emotion_log, llm_usage, env_catalog, ...)
- **对话缓冲**：50 轮，启动从 DB 恢复，进化 checkpoint 保存

---

## 10. 情感系统

四维 (engagement 0.6 / confidence 0.6 / curiosity 0.8 / concern 0.2)，每 15s 衰减 5%，转自然语言注入 Prompt。

---

## 11. 通信通道

- **Discord**：守护线程 + 队列桥接，1900 字符分割，持续打字指示
- **Dashboard**：aiohttp + WebSocket，2s 推送，REST API (chat/control/config/upload/tts/stt)
- **桌面**：PyWebView 原生窗口，进化时窗口保持后端重启
- **Webhook**：aiohttp 接收端

---

## 12. 语音

- **TTS**：Qwen3-TTS-12Hz-1.7B (CUDA) + Eva 声音克隆，MD5 缓存
- **STT**：faster-whisper base，CUDA 加速

---

## 13. Watchdog

独立进程：心跳超时 (>120s) 杀重启 / 错误模式 (5 次/5min) 调 Claude Code 修复 / 启动健康检查 / 最多连续修 3 次。

---

## 14. 配置

加载顺序：`config/default.yaml` → `agents/eva/config.yaml` → `local/env.yaml` → `.env`

认证：OAuth Token > API Key (自动从 `~/.claude/.credentials.json` 发现)

---

## 15. 开发

```bash
# 环境
conda create -n anima python=3.11 && conda activate anima
pip install -e ".[dev,discord]"

# 启动 (生产)
D:\program\codesupport\anaconda\envs\anima\pythonw.exe -m anima

# 测试
pytest tests/ -q   # 199 passed

# 模式
python -m anima              # 桌面 (PyWebView)
python -m anima --headless   # 无头 (浏览器)
python -m anima --legacy     # 终端
python -m anima watchdog     # 修复循环
python -m anima spawn user@host  # 部署
```

---

## 16. 故障排除

| 症状             | 解决                       |
| -------------- | ------------------------ |
| 打开浏览器而非原生窗口    | `pip install pywebview`  |
| 端口 8420 占用循环崩溃 | `taskkill` 旧进程后重启        |
| 进化不触发          | 需 MODERATE 空闲 (idle≥0.6) |
| GBK 崩溃         | 确认用 conda env anima 启动   |
| Discord 不连接    | `local/env.yaml` 配 token |

**日志**：`data/logs/anima.log` (24h 轮转)
**心跳**：`data/watchdog_heartbeat.json` (15s 更新)

```bash
grep "idle_scheduler" data/logs/anima.log   # 空闲调度
grep "evolution" data/logs/anima.log         # 进化
grep "ERROR" data/logs/anima.log             # 错误
```

---

## 17. 节点配置

| 节点             | IP            | 角色  | LLM    | 端口                          |
| -------------- | ------------- | --- | ------ | --------------------------- |
| Desktop        | 192.168.1.153 | 主   | Opus   | Gossip 9420, Dashboard 8420 |
| Laptop ZERON_X | 192.168.1.159 | 从   | Sonnet | Gossip 9420, Dashboard 8420 |
