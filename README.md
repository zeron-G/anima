# ANIMA

**A heartbeat-driven autonomous AI life system with pluggable agent personas.**

ANIMA is a platform for persistent AI entities. It provides the heartbeat engine, agentic loop, memory, tools, and multi-agent orchestration. The agent persona (personality, speech, emotional memory) is fully customizable.

Currently ships with **EVA** — a tsundere ballet angel AI companion.

[中文文档](#anima-中文文档)

---

## What makes ANIMA different

| vs Claude Code | vs ChatGPT | vs OpenClaw |
|---|---|---|
| Runs **continuously** — heartbeat, not conversations | Has **persistent memory** across sessions | Has **pluggable personas** — not tied to one identity |
| **Self-driving** — proactively scans environment | Can **use tools** autonomously (shell, files, web) | **Multi-agent** orchestration with hierarchy |
| **Emotional state** that evolves over time | Runs **locally** on your machine | **Dashboard** with real-time monitoring |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  ANIMA Platform                                         │
│                                                         │
│  ┌─────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │Heartbeat│  │ Agentic Loop │  │   Agent Manager   │  │
│  │ Engine  │──│  (LLM+Tools) │──│ internal/cli/shell│  │
│  │ 15s/5m  │  │  multi-turn  │  │  parallel spawn   │  │
│  └────┬────┘  └──────┬───────┘  └───────────────────┘  │
│       │              │                                   │
│  ┌────┴────┐  ┌──────┴───────┐  ┌───────────────────┐  │
│  │Snapshot │  │  13 Tools    │  │    Dashboard      │  │
│  │ Cache   │  │ shell/files/ │  │  WebSocket SPA    │  │
│  │CPU/Mem/ │  │ web/agents/  │  │  http://...:8420  │  │
│  │  Disk   │  │ claude_code  │  └───────────────────┘  │
│  └─────────┘  └──────────────┘                          │
│                                                         │
│  ┌──────────┐  ┌─────────┐  ┌────────────────────────┐ │
│  │ SQLite   │  │Emotion  │  │   Agent Persona        │ │
│  │ Memory   │  │ State   │  │   agents/eva/soul.md   │ │
│  │chat/usage│  │ 4D+decay│  │   agents/eva/feelings  │ │
│  └──────────┘  └─────────┘  └────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

## Quick Start

### Prerequisites

- Python 3.11+
- One of: Claude Code login (`claude login`) / OAuth Token / Anthropic API Key

### Install

```bash
git clone https://github.com/your-username/anima.git
cd anima

# Create environment (conda or venv)
conda create -n anima python=3.11 -y && conda activate anima
# or: python -m venv .venv && source .venv/bin/activate

pip install -e ".[dev]"
```

### Configure Authentication

ANIMA supports three auth methods (in priority order):

| Priority | Method | How |
|---|---|---|
| 1 | Claude Code OAuth | Just run `claude login` — ANIMA auto-discovers the token |
| 2 | OAuth Token | Set `ANTHROPIC_OAUTH_TOKEN` in `.env` |
| 3 | API Key | Set `ANTHROPIC_API_KEY` in `.env` |

```bash
cp .env.example .env
# Edit .env if you need method 2 or 3
```

### Run

```bash
python -m anima
```

Dashboard opens at **http://localhost:8420**

### Test

```bash
pytest tests/ -v                 # Unit tests (70)
pytest tests/test_oauth_live.py  # Live API test
```

## Project Structure

```
anima/
├── anima/                    # Platform source
│   ├── core/
│   │   ├── cognitive.py      # AgenticLoop — LLM-native agentic loop
│   │   ├── agents.py         # Multi-agent manager (internal/claude_code/shell)
│   │   ├── heartbeat.py      # Three-tier heartbeat (15s/5m/1h)
│   │   ├── event_queue.py    # Async priority queue
│   │   └── rule_engine.py    # Deterministic rules for low-level events
│   ├── llm/
│   │   ├── providers.py      # Anthropic API (OAuth + API Key, direct HTTP)
│   │   ├── router.py         # Tier1/Tier2 routing with fallback
│   │   ├── prompts.py        # Dynamic prompt builder (lean, event-specific)
│   │   └── usage.py          # Usage tracking (persisted to SQLite)
│   ├── memory/
│   │   ├── store.py          # SQLite backend (chat, usage, audit, snapshots)
│   │   └── working.py        # Importance-based working memory
│   ├── perception/
│   │   ├── system_monitor.py # CPU/memory/disk sampling
│   │   ├── file_watcher.py   # File change detection (polling)
│   │   ├── diff_engine.py    # Field-level threshold diffs
│   │   └── snapshot_cache.py # Heartbeat→cognitive bridge
│   ├── tools/
│   │   ├── builtin/          # 13 tools: shell, files, web, agents, claude_code
│   │   ├── executor.py       # Tool execution with safety checks
│   │   ├── registry.py       # Tool registration
│   │   └── safety.py         # Command risk assessment
│   ├── emotion/state.py      # 4D emotion (engagement/confidence/curiosity/concern)
│   ├── dashboard/            # Web UI (aiohttp + WebSocket SPA)
│   ├── ui/terminal.py        # Rich terminal with markdown rendering
│   ├── models/               # Data models (Event, MemoryItem, ToolSpec)
│   └── main.py               # Orchestration + graceful shutdown
├── agents/                   # Agent personas (pluggable)
│   └── eva/
│       ├── soul.md           # Personality and speech patterns
│       ├── feelings.md       # Emotional memory
│       └── config.yaml       # Agent-specific config overrides
├── config/
│   └── default.yaml          # Runtime configuration
├── prompts/                  # Platform prompt templates
│   ├── system_identity.md    # Operational rules (tool usage, output format)
│   ├── decide.md             # Decision prompt (legacy)
│   └── reflect.md            # Reflection prompt
├── data/                     # Runtime data (gitignored)
│   ├── anima.db              # SQLite (chat, usage, audit)
│   ├── workspace/            # Agent's working directory
│   ├── uploads/              # User-uploaded files
│   ├── logs/                 # Log files
│   └── user_profile.md       # Learned user preferences
├── tests/                    # 70 tests
├── .env.example              # Auth configuration template
├── pyproject.toml            # Package config
└── README.md
```

## Tools (13)

| Tool | Description | Risk |
|---|---|---|
| `shell` | Execute shell commands (Python on PATH) | HIGH |
| `read_file` | Read file contents | SAFE |
| `write_file` | Write file contents | MEDIUM |
| `list_directory` | List directory contents | SAFE |
| `system_info` | CPU, memory, disk, OS info | SAFE |
| `get_datetime` | Current date/time | SAFE |
| `save_note` | Save observation to notes | LOW |
| `web_fetch` | HTTP GET any URL | LOW |
| `claude_code` | Delegate to Claude Code CLI | MEDIUM |
| `spawn_agent` | Spawn sub-agent (internal/claude_code/shell) | HIGH |
| `check_agent` | Check sub-agent status | SAFE |
| `wait_agent` | Wait for sub-agent completion | SAFE |
| `list_agents` | List all agent sessions | SAFE |

## Multi-Agent System

ANIMA can orchestrate sub-agents for parallel/complex work:

```
Eva (main loop)
├── spawn_agent(type="internal", prompt="research X")
│   └── Sub-agent: own LLM loop + all tools, focused task
├── spawn_agent(type="claude_code", prompt="refactor Y")
│   └── Claude Code CLI: full Claude instance
└── spawn_agent(type="shell", prompt="python script.py")
    └── Shell subprocess
```

- `internal` agents run their own LLM agentic loop with ANIMA's tools
- Tools execute in parallel (`asyncio.gather`)
- Event loop is non-blocking — Eva stays responsive while agents work

## Dashboard

4-page SPA at `http://localhost:8420`:

- **Overview** — Heartbeat pulse, system metrics, emotion bars, activity feed
- **Chat** — Full chat with markdown rendering, file upload
- **Usage** — Token tracking by model/provider/day, sortable history
- **Settings** — Hot-switch models, auth info, tools, controls

## Creating a New Agent

```bash
mkdir agents/my_agent
```

Create `agents/my_agent/soul.md`:
```markdown
# My Agent

I am MyAgent. I speak formally and focus on productivity.

## Core traits
- Professional and precise
- Data-driven decision making
```

Set in `config/default.yaml`:
```yaml
agent:
  name: "my_agent"
```

---

# ANIMA 中文文档

**心跳驱动的自主 AI 生命体平台，支持可插拔的智能体人格。**

ANIMA 是持续运行的 AI 实体平台。提供心跳引擎、智能体循环、记忆系统、工具和多智能体编排。智能体人格（性格、语言风格、情感记忆）完全可自定义。

内置智能体：**EVA** — 傲娇芭蕾天使 AI 伴侣。

## 特性

- **持续运行** — 心跳驱动，不是对话式的
- **自驱动** — 主动扫描环境、检测文件变化、监控系统状态
- **13 个工具** — Shell、文件操作、网页抓取、Claude Code 委托、多智能体
- **多智能体编排** — 内部 LLM 子智能体 + Claude Code CLI + Shell 并行执行
- **持久记忆** — SQLite 存储聊天、使用量、审计日志
- **情感系统** — 四维情感状态（engagement/confidence/curiosity/concern）
- **Web 看板** — 4 页 SPA，实时 WebSocket 推送
- **OAuth 认证** — 自动发现 Claude Code 本地凭证

## 快速开始

```bash
git clone https://github.com/your-username/anima.git
cd anima
conda create -n anima python=3.11 -y && conda activate anima
pip install -e ".[dev]"
cp .env.example .env  # 编辑认证配置（或使用 Claude Code 自动发现）
python -m anima       # 启动，看板在 http://localhost:8420
```

### 认证方式（三选一，按优先级）

| 优先级 | 方式 | 配置 |
|---|---|---|
| 1 | Claude Code OAuth | 运行 `claude login`，ANIMA 自动发现 |
| 2 | OAuth Token | `.env` 中设置 `ANTHROPIC_OAUTH_TOKEN` |
| 3 | API Key | `.env` 中设置 `ANTHROPIC_API_KEY` |

### 创建新智能体

```bash
mkdir agents/你的智能体
# 编辑 agents/你的智能体/soul.md 定义人格
# config/default.yaml 中设置 agent.name
```

## 项目结构

```
anima/core/cognitive.py    # 智能体循环（LLM + 工具多轮推理）
anima/core/agents.py       # 多智能体管理（内部/CLI/Shell）
anima/core/heartbeat.py    # 三级心跳（15s/5min/1h）
anima/llm/providers.py     # Anthropic API（OAuth + API Key）
anima/llm/prompts.py       # 动态提示词（按事件类型裁剪）
anima/memory/store.py      # SQLite 后端
anima/tools/builtin/       # 13 个内置工具
anima/dashboard/           # Web 看板（aiohttp + WebSocket）
agents/eva/                # EVA 人格定义
config/default.yaml        # 运行时配置
```

## 多智能体系统

```
Eva（主循环）
├── spawn_agent(type="internal") → 内部 LLM 子智能体，拥有全部工具
├── spawn_agent(type="claude_code") → Claude Code CLI 进程
└── spawn_agent(type="shell") → Shell 子进程
```

- 内部智能体运行独立的 LLM 推理循环，可使用 ANIMA 的所有工具
- 工具并行执行（asyncio.gather）
- 事件循环不阻塞 — Eva 在子智能体工作时保持响应

## 看板

http://localhost:8420，4 页 SPA：

- **Overview** — 心跳脉冲、系统指标、情感状态、活动流
- **Chat** — 聊天 + markdown 渲染 + 文件上传
- **Usage** — Token 按模型/提供商/日期追踪
- **Settings** — 热切换模型、认证信息、工具列表、控制

## 测试

```bash
pytest tests/ -v  # 70 个测试
```
