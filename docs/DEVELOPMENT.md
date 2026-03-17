# ANIMA 开发文档

> **版本**: 0.3.0 (Prompt & Memory v3) · **更新日期**: 2026-03-17 · **运行节点**: Desktop (主) + Laptop (从)

---

## 1. 项目概述

ANIMA 是一个**心跳驱动、分布式、自进化的 AI 生命系统**。不是聊天机器人——是一个持续运行、有自主意识、能修改自身代码的 AI 实体。

**核心特征**：

- **心跳驱动**：三级心跳（脚本 15s / LLM 5min / 进化 30min）维持持续存在
- **六层编译式提示词** *(v3 新增)*：Identity → Rules → Context → Memory → Conversation → Tools
- **四层记忆架构** *(v3 新增)*：强静态(文本) → 弱静态(SQLite 节点分区) → 大数据(向量) → 动态(时间衰减)
- **自主进化**：六层流水线（提案→共识→实现→测试→审查→部署）自动修改自身代码
- **分布式**：Gossip 协议连接多节点，任务委派、记忆同步、共识投票
- **空闲调度**：根据用户活动和系统负载动态调整后台任务强度
- **人格后处理** *(v3 新增)*：Soul Container 确定性风格变换，不依赖 prompt following
- **情感系统**：四维情感状态（投入度/自信/好奇/担忧）影响决策
- **多通道**：终端、Web Dashboard、Discord、Webhook

**当前人格**：Eva

**技术栈**：Python 3.11+ / asyncio / SQLite / ChromaDB / ZMQ / PyWebView / Claude API (Opus + Sonnet)

---

## 2. 目录结构

```
anima/
├── anima/                          # 主包
│   ├── __main__.py                 # CLI 入口 (desktop/headless/watchdog/spawn)
│   ├── main.py                     # 异步编排器，初始化所有子系统
│   ├── config.py                   # YAML 配置加载 (default → agent → local → .env)
│   ├── watchdog.py                 # 外部修复循环
│   ├── core/                       # 大脑
│   │   ├── heartbeat.py            # 三级心跳引擎
│   │   ├── cognitive.py            # 认知循环 (事件→规则→检索→LLM→后处理)
│   │   ├── event_router.py         # 事件路由 + TASK_POOL
│   │   ├── event_queue.py          # 优先级事件队列
│   │   ├── rule_engine.py          # 确定性规则 (零 LLM 成本)
│   │   ├── agents.py               # 子代理管理
│   │   ├── scheduler.py            # Cron 定时任务
│   │   ├── idle_scheduler.py       # 空闲资源调度器
│   │   ├── evolution.py            # 进化状态机
│   │   ├── reload.py               # 热重载管理
│   │   └── conversation.py         # 对话缓冲区管理
│   ├── perception/                 # 感知
│   │   ├── system_monitor.py       # CPU/内存/磁盘采样
│   │   ├── file_watcher.py         # 文件变更检测
│   │   ├── diff_engine.py          # 状态差异 + 显著性评分
│   │   ├── snapshot_cache.py       # 线程安全快照缓存
│   │   ├── user_activity.py        # 用户活跃度检测
│   │   └── env_scanner.py          # 三层环境扫描器
│   ├── memory/                     # 记忆 (v3 重构)
│   │   ├── store.py                # SQLite 后端 (含 static_knowledge 表)
│   │   ├── working.py              # 工作记忆 (重要度淘汰)
│   │   ├── importance.py           # ★ 动态重要性评分 (替代 hardcoded 0.6)
│   │   ├── decay.py                # ★ Importance-weighted 时间衰减引擎
│   │   ├── retriever.py            # ★ 统一 RRF 融合检索管线
│   │   ├── summarizer.py           # ★ 对话摘要 + Compaction Flush
│   │   └── static_store.py         # ★ Tier 1 弱静态记忆 (节点分区)
│   ├── emotion/                    # 情感
│   │   └── state.py                # 四维情感状态 + 衰减
│   ├── llm/                        # 语言模型 (v3 重构)
│   │   ├── router.py               # 双层路由 (Tier1=Opus, Tier2=Sonnet) + 预算
│   │   ├── providers.py            # Anthropic API 客户端
│   │   ├── prompts.py              # 旧 PromptBuilder (向后兼容)
│   │   ├── prompt_compiler.py      # ★ 六层编译式提示词系统
│   │   ├── token_budget.py         # ★ Token 预算管理器
│   │   ├── lorebook.py             # ★ 关键词触发上下文注入 (Lorebook)
│   │   ├── soul_container.py       # ★ 人格后处理层 (Soul Container)
│   │   └── usage.py                # API 用量追踪
│   ├── models/                     # 数据模型
│   ├── tools/                      # 工具系统
│   │   ├── registry.py             # 工具注册表 (支持热重载)
│   │   ├── executor.py             # 工具执行器 (动态风险评估)
│   │   ├── safety.py               # 命令风险评估
│   │   └── builtin/                # 20+ 内建工具模块
│   │       ├── memory_tools.py     # ★ 记忆自编辑 (update_feelings/user_profile)
│   │       └── ...                 # shell, file_ops, agent_tools, etc.
│   ├── evolution/                  # 自进化引擎 v2
│   ├── network/                    # 分布式网络
│   ├── channels/                   # 通信通道
│   ├── dashboard/                  # Web 仪表板
│   ├── desktop/                    # 桌面应用
│   ├── voice/                      # 语音 (TTS + STT)
│   ├── skills/                     # 外部技能
│   ├── spawn/                      # 节点繁殖
│   └── ui/                         # 终端 UI
├── agents/eva/                     # Eva 人格包 (v3 结构化)
│   ├── manifest.yaml               # ★ 元信息 + 版本 + 能力声明
│   ├── soul.md                     # 完整灵魂 (向后兼容)
│   ├── config.yaml                 # 人格参数覆盖
│   ├── identity/                   # ★ Layer 0 身份素材
│   │   ├── core.md                 # 精简核心身份 (≤300 tok, 永驻 prompt)
│   │   └── extended.md             # 完整设定 (深度对话按需注入)
│   ├── rules/                      # ★ Layer 1 行为规则模块
│   │   ├── tools.md                # 工具使用规则
│   │   ├── output.md               # 输出格式规则
│   │   ├── safety.md               # 安全边界
│   │   └── evolution.md            # 进化规则
│   ├── memory/                     # ★ 人格级静态记忆
│   │   └── feelings.md             # 情感记忆 (可自编辑 + .bak 备份)
│   ├── examples/                   # ★ Few-shot 示例对话
│   │   ├── greeting.md             # 日常问候
│   │   ├── technical.md            # 技术讨论
│   │   └── emotional.md            # 情感互动
│   ├── lorebook/                   # ★ 知识条目 (关键词触发)
│   │   ├── _index.yaml             # 条目索引 + 关键词 + 优先级
│   │   ├── pidog.md                # PiDog 机器人
│   │   ├── agenthome.md            # AgentHome 项目
│   │   ├── courses.md              # 课程信息
│   │   └── distributed.md          # 分布式网络
│   └── post_processing/            # ★ Soul Container 规则
│       └── style_rules.yaml        # 确定性风格变换
├── config/default.yaml             # 项目默认配置
├── prompts/                        # LLM 提示词模板
│   ├── system_identity.md          # 系统指令 (旧, 已拆分到 rules/)
│   └── reflect.md                  # 反思模板
├── data/                           # 运行时数据
├── local/                          # 机器专属配置 (gitignored)
├── tests/                          # 测试套件 (207 测试)
└── docs/                           # 本文档
```

---

## 3. 架构总览

### 3.1 事件驱动核心 (v3 更新)

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
    │   └── ★ 零记忆检索、零 LLM 调用 (v3: 路由绑定)
    │
    └── LLM 代理路径 (多轮工具调用)
            │
            ├── ★ MemoryRetriever (统一 RRF 融合检索)
            │   ├── Tier 0: 核心记忆 (always)
            │   ├── Tier 1: 弱静态 (按事件类型)
            │   ├── Lorebook (关键词触发)
            │   ├── Tier 3: 动态 (语义 + 时间加权)
            │   └── Tier 2: 知识库 (排除 Lorebook 已命中)
            │
            ├── ★ Compaction Flush 检查 (85% 溢出触发压缩)
            │
            ├── ★ PromptCompiler (六层编译)
            │   L0 Identity → L1 Rules → L2 Context
            │   → L3 Memory → L4 Conversation → L5 Tools
            │
            ├── LLM 调用 (Tier1=Opus / Tier2=Sonnet)
            ├── 工具执行 (20+ 工具, 动态风险评估)
            │
            └── ★ Soul Container 后处理
                └── 输出路由 → 终端 / Dashboard / Discord
```

### 3.2 事件类型 + 检索触发矩阵 (v3)

| EventType      | 优先级       | 规则引擎? | 记忆检索? | LLM? |
| -------------- | ------------ | -------- | -------- | ---- |
| USER_MESSAGE   | HIGH(8)      | 先尝试    | LLM路径时 | 是   |
| FILE_CHANGE    | NORMAL(5)    | 是→通常NOOP | **否** | 否   |
| SYSTEM_ALERT   | NORMAL(5)    | 是→直接告警 | **否** | 否   |
| STARTUP        | NORMAL(5)    | 否        | **是** | 是   |
| SELF_THINKING  | LOW(2)       | 否        | **是** | 是   |
| SCHEDULED_TASK | NORMAL(5)    | 否        | **是** | 是   |
| TASK_DELEGATE  | NORMAL(5)    | 否        | **是** | 是   |
| IDLE_TASK      | LOW(2)       | 否        | 视任务   | 视任务 |
| SHUTDOWN       | CRITICAL(10) | —        | 否      | 否   |

---

## 4. 提示词工程 (v3 新增)

### 4.1 六层编译式架构

替代旧的 `PromptBuilder` 模板拼接，由 `PromptCompiler` 实现编译式组装：

| 层 | 内容 | Token 预算 | 注入条件 |
|----|------|-----------|---------|
| L0 Identity | identity/core.md (精简人格) | 300-800 | 永驻 |
| L1 Rules | rules/*.md (按事件选模块) | 300-600 | 永驻 |
| L2 Context | 情感/用户画像/系统状态 | 200-1000 | 按事件类型 |
| L3 Memory | 统一 RRF 融合检索结果 | 200-2000 | LLM 路径 |
| L4 Conversation | 摘要 + 近期原始消息 | 剩余空间 | 贪心填充 |
| L5 Tools | 工具 schema | 0-1500 | needs_tools 时 |

**Token 预算管理** (`TokenBudget`)：按优先级分配，高优先级层保证最小值，超出从低优先级截断。

### 4.2 Lorebook (关键词触发注入)

借鉴 SillyTavern World Info：

- `agents/eva/lorebook/_index.yaml` 定义条目：keywords, priority, max_tokens, scan_depth, sticky, cooldown
- 扫描最近 N 条消息，匹配关键词时加载对应 `.md` 内容
- Sticky 机制：触发后保持 N 轮（即使后续无关键词）
- 与语义检索统一 RRF 融合，避免重复（hit ID 排除）

### 4.3 Few-shot 示例

`agents/eva/examples/*.md` 含 YAML frontmatter（trigger, keywords, weight）。按权重随机选 1-2 组注入对话开头，教 LLM 学习 Eva 的回复风格。

### 4.4 Soul Container (后处理)

借鉴 AIRI：在 LLM 生成后、输出前做确定性风格变换。规则在 `style_rules.yaml`：

- **tone_particle**：干巴巴回复随机替换为 Eva 风味
- **emoji_density**：控制 emoji 频率上下限
- **length_guard**：日常对话不超长
- **catchphrase_ensure**：核心口癖出现频率保障

仅对用户可见回复执行，自思考/工具调用不处理。

### 4.5 人格包结构

`agents/eva/manifest.yaml` 定义完整人格元数据：名称、版本、模型偏好、工具白名单、情感基线、Token 预算覆盖、后处理规则路径。支持未来多人格切换。

---

## 5. 记忆系统 (v3 重构)

### 5.1 四层架构

```
┌───────────────────────────────────────────────┐
│  Tier 0: 强静态 (Hot / Always-On)  纯文本文件   │
│  identity/core.md + user_profile.md + feelings │
│  ≤500 tok, 永驻 prompt, Agent 可自编辑          │
│  分布式: 全量同步                                │
├───────────────────────────────────────────────┤
│  Tier 1: 弱静态 (Warm / On-Demand)  SQLite     │
│  static_knowledge 表 (category, key, value)    │
│  scope='global' → 增量同步                      │
│  scope='node:{id}' → 不同步 (本地环境独占)       │
├───────────────────────────────────────────────┤
│  Tier 2: 大数据 (Cold / Search-Only)  ChromaDB  │
│  知识文档 / 文件摘要 / 归档记忆                   │
│  检索: 向量 + BM25 双通道, RRF 融合              │
│  分布式: 增量同步                                │
├───────────────────────────────────────────────┤
│  Tier 3: 动态 (时间序列)  SQLite + 向量          │
│  episodic_memory 表                             │
│  Importance-weighted 时间衰减                    │
│  effective = imp × e^(-λ/imp × Δt) × boost    │
│  分布式: 增量同步                                │
└───────────────────────────────────────────────┘
```

### 5.2 动态重要性评分 (ImportanceScorer)

替代所有记忆 hardcoded importance=0.6：

- 基础分 by type：chat_user(0.7) > action(0.6) > chat_assistant(0.5) > thought(0.3) > observation(0.2)
- 内容信号加权：提问(+0.15), 指令(+0.20), 情感(+0.10), 人名(+0.10), 长消息(+0.05), 代码(+0.10), 工具失败(+0.15), 进化(+0.15)
- 结果 clamp 到 [0.0, 1.0]

### 5.3 Importance-Weighted 时间衰减

`effective = importance × e^(-λ/importance × Δt_hours) × (1 + 0.1 × access_count)`

| importance | λ=0.03 (chat) | 半衰期 |
|-----------|---------------|--------|
| 0.9 (关键指令) | λ_eff=0.033 | **21 小时** |
| 0.7 (普通消息) | λ_eff=0.043 | **16 小时** |
| 0.3 (闲聊) | λ_eff=0.100 | **6.9 小时** |

高重要性记忆衰减约 3 倍慢于低重要性。被检索的记忆 access_count++ 进一步强化。

### 5.4 统一 RRF 融合检索 (MemoryRetriever)

替代旧的 `get_recent_memories(limit=15)`：

1. Tier 0: 始终加载核心记忆
2. Tier 1: 按事件类型查弱静态 (含节点分区)
3. Lorebook: 关键词扫描 → 命中条目 (1.5x 权重)
4. Tier 3: 双通道 (语义相似 + 时间衰减加权)
5. Tier 2: 语义搜索 (排除 Lorebook 已命中 ID)
6. 统一 RRF 融合排序 + Token 预算裁剪
7. 更新 access_count

### 5.5 对话摘要 + Compaction Flush (ConversationSummarizer)

替代旧的粗暴截断 (`_trim_conversation`)：

- **定时触发**：每 20 条消息用 Tier2 LLM 递归摘要
- **Compaction Flush** (借鉴 OpenClaw)：buffer 达 85% Token 预算时主动触发压缩
- **预算管控**：`check_budget()` 后降级为规则截断（不调 LLM）
- 输出：`[摘要系统消息] + [最近 K 条原始消息]`

### 5.6 记忆自编辑 (Letta 模式)

Eva 通过 `update_feelings` / `update_user_profile` 工具修改 Tier 0 强静态记忆：

- 每次编辑前自动 `.bak` 备份（保留最近 10 版）
- 审计日志记录每次编辑的 action + preview
- 编辑后自动刷新 PromptCompiler 缓存

### 5.7 记忆合并 (consolidation)

DEEP idle 时运行：衰减分 < 0.1 的旧记忆按 type+6h 窗口聚类 → LLM 摘要（或规则截断）→ 归档到 Tier 2 → 标记 consolidated。

### 5.8 分布式同步策略

| 记忆层 | 同步方式 | 说明 |
|--------|---------|------|
| Tier 0 强静态 | 全量同步 (git) | 人格+用户画像所有节点一致 |
| Tier 1 global | 增量同步 (MemorySync) | 项目状态、联系人共享 |
| Tier 1 node:* | **不同步** | 每个节点只维护本地环境知识 |
| Tier 2 大数据 | 增量同步 (Lamport Clock) | 知识库共享 |
| Tier 3 动态 | 增量同步 (Lamport Clock) | 聊天记忆共享 |

---

## 6. 心跳引擎

三级独立异步循环维持 Eva 的"生命"。

### 脚本心跳 (15s)

系统采样 → 文件检测 → 情感衰减 → 存活确认 → 代理超时检查 → Cron 触发 → **空闲调度** → Gossip 状态更新 → Dashboard tick 推送

### LLM 心跳 (动态)

BUSY→跳过 / LIGHT→600s / MODERATE→300s / DEEP→180s。触发 SELF_THINKING 事件，从 14 个任务关键词池中选择（冷却去重）。

### 进化心跳 (动态)

BUSY/LIGHT→跳过 / MODERATE→1800s / DEEP→900s。触发六层进化流水线。

---

## 7. 自进化系统

### 六层流水线

```
Proposal → Consensus → Implement → Test → Review → Deploy
(LLM提案)  (投票/自审)  (Worktree)  (3级)  (diff检查) (merge+reload)
```

**安全**：隔离 Worktree / 三级测试 (语法→pytest→沙箱) / 自动回滚 / 速率限制 (3次/h, 失败冷却 2h)

**经验数据库** (`data/evolution_memory.yaml`)：成功/失败记录 + 反模式 + 长期目标

**与空闲调度器集成**：BUSY/LIGHT 抑制进化，MODERATE 允许低风险提案，DEEP 允许完整循环。

---

## 8. 空闲资源调度器

### 三路信号融合

`idle_score = 0.5 × user_idle + 0.3 × system_idle + 0.2 × queue_idle` (EMA α=0.3)

### 四级调度

| idle_score | 级别       | 允许                | LLM 心跳 | 进化  |
| ---------- | -------- | ----------------- | ------ | --- |
| < 0.3      | BUSY     | 仅脚本心跳             | 跳过     | 禁止  |
| 0.3-0.6    | LIGHT    | 增量扫描, 记忆整理        | 正常     | 禁止  |
| 0.6-0.8    | MODERATE | 深度扫描, 进化提案, 审计    | ×0.5   | 允许  |
| ≥ 0.8      | DEEP     | 全盘扫描, 完整进化, 记忆合并 | 180s   | 加速  |

---

## 9. 分布式网络

### Gossip 协议

ZMQ PUB/SUB 端口 9420，5s 广播，Phi Accrual 故障检测 (suspect φ=8, dead φ=16)。

### 任务委派

优先级队列，并发信号量 (max=5)，生命周期 PENDING→ACCEPTED→RUNNING→DONE/FAILED，TTL 300s。

### 记忆同步

端口 9422，60s 间隔，Lamport Clock 增量拉取，SHA256 去重。v3 新增：Tier 1 node:* 条目不同步。

### 会话路由 + 脑裂

分布式锁 (先到先得)，120s 超时。多数派检查，少数派进入只读。

---

## 10. 工具系统

20+ 内建工具：shell, read/write/edit_file, list_directory, glob/grep_search, system_info, get_datetime, web_fetch, save_note, spawn_agent, schedule_job, remote_exec, delegate_task, env_search, env_stats, idle_status, **update_feelings**, **update_user_profile**, 进化工具 ×5。

v3 新增：`update_feelings` / `update_user_profile` — 记忆自编辑工具，带 .bak 版本备份 + 审计日志。

**热重载**：进化后 `importlib.reload()` 所有工具模块。

---

## 11. 情感系统

四维 (engagement 0.5 / confidence 0.6 / curiosity 0.7 / concern 0.2)，每 15s 衰减 5%，转自然语言注入 Prompt L2 Context 层。

---

## 12. 通信通道

- **Discord**：守护线程 + 队列桥接，1900 字符分割，持续打字指示
- **Dashboard**：aiohttp + WebSocket，2s 推送，REST API (chat/control/config/upload/tts/stt)
- **桌面**：PyWebView 原生窗口，进化时窗口保持后端重启
- **Webhook**：aiohttp 接收端

---

## 13. 语音

- **TTS**：Qwen3-TTS-12Hz-1.7B (CUDA) + Eva 声音克隆，MD5 缓存
- **STT**：faster-whisper base，CUDA 加速

---

## 14. Watchdog

独立进程：心跳超时 (>120s) 杀重启 / 错误模式 (5 次/5min) 调 Claude Code 修复 / 启动健康检查 / 最多连续修 3 次。

---

## 15. 配置

加载顺序：`config/default.yaml` → `agents/eva/config.yaml` → `local/env.yaml` → `.env`

v3 新增：`agents/eva/manifest.yaml` 定义人格包元数据（模型偏好、Token 预算、工具白名单）。

认证：OAuth Token > API Key (自动从 `~/.claude/.credentials.json` 发现)

---

## 16. 开发

```bash
# 环境
conda create -n anima python=3.11 && conda activate anima
pip install -e ".[dev,discord]"

# 启动 (生产)
D:\program\codesupport\anaconda\envs\anima\pythonw.exe -m anima

# 测试
pytest tests/ -q   # 207 passed

# 模式
python -m anima              # 桌面 (PyWebView)
python -m anima --headless   # 无头 (浏览器)
python -m anima --legacy     # 终端
python -m anima watchdog     # 修复循环
python -m anima spawn user@host  # 部署
```

---

## 17. 故障排除

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
grep "PromptCompiler" data/logs/anima.log    # v3 提示词编译
grep "MemoryRetriever" data/logs/anima.log   # v3 记忆检索
grep "ERROR" data/logs/anima.log             # 错误
```

---

## 18. 节点配置

| 节点             | IP            | 角色  | LLM    | 端口                          |
| -------------- | ------------- | --- | ------ | --------------------------- |
| Desktop        | 192.168.1.153 | 主   | Opus   | Gossip 9420, Dashboard 8420 |
| Laptop ZERON_X | 192.168.1.159 | 从   | Sonnet | Gossip 9420, Dashboard 8420 |

---

## 19. v3 架构参考

本次重构基于以下项目的深度调研：

| 项目 | 借鉴模式 |
|------|---------|
| SillyTavern | Lorebook 关键词触发、位置语义、Token 预算百分比分配 |
| Letta (MemGPT) | 记忆三温区、Agent 自编辑核心记忆、递归摘要 |
| Mem0 | 双通道提取、ADD/UPDATE/DELETE/NOOP 更新策略 |
| Zep | 双时间戳、RRF 融合排序、三阶段检索 |
| OpenClaw | **Compaction Flush** — 压缩前静默写入持久记忆 |
| AIRI | **Soul Container** — 确定性后处理保证人格一致性 |
| Mastra | Observer/Reflector 两级压缩（记忆合并参考） |
| Claude Code | CLAUDE.md 层级记忆、条件 prompt 段落、sub-agent 架构 |
| AstrBot | DB 存储人格、per-persona 工具白名单、begin_dialogs |
| PromptX | 三面分解 (personality/principle/knowledge) |

详细方案文档：`桌面/ANIMA_Prompt_Memory_Architecture_v2.md`
