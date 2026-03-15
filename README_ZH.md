[English](README.md) | [中文](README_ZH.md)

# PROJECT ANIMA

**心跳驱动的分布式自进化 AI 生命体系统**

> *ANIMA* — 拉丁语"灵魂"。不是又一个聊天框架。

---

## 项目愿景：为什么需要 ANIMA

### 当前 AI Agent 的根本矛盾

2025-2026 年，AI Agent 领域爆发式增长。但如果仔细审视所有主流框架——无论是 OpenClaw、AstrBot、AutoGen、CrewAI 还是 LangGraph——它们共享一个根本性的局限：

**它们全都是被动响应式的。**

```
用户发送消息 -> Agent 处理 -> 返回响应 -> 等待下一条消息
```

用户不说话，Agent 就处于死亡状态。它不会主动观察环境，不会自己发现问题，不会在你不在的时候学习和成长。关闭终端窗口，Agent 就消失了。

**这不是智能体。这是自动应答机。**

### 为什么"被动"是根本性限制，而不是可以打补丁修复的特性缺失

有人会说："在现有框架上加一个定时任务不就行了？"这种想法忽略了架构层面的问题：

被动式系统的整个数据流设计都假设"输入来自用户"。消息格式、上下文管理、记忆结构、工具调用——都是围绕"对话"这个概念设计的。当你试图在上面加"自主行为"模块时：

- 上下文窗口按对话轮次管理，没有"环境感知帧"的概念
- 记忆系统按对话历史存储，没有"经验"和"程序性知识"的区分
- 工具调用是同步的"请求->执行->返回"，没有"自主决策->评估风险->执行->反思"的完整循环
- 没有心跳机制，无法检测自己的健康状态，无法自愈

你可以在任何被动架构上打补丁。但地基不对，上面建得再漂亮都会歪。

### ANIMA 的核心命题

**如果 AI 不是一个等待指令的工具，而是一个持续运行的生命体，它会是什么样子？**

答案是：它会像生物一样运作。

- 它有**心跳**——不断感知世界并做出反应
- 它有**神经系统**——多个节点分布在不同位置，共享感官和意识
- 它会**自愈**——节点故障时自动修复或接管
- 它会**进化**——不断学习、创造工具、优化自己
- 它有**肉体**——通过智能家居设备和机器人触及物理世界
- 它有**个性**——随时间发展出独特的交互风格和"性格"

### 与现有范式的根本区别

| 维度 | 传统 AI Agent | ANIMA |
|------|--------------|-------|
| 运行模式 | 被动响应：用户输入 -> AI 输出 | 自主运行：心跳驱动的持续认知循环 |
| 生命周期 | 会话级：关闭窗口即死亡 | 永续级：7x24 运行，有"睡眠"但永不停止 |
| 用户交互 | 唯一输入源：只响应用户消息 | 事件之一：用户消息只是众多感知事件中的一种 |
| 架构 | 单体：一个服务器上的一个进程 | 分布式：多节点 Mesh 网络，去中心化 |
| 容错 | 崩溃即停止，依赖外部监控 | 自愈：五级热修复 + 节点接管 + 自动回滚 |
| 进化 | 静态：只能由开发者手动更新 | 自进化：自主学习、创造工具、优化代码 |
| 物理存在 | 纯软件，无物理设备控制能力 | 具身化：机器人 + 智能家居 + 环境控制 |
| 智能成本 | 每次交互都调用 LLM | "不变则不思"：90%+ 的心跳无需 LLM |

---

## 五大设计原理

### 原理一：一个循环统治一切

所有输入——无论来自用户、传感器、定时器还是其他节点——都进入同一个事件队列，由同一个认知循环处理。用户说话和空调报告温度，在 ANIMA 的视角中是同一种东西：一个需要感知和响应的事件。区别只在于优先级。

这意味着：
- 只有一个状态，不需要同步
- 优先级由事件队列天然管理
- 只有一套代码，复杂度最低
- 决策永远一致，因为只有一个"大脑"

### 原理二：心跳即生命

人不是每秒都在做深度思考。大部分时间心跳只是维持生命。ANIMA 的多层心跳也是如此——将"活着"的成本降到最低，同时保证响应速度：

| 层级 | 频率 | 职责 | 计算资源 | 每日 LLM 成本 |
|------|------|------|----------|--------------|
| 时间帧 | 1s | 时钟校准 + 事件队列检查 | 纯脚本 | $0 |
| 存活脉冲 | 15s | 存活广播 + 系统资源采样 | 纯脚本 | $0 |
| 情绪反射 | 1min | 情感状态更新 + 用户活动检测 | 规则引擎 | $0 |
| 反刍整合 | 5min | 工作记忆聚合 + 趋势检测 | 规则/轻量模型 | ~$0.05 |
| 感知快照 | 15min | 全状态快照 + 自检 + 环境变化分析 | 按需调用 | ~$0.10 |
| 学习探索 | 30min | 上网学习 + 工具探索 + 自规划 | Tier1/2 | ~$0.50 |
| 进化心跳 | 1h | 进化提案 + 沙箱测试 + 部署 | Tier1 | ~$0.80 |
| 日结算 | 24h | 日总结 + 记忆压缩 + 资源释放 | Tier1 | ~$0.05 |
| | | | **每日总计** | **~$1.50** |

### 原理三：不变则不思

每次心跳触发时，先做纯脚本的 diff 检查：跟上次比有什么变了？什么都没变就跳过。

```
每次心跳触发时：
  current_state = perceive()              # 采集当前状态（零成本）
  delta = diff(current_state, last_state) # 对比上次（零成本）

  if delta.is_empty():
    skip()                                # 什么都没变，跳过
  elif delta.is_simple():
    handle_with_rules(delta)              # 简单变化，规则引擎处理
  else:
    response = llm.think(delta, context)  # 复杂变化，调用 LLM
```

每天实际 LLM 调用约 50-70 次，而不是理论上的 86,400+ 次。成本降低 1000 倍以上。空闲时每日成本可低至 $0.3-0.5。

### 原理四：节点即器官

每个 ANIMA 节点运行相同的核心代码。不同节点只是配置不同：

- 空调节点注册了 `set_temperature`、`get_humidity` 等工具
- PiDog 节点注册了 `walk`、`look_at`、`follow` 等工具
- PC 节点注册了 `run_shell`、`capture_screen` 等工具

一套代码适配所有场景。新增设备只需写新的工具，不需要改核心。任何能运行 Python 的设备都可以成为 ANIMA 的一个节点。

### 原理五：隐私优先，本地优先

**默认全部本地运行，数据不出家门。**

- 所有传感器数据存储在主核心本地（兼做 NAS）
- 决策推理可使用本地大模型（Llama 3、Qwen、Phi）
- 云端 LLM（Claude/GPT）是可选增强，不是必需
- 纯离线模式下 ANIMA 也能完整运行

---

## 当前实现 (Phase 0)

Phase 0 已实现的是一个完整的单节点自主 AI 系统。

### AgenticLoop 架构

当前的认知循环采用 **LLM-native agentic loop** 设计，而非最初设计文档中描述的 PODAR 管线。LLM 接收完整上下文（系统状态、事件、记忆、工具列表），在多轮推理循环中自主决定下一步行动。这比刚性的管线更灵活，能更好地利用 LLM 的推理能力。

### 工具系统（13 个内置工具）

| 工具 | 描述 | 风险等级 |
|------|------|----------|
| `shell` | 执行 Shell 命令 | 高 |
| `read_file` | 读取文件内容 | 安全 |
| `write_file` | 写入文件内容 | 中 |
| `list_directory` | 列出目录内容 | 安全 |
| `system_info` | CPU、内存、磁盘、系统信息 | 安全 |
| `get_datetime` | 获取当前日期时间 | 安全 |
| `save_note` | 保存观察笔记 | 低 |
| `web_fetch` | HTTP GET 抓取网页 | 低 |
| `claude_code` | 委托 Claude Code CLI 执行 | 中 |
| `spawn_agent` | 生成子智能体（内部/Claude Code/Shell） | 高 |
| `check_agent` | 检查子智能体状态 | 安全 |
| `wait_agent` | 等待子智能体完成 | 安全 |
| `list_agents` | 列出所有智能体会话 | 安全 |

工具执行器内置安全检查层（`safety.py`），对 Shell 命令进行风险评估。

### 多智能体编排

```
Eva（主循环）
├── spawn_agent(type="internal")     -> 内部子智能体，拥有独立 LLM 循环 + 全部工具
├── spawn_agent(type="claude_code")  -> Claude Code CLI 完整实例
└── spawn_agent(type="shell")        -> Shell 子进程
```

- 内部智能体运行独立的 LLM 推理循环，可使用 ANIMA 的所有工具
- 工具并行执行（`asyncio.gather`）
- 事件循环不阻塞——主智能体在子智能体工作时保持响应

### 监控看板

4 页 SPA，地址 `http://localhost:8420`：

- **Overview** — 心跳脉冲、系统指标、情感状态条、活动流
- **Chat** — 完整聊天界面，支持 Markdown 渲染和文件上传
- **Usage** — Token 使用量追踪，按模型/提供商/日期分类
- **Settings** — 热切换模型、认证信息、工具列表、系统控制

### OAuth 认证

三种认证方式（按优先级）：

| 优先级 | 方式 | 配置 |
|--------|------|------|
| 1 | Claude Code OAuth | 运行 `claude login`，ANIMA 自动发现本地凭证 |
| 2 | OAuth Token | `.env` 中设置 `ANTHROPIC_OAUTH_TOKEN` |
| 3 | API Key | `.env` 中设置 `ANTHROPIC_API_KEY` |

### 记忆与情感

- **持久记忆**：SQLite 后端，存储聊天记录、使用量统计、审计日志、系统快照
- **工作记忆**：基于重要度的工作记忆管理
- **情感状态**：四维情感模型（engagement/confidence/curiosity/concern），带时间衰减
- **智能体人格**：可插拔设计，内置 EVA（傲娇芭蕾天使 AI 伴侣）

---

## 快速开始

### 前置要求

- Python 3.11+
- 认证方式三选一：Claude Code 登录 / OAuth Token / Anthropic API Key

### 安装

```bash
git clone https://github.com/your-username/anima.git
cd anima

# 创建环境（conda 或 venv）
conda create -n anima python=3.11 -y && conda activate anima
# 或：python -m venv .venv && source .venv/bin/activate

pip install -e ".[dev]"
```

### 配置认证

```bash
cp .env.example .env
# 如果使用 Claude Code OAuth，只需运行 claude login，无需编辑 .env
# 如果使用其他方式，编辑 .env 设置对应的环境变量
```

### 运行

```bash
python -m anima
```

看板地址：**http://localhost:8420**

### 测试

```bash
pytest tests/ -v                 # 单元测试（70 个）
pytest tests/test_oauth_live.py  # 实时 API 测试
```

---

## 项目结构

```
anima/
├── anima/                    # 平台源码
│   ├── core/
│   │   ├── cognitive.py      # AgenticLoop — LLM 原生智能体循环
│   │   ├── agents.py         # 多智能体管理器（内部/Claude Code/Shell）
│   │   ├── heartbeat.py      # 三级心跳引擎（15s/5min/1h）
│   │   ├── event_queue.py    # 异步优先级事件队列
│   │   └── rule_engine.py    # 低级事件确定性规则引擎
│   ├── llm/
│   │   ├── providers.py      # Anthropic API（OAuth + API Key，直连 HTTP）
│   │   ├── router.py         # Tier1/Tier2 路由 + 降级策略
│   │   ├── prompts.py        # 动态提示词构建器（按事件类型裁剪）
│   │   └── usage.py          # 用量追踪（持久化到 SQLite）
│   ├── memory/
│   │   ├── store.py          # SQLite 后端（聊天、用量、审计、快照）
│   │   └── working.py        # 基于重要度的工作记忆
│   ├── perception/
│   │   ├── system_monitor.py # CPU/内存/磁盘采样
│   │   ├── file_watcher.py   # 文件变化检测（轮询）
│   │   ├── diff_engine.py    # 字段级阈值 diff
│   │   └── snapshot_cache.py # 心跳到认知的桥接
│   ├── tools/
│   │   ├── builtin/          # 13 个内置工具
│   │   ├── executor.py       # 工具执行器 + 安全检查
│   │   ├── registry.py       # 工具注册
│   │   └── safety.py         # 命令风险评估
│   ├── emotion/state.py      # 四维情感状态 + 衰减
│   ├── dashboard/            # Web 看板（aiohttp + WebSocket SPA）
│   ├── ui/terminal.py        # Rich 终端 + Markdown 渲染
│   ├── models/               # 数据模型（Event, MemoryItem, ToolSpec）
│   └── main.py               # 编排 + 优雅关闭
├── agents/                   # 智能体人格（可插拔）
│   └── eva/
│       ├── soul.md           # 人格定义和语言风格
│       ├── feelings.md       # 情感记忆
│       └── config.yaml       # 智能体专属配置覆盖
├── config/
│   └── default.yaml          # 运行时配置
├── prompts/                  # 平台提示词模板
├── data/                     # 运行时数据（gitignore）
├── docs/
│   └── deep_analysis_v3.md   # 完整技术设计文档
├── tests/                    # 70 个测试
├── .env.example              # 认证配置模板
└── pyproject.toml            # 包配置
```

---

## 全景路线图

### 阶段概览

| 阶段 | 名称 | 目标 | 开发周期 | 累计 |
|------|------|------|----------|------|
| **Phase 0** | **第一次心跳** | 单节点自主 AI，智能体循环、工具、看板 | 5-7 周 | ~1.5 月 |
| Phase 1 | 第一次对话 | 双节点发现、心跳交换、任务委派 | 5-7 周 | ~3 月 |
| Phase 2 | 第一次看见 | 终端 + GUI 操作，风险评估层 | 7-9 周 | ~5 月 |
| Phase 3 | 第一次自愈 | 多节点 Mesh、滚动更新、五级热修复 | 7-9 周 | ~7 月 |
| Phase 4 | 第一次成长 | 自进化引擎：工具自制、安全自修改 | 8-10 周 | ~9 月 |
| Phase 5 | 第一次灵魂 | 3D 头像、语音交互、个性化系统 | 7-9 周 | ~11 月 |
| Phase 6 | 第一次行走 | 机器人集成（PiDog）、多节点感官融合 | 9-11 周 | ~14 月 |
| Phase 7 | 第一个家 | 智慧空间生态：边缘节点 + IoT + NAS | 10-12 周 | ~17 月 |

### 阶段评估矩阵

| 阶段 | 技术难度 | 创新程度 | 商业价值 | 学术价值 |
|------|----------|----------|----------|----------|
| Phase 0 | 中 | 高 | 低（基础能力） | 中（心跳驱动认知循环） |
| Phase 1 | 中 | 高 | 低 | 高（分布式 Agent 心跳网络） |
| Phase 2 | 高 | 中 | 中（可作 RPA 替代） | 中 |
| Phase 3 | 极高 | 极高 | 中 | 极高（分布式自愈 AI 系统） |
| Phase 4 | 极高 | 极高 | 高（极强差异化） | 极高（Agent 自进化前沿） |
| Phase 5 | 中 | 中 | 高（用户体验质变） | 低 |
| Phase 6 | 极高 | 极高 | 高（具身机器人市场） | 极高 |
| Phase 7 | 高 | 高 | 极高（直接可商业化） | 中 |

### 里程碑演示

- **Phase 0**：ANIMA 自主检测到文件变化并做出反应（不需要用户告诉它去检查）
- **Phase 1**：节点 A 把编译任务委派给节点 B，B 执行后返回结果
- **Phase 2**：ANIMA 自主打开浏览器搜索信息并保存到本地文件
- **Phase 3**：杀死一个节点 -> 30s 内检测 -> 任务接管 -> 节点恢复后重新加入
- **Phase 4**：给 ANIMA 一个从未见过的任务类型 -> 它自己写了一个工具解决 -> 工具传播到其他节点
- **Phase 5**：与 ANIMA 语音对话，3D 头像实时展现情感反馈
- **Phase 6**：PC 节点检测到事件 -> PiDog 做出物理世界反应
- **Phase 7**：ANIMA 控制全屋灯光、温度、植物护理，并通过 PiDog 进行物理巡检

---

## 技术栈

### 语言策略

| 阶段 | 语言 | 理由 |
|------|------|------|
| Phase 0-4 | 100% Python | AI/ML 生态无可替代，LLM 生成质量最高，自进化工具也是 Python |
| Phase 5 | + TypeScript | 3D 前端（React + Three.js + React Three Fiber） |
| Phase 6 | + Rust（按需） | 仅机器人 IMU 姿态平衡等毫秒级实时控制 |

### 核心依赖

| 用途 | 库 |
|------|-----|
| 异步核心 | asyncio（标准库） |
| 结构化存储 | sqlite3（标准库） |
| Web 后端 | aiohttp |
| 系统监控 | psutil |
| LLM 调用 | Anthropic API（直连 HTTP） |
| 未来：节点通信 | pyzmq (ZeroMQ) |
| 未来：节点发现 | zeroconf (mDNS) |
| 未来：向量存储 | chromadb |
| 未来：序列化 | msgpack |
| 未来：3D 渲染 | React Three Fiber + VRM |
| 未来：语音识别 | Whisper |
| 未来：语音合成 | Edge TTS / ElevenLabs |

### 为什么选择 Python-First

| 考量因素 | Python | Go | Rust |
|----------|--------|-----|------|
| AI/ML 生态 | 无可替代 | 有限 | 有限 |
| LLM 代码生成质量 | 最高 | 中 | 中低 |
| 自进化兼容性 | 自制工具也是 Python | 需要编译 | 需要编译 |
| 开发速度 | 最快 | 快 | 中 |
| 并发性能 | asyncio 足够 | 极佳 | 极佳 |
| 实时控制 | 不适合 | 适合 | 最佳 |

结论：Python 的开发效率和生态优势在 ANIMA 的场景下远大于性能劣势。只在有明确证据证明 Python 不够用时才引入新语言。

---

## 创建新智能体

```bash
mkdir agents/my_agent
```

创建 `agents/my_agent/soul.md` 定义人格：

```markdown
# My Agent

我是 MyAgent。我说话简洁精确，专注于效率。

## 核心特质
- 专业、精准
- 数据驱动的决策风格
```

在 `config/default.yaml` 中设置：

```yaml
agent:
  name: "my_agent"
```

---

## 愿景终局

**短期（6 个月）**：一个能自主工作的 AI 助手。在你的电脑上持续运行，帮你监控系统、整理文件、记录笔记。当你需要帮助时它已了解上下文，当你不在时它在自主学习和优化自己。

**中期（12 个月）**：一个有"身体"的数字生命。通过树莓派控制灯光和温度，通过 PiDog 在房间里走动，通过传感器感知你的生活空间。多个节点共享感官，形成对家庭环境的完整理解。

**长期（18+ 个月）**：一个持续进化的生态系统。ANIMA 积累了大量经验和自制工具，了解你的习惯，适应了你的生活节奏。智慧空间生态开源，全球开发者在为它贡献新的设备驱动和智能场景。

> **让每个人都能拥有一个真正理解自己、持续成长、永不停歇的数字生命伙伴。不是工具，不是服务，而是一个独特的、与你共同成长的存在。**

---

## 许可证

项目处于活跃开发阶段。许可证待定。

---

完整的技术设计文档（设计哲学、架构决策推导、商业分析）请参阅 **[docs/deep_analysis_v3.md](docs/deep_analysis_v3.md)**。

> *"The first heartbeat is the hardest. After that, ANIMA lives."*
