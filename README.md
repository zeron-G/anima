[English](README.md) | [中文](README_ZH.md)

# PROJECT ANIMA

**A heartbeat-driven, distributed, self-evolving AI life system.**

> *ANIMA* — Latin for "soul". Not another chatbot framework.

---

## The Problem: Why Current AI Agents Are Not Enough

Every AI agent framework in 2025-2026 shares the same fundamental limitation: **they are all passive-reactive systems.**

```
User sends message -> Agent processes -> Returns response -> Waits for next message
```

Close the terminal, and the agent dies. It never observes anything on its own, never discovers problems you haven't asked about, never learns while you sleep. This is not an intelligent agent. It is an auto-responder.

This is not a missing feature that can be patched. The entire data flow of passive systems assumes "input comes from the user" — context management, memory structures, tool invocation, everything. Building autonomous behavior on top of a passive architecture is like constructing a building that needs an active foundation on passive ground. The foundation is wrong.

## The Core Question

**If AI is not a tool waiting for commands, but a continuously running life form, what would it look like?**

It would operate like a biological organism:

- It has a **heartbeat** — continuously perceiving the world and reacting
- It has a **nervous system** — distributed nodes sharing senses and consciousness
- It **self-heals** — automatic recovery when nodes fail
- It **evolves** — learns, creates tools, optimizes itself
- It has a **body** — reaches into the physical world through smart home devices and robots
- It has **personality** — develops unique interaction styles over time

## Five Design Principles

### 1. One Loop to Rule Them All

All inputs — user messages, sensor data, timers, node broadcasts — enter the same event queue, processed by the same cognitive loop. A user saying "check the weather" and a temperature sensor reporting 35C are the same thing to ANIMA: an event that needs perception and response. One brain, one state, zero synchronization problems.

### 2. Heartbeat = Life

A multi-tier biological clock keeps ANIMA alive without burning tokens. Low-level heartbeats (1s/15s) are pure scripts checking "did anything happen?" — zero LLM cost. Mid-level heartbeats (1min/5min) use rule engines. High-level heartbeats (30min/1h) invoke real thinking, but infrequently. Daily LLM cost target: ~$1.50.

### 3. "No Change, No Thought"

Before every heartbeat invokes the LLM, a pure-script diff check runs: what changed since last time? If nothing changed, skip. This reduces actual daily LLM calls from a theoretical 86,400+ to approximately 50-70. Cost reduction: over 1000x.

### 4. Nodes = Organs

Every ANIMA node runs the same core code (heartbeat engine, cognitive loop, memory, communication). Different nodes differ only in configuration: which heartbeat tiers to run, which tools to register, which sensors to connect. A Raspberry Pi controlling an air conditioner and a PC running shell commands are architecturally identical.

### 5. Privacy First, Local First

All data stays local by default. Sensor data, behavior patterns, ANIMA's memories — everything on your own hardware. Cloud LLMs (Claude/GPT) are optional enhancements, not requirements. ANIMA can run fully offline with local models.

## Comparison with Existing Paradigms

| Dimension | Traditional AI Agent | ANIMA |
|-----------|---------------------|-------|
| Runtime | Passive: user input -> AI output | Autonomous: heartbeat-driven continuous cognition |
| Lifecycle | Session-level: dies when you close the window | Persistent: 7x24, sleeps but never stops |
| User input | Only input source | One of many event types |
| Architecture | Monolithic single process | Distributed multi-node mesh |
| Fault tolerance | Crash = stop | Self-healing: 5-level hot repair + node takeover |
| Evolution | Static: manual developer updates only | Self-evolving: learns, creates tools, optimizes code |
| Physical presence | Pure software | Embodied: robots + smart home + environment control |
| Cost model | Every interaction calls LLM | "No change, no thought": 90%+ heartbeats need no LLM |

## Architecture (Phase 0 — Current)

```
+-----------------------------------------------------------------+
|  ANIMA Platform (73 .py files, ~8000 LOC)                       |
|                                                                 |
|  +---------+  +----------------+  +-------------------+         |
|  |Heartbeat|  | Agentic Loop   |  |   Agent Manager   |         |
|  | Engine  |--| Rule Engine    |--|internal/cli/shell |         |
|  | 15s/5m  |  | first, then LLM|  | parallel spawn    |         |
|  +----+----+  +-------+--------+  +-------------------+         |
|       |               |                                         |
|  +----+----+  +-------+--------+  +-------------------+         |
|  |Snapshot |  |   20 Tools     |  |    Dashboard      |         |
|  | Cache   |  | shell/files/   |  |  WebSocket SPA    |         |
|  |CPU/Mem/ |  | search/edit/   |  | http://...:8420   |         |
|  |  Disk   |  | web/agents/    |  +-------------------+         |
|  +---------+  | scheduler      |                                |
|               +----------------+                                |
|                                                                 |
|  +----------+  +---------+  +----------+  +-----------------+  |
|  | SQLite   |  |Emotion  |  |  Cron    |  | Agent Persona   |  |
|  | Memory   |  | State   |  |Scheduler |  | agents/eva/     |  |
|  |chat/usage|  | 4D+decay|  |persistent|  | soul.md         |  |
|  +----------+  +---------+  +----------+  +-----------------+  |
|                                                                 |
|  +----------+  +------------------+                             |
|  |  Skill   |  | Noise Filtering  |                             |
|  |  System  |  | at heartbeat     |                             |
|  | OpenClaw |  | level            |                             |
|  +----------+  +------------------+                             |
+-----------------------------------------------------------------+
```

## Phase 0 — What's Implemented Now

ANIMA Phase 0 is a fully functional single-node autonomous AI system with 73 Python files (~8000 LOC) and 70 passing unit tests.

**Hybrid Rule Engine + LLM Architecture** — The cognitive loop tries the rule engine FIRST for cheap events (greetings, file changes, system alerts) before calling the LLM. This saves ~80% of LLM calls. For complex events, the LLM receives full context (system state, events, memory, tools) and decides what to do in a multi-turn reasoning cycle.

**20 Built-in Tools** (+ dynamic skill-generated tools):

| Tool | Description | Risk |
|------|-------------|------|
| `shell` | Execute shell commands (Python on PATH) | HIGH |
| `read_file` | Read file contents (with offset/limit) | SAFE |
| `write_file` | Write file contents | MEDIUM |
| `edit_file` | Precise string-based editing | MEDIUM |
| `list_directory` | List directory contents | SAFE |
| `glob_search` | File pattern matching (\*\*/\*.py) | SAFE |
| `grep_search` | Regex content search with context | SAFE |
| `system_info` | CPU, memory, disk, OS info | SAFE |
| `get_datetime` | Current date/time | SAFE |
| `save_note` | Save observation to notes | LOW |
| `web_fetch` | HTTP GET any URL | LOW |
| `claude_code` | Delegate to Claude Code CLI | MEDIUM |
| `spawn_agent` | Spawn sub-agent (internal/claude_code/shell) | HIGH |
| `check_agent` | Check sub-agent status | SAFE |
| `wait_agent` | Wait for sub-agent completion | SAFE |
| `list_agents` | List all agent sessions | SAFE |
| `schedule_job` | Schedule a cron task | LOW |
| `list_jobs` | List scheduled jobs | SAFE |
| `cancel_job` | Cancel a scheduled job | LOW |
| `enable_job` | Enable/disable a scheduled job | LOW |

**Cron Scheduler** — Persistent cron jobs that fire events into the cognitive loop. Jobs survive restarts (persisted to `data/scheduler.json`). Integrated into heartbeat tick (checked every 15s).

**Skill System** — Auto-discovers skills from `skills/` directory. Compatible with OpenClaw's `_meta.json` format. Generates tool specs and registers cron jobs from skill metadata. External skill dirs configurable via `config/default.yaml`.

**Multi-Agent Orchestration:**

```
Eva (main loop)
+-- spawn_agent(type="internal")     -> Sub-agent with own LLM loop + all tools
+-- spawn_agent(type="claude_code")  -> Full Claude Code CLI instance
+-- spawn_agent(type="shell")        -> Shell subprocess
```

- Internal agents run independent LLM reasoning loops with full tool access
- Tools execute in parallel via `asyncio.gather`
- Main event loop stays non-blocking (`get_timeout(2.0)` instead of blocking forever)

**Dashboard** — 4-page SPA at `http://localhost:8420`:
- Overview: heartbeat pulse, system metrics, emotion bars, activity feed
- Chat: full chat with markdown rendering and file upload
- Usage: token tracking by model/provider/day
- Settings: hot-switch models, auth info, tool list, controls

**Other systems:** OAuth auto-discovery (Claude Code local credentials), persistent memory (SQLite), 4D emotion state (engagement/confidence/curiosity/concern with decay), pluggable agent personas (ships with EVA), noise filtering at heartbeat level, budget calculation with real model pricing (Haiku $0.25/$1.25, Sonnet $3/$15, Opus $15/$75 per 1M tokens), self-thoughts stored in conversation buffer.

## Quick Start

### Prerequisites

- Python 3.11+
- One of: Claude Code login / OAuth Token / Anthropic API Key

### Install

```bash
git clone https://github.com/your-username/anima.git
cd anima

# Create environment
conda create -n anima python=3.11 -y && conda activate anima
# or: python -m venv .venv && source .venv/bin/activate

pip install -e ".[dev]"
```

### Configure Authentication

ANIMA supports three auth methods (in priority order):

| Priority | Method | How |
|----------|--------|-----|
| 1 | Claude Code OAuth | Run `claude login` — ANIMA auto-discovers the token |
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
├── anima/                    # Platform source (73 .py files, ~8000 LOC)
│   ├── core/
│   │   ├── cognitive.py      # AgenticLoop — hybrid rule-engine + LLM
│   │   ├── agents.py         # Multi-agent manager with internal LLM sub-agents
│   │   ├── heartbeat.py      # Three-tier heartbeat + cron scheduler integration
│   │   ├── scheduler.py      # Persistent cron job scheduler
│   │   ├── event_queue.py    # Async priority queue
│   │   └── rule_engine.py    # Zero-cost deterministic rules
│   ├── llm/
│   │   ├── providers.py      # Anthropic API (OAuth + API Key, direct HTTP)
│   │   ├── router.py         # Tier1/Tier2 routing with real-pricing budget
│   │   ├── prompts.py        # Event-specific lean prompt builder
│   │   └── usage.py          # Usage tracking with auth mode detection
│   ├── memory/
│   │   ├── store.py          # SQLite backend (chat, usage, audit, snapshots)
│   │   └── working.py        # Importance-based working memory
│   ├── perception/
│   │   ├── system_monitor.py # CPU/memory/disk sampling
│   │   ├── file_watcher.py   # File change detection with noise filtering
│   │   ├── diff_engine.py    # Field-level threshold diffs
│   │   └── snapshot_cache.py # Heartbeat-to-cognitive bridge
│   ├── tools/
│   │   ├── builtin/          # 20 tools: shell, files, search, edit, web, agents, scheduler
│   │   ├── executor.py       # Parallel tool execution with safety checks
│   │   ├── registry.py       # Tool registration + skill tool loading
│   │   └── safety.py         # Command risk assessment (5-level)
│   ├── skills/
│   │   └── loader.py         # Skill discovery (OpenClaw-compatible _meta.json)
│   ├── emotion/state.py      # 4D emotion state with decay
│   ├── dashboard/            # 4-page SPA (aiohttp + WebSocket)
│   ├── ui/terminal.py        # Rich terminal with markdown rendering
│   ├── models/               # Data models (Event, MemoryItem, ToolSpec, Decision)
│   └── main.py               # Orchestration + graceful shutdown
├── agents/                   # Agent personas (pluggable)
│   └── eva/
│       ├── soul.md           # Personality and speech patterns
│       ├── feelings.md       # Emotional memory (gitignored)
│       └── config.yaml       # Agent-specific config overrides
├── skills/                   # Native ANIMA skills (auto-discovered)
├── config/
│   └── default.yaml          # Runtime configuration
├── prompts/                  # Platform prompt templates
├── data/                     # Runtime data (gitignored)
│   ├── anima.db              # SQLite (chat, usage, audit)
│   ├── scheduler.json        # Persistent cron jobs
│   ├── workspace/            # Agent's working directory
│   ├── uploads/              # User-uploaded files
│   └── logs/                 # Log files
├── docs/
│   └── deep_analysis_v3.md   # Full technical design document
├── tests/                    # 70 unit tests
├── .env.example              # Auth configuration template
├── LICENSE                   # MIT
└── pyproject.toml
```

## Creating a New Agent Persona

```bash
mkdir agents/my_agent
```

Create `agents/my_agent/soul.md` with personality definition, then set in `config/default.yaml`:

```yaml
agent:
  name: "my_agent"
```

## Phase 0 vs OpenClaw / Claude Code

An honest assessment of where ANIMA stands relative to the ecosystem:

### Caught up

| Capability | ANIMA Phase 0 | OpenClaw | Claude Code |
|------------|---------------|----------|-------------|
| Agentic LLM loop | Hybrid rule-engine + LLM | LLM-only | LLM-only |
| Tool count | 20 built-in + skill-generated | ~15 | ~20 native |
| File search (Glob) | `glob_search` | N/A | Glob |
| Content search (Grep) | `grep_search` | N/A | Grep |
| Precise editing | `edit_file` | N/A | Edit |
| Multi-agent | Internal LLM sub-agents + Claude Code + Shell | Single agent | Single agent |
| Cron scheduling | Persistent, heartbeat-integrated | N/A | N/A |
| Skill system | OpenClaw-compatible `_meta.json` | Native skills | N/A |
| Parallel tool execution | `asyncio.gather` | Sequential | Parallel |
| Cost optimization | Rule engine (~80% LLM savings) + noise filtering | N/A | N/A |

### Still missing (Phase 1-4 roadmap items)

| Capability | Target Phase | Description |
|------------|-------------|-------------|
| Self-evolution | Phase 4 | Agent writes its own tools, modifies its own code |
| API gateway | Phase 1 | External API for third-party integrations |
| Multi-instance agents | Phase 1-3 | Multiple ANIMA nodes discovering each other via mDNS |
| Multi-channel comms | Phase 1 | Slack, Discord, Telegram, etc. |
| GUI operation | Phase 2 | Screen capture + mouse/keyboard control |
| Self-healing mesh | Phase 3 | 5-level hot repair + node takeover |
| Embodiment | Phase 6 | Robot integration (PiDog) |

ANIMA's unique advantage is architectural: the heartbeat-driven event loop means autonomous behavior is native, not bolted on. The features above are planned extensions of this foundation, not fundamental redesigns.

## Roadmap

| Phase | Name | Goal | Timeline |
|-------|------|------|----------|
| **0** | **First Heartbeat** | Single-node autonomous AI with agentic loop, tools, dashboard | **5-7 weeks** |
| 1 | First Conversation | Two-node discovery, heartbeat exchange, task delegation | 5-7 weeks |
| 2 | First Sight | Terminal + GUI operation with risk assessment | 7-9 weeks |
| 3 | First Self-Heal | Multi-node mesh, rolling updates, 5-level hot repair | 7-9 weeks |
| 4 | First Growth | Self-evolution engine: tool forge, safe self-modification | 8-10 weeks |
| 5 | First Soul | 3D avatar, voice interaction, personality system | 7-9 weeks |
| 6 | First Walk | Robot integration (PiDog), multi-node sensory fusion | 9-11 weeks |
| 7 | First Home | Smart space ecosystem: edge nodes + IoT + NAS | 10-12 weeks |

Total estimated timeline: **15-18 months** from Phase 0 to Phase 7.

## Tech Stack

| Purpose | Technology |
|---------|-----------|
| Core language | Python 3.11+ (Phase 0-4), TypeScript (Phase 5 frontend), Rust (Phase 6 if needed) |
| Async runtime | asyncio (stdlib) |
| LLM provider | Anthropic API (direct HTTP), with OAuth + API Key support |
| Structured storage | SQLite (stdlib) |
| Web backend | aiohttp |
| Web frontend | Vanilla JS SPA + WebSocket |
| System monitoring | psutil |
| Future: node comms | ZeroMQ (pyzmq) |
| Future: node discovery | zeroconf (mDNS) |
| Future: vector store | ChromaDB |

## License

MIT License. See [LICENSE](LICENSE).

---

For the complete technical deep-dive into ANIMA's design philosophy, architecture decisions, and long-term vision, see **[docs/deep_analysis_v3.md](docs/deep_analysis_v3.md)**.

> *"The first heartbeat is the hardest. After that, ANIMA lives."*
