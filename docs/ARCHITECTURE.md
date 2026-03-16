# ANIMA Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     ANIMA Process                            │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Heartbeat│  │Cognitive │  │ Terminal  │  │Dashboard │   │
│  │ Engine   │──│  Loop    │  │    UI     │  │  Server  │   │
│  │(3 tiers) │  │(LLM+Rule)│  │  (rich)  │  │ (aiohttp)│   │
│  └────┬─────┘  └────┬─────┘  └──────────┘  └────┬─────┘   │
│       │              │                           │          │
│  ┌────┴─────┐  ┌────┴─────┐               ┌────┴─────┐   │
│  │Perception│  │  Memory  │               │  Desktop │   │
│  │(file,sys)│  │(SQLite)  │               │ Frontend │   │
│  └──────────┘  └──────────┘               └──────────┘   │
│       │              │                                      │
│  ┌────┴─────┐  ┌────┴─────┐  ┌──────────┐                │
│  │ Emotion  │  │  Tools   │  │ Evolution│                │
│  │ (4-dim)  │  │ (13+)    │  │  Engine  │                │
│  └──────────┘  └──────────┘  └──────────┘                │
│                                                              │
│  ┌──────────────────────────┐  ┌─────────────────────┐     │
│  │   Distributed Network    │  │     Channels        │     │
│  │ (ZMQ Gossip + Sync)      │  │ (Discord, Webhook)  │     │
│  └──────────────────────────┘  └─────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

## Configuration Hierarchy

```
config/default.yaml          ← Project defaults (committed)
       ↓ merge
agents/eva/config.yaml       ← Agent personality overrides
       ↓ merge
local/env.yaml               ← Machine-specific (gitignored)
       ↓ load
.env                         ← Secrets (gitignored)
```

## Startup Flow

```
__main__.py
  → desktop/app.py::launch_desktop()
    → singleton.acquire_lock()
    → Thread: _run_backend()
      → main.py::run()
        → load_config()              # default + agent + local
        → init subsystems            # 20+ objects wired together
        → dashboard.start()          # aiohttp on :8420
        → heartbeat.start()          # 3 async loops
        → cognitive.run()            # event consumer loop
        → terminal.start()           # input thread
        → wait shutdown_event
    → webview.start()                # blocks until window closed
    → os._exit(0)                    # kill everything
```

## Event Flow

```
Source (terminal/dashboard/discord/heartbeat/file_watcher)
  → EventQueue.put(Event)
  → AgenticLoop.run() picks from queue
    → RuleEngine.evaluate(event)
      ├── Match → Decision → execute (no LLM cost)
      └── No match → LLM agentic loop:
          → PromptBuilder.build_system_prompt()
          → event_router.event_to_message(event)
          → event_router.pick_tier(event) → tier 1 or 2
          → LLMRouter.call_with_tools(messages, tools)
            → Multi-turn loop:
              ├── Text response → output
              └── Tool call → ToolExecutor.execute() → append result → loop
          → Save to memory
          → Output callback → terminal + dashboard + channels
```

## Evolution → Reload → Main Data Flow

This is the self-modification cycle — the most complex cross-file flow:

```
                    ┌──────────────────┐
                    │   Heartbeat      │
                    │  (major tick)    │
                    └────────┬─────────┘
                             │ SELF_THINKING event
                             │ with evolution_prompt
                             ▼
                    ┌──────────────────┐
                    │  Cognitive Loop  │
                    │                  │
                    │  1. LLM proposes │
                    │  2. LLM executes │
                    │     (edit files) │
                    │  3. Run tests    │
                    │  4. git commit   │
                    └────────┬─────────┘
                             │ calls _maybe_trigger_reload()
                             ▼
                    ┌──────────────────┐
                    │  ReloadManager   │
                    │  (core/reload.py)│
                    │                  │
                    │  1. Save state:  │
                    │   - conversation │  → data/evolution_checkpoint.json
                    │   - emotion      │
                    │   - tick_count   │
                    │   - reason       │
                    │  2. Set flag:    │
                    │   restart_       │
                    │   requested=True │
                    └────────┬─────────┘
                             │ flag detected by
                             ▼
                    ┌──────────────────┐
                    │    main.py       │
                    │  _watch_reload() │
                    │                  │
                    │  1. Detect flag  │
                    │  2. shutdown_    │
                    │     event.set()  │
                    │  3. Graceful     │
                    │     shutdown     │
                    │  4. Return True  │  ← "restart requested"
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  main_entry()    │
                    │                  │
                    │  while True:     │
                    │    restart =     │
                    │      run()       │
                    │    if restart:   │
                    │      sleep(2)    │
                    │      continue    │  ← Python re-imports changed .py files
                    │    else: break   │
                    └────────┬─────────┘
                             │ next run() call
                             ▼
                    ┌──────────────────┐
                    │    run() again   │
                    │                  │
                    │  1. Load config  │
                    │  2. Init systems │
                    │  3. Load         │
                    │     checkpoint   │  ← reads data/evolution_checkpoint.json
                    │  4. Restore:     │
                    │   - conversation │
                    │   - emotion      │
                    │   - tick_count   │
                    │  5. Continue     │
                    │     running      │
                    └──────────────────┘
```

### State Files
| File | Written by | Read by | Content |
|------|-----------|---------|---------|
| `data/evolution_checkpoint.json` | ReloadManager | main.py | conversation, emotion, tick_count, reason |
| `data/evolution_state.json` | EvolutionState | evolution.py | loop count, history (last 30) |
| `data/anima.lock` | singleton.py | singleton.py | PID of running process |
| `data/watchdog_heartbeat.json` | heartbeat.py | watchdog.py | timestamp, PID |

## Watchdog (External Repair Loop)

```
watchdog.py (separate process)
  │
  ├── Start ANIMA subprocess
  ├── Wait 30s → health check
  │   ├── OK → monitor loop
  │   └── FAIL → invoke Claude Code CLI → fix → restart
  │
  └── Monitor loop (every 5s):
      ├── Process alive? → if dead, diagnose crash → fix → restart
      ├── Heartbeat fresh? → if stale 3min, kill → restart
      └── Error patterns? → if 5+ same error, invoke Claude Code → fix
```

## Code Module Responsibilities

### Refactored cognitive system (3 files):
| File | Responsibility |
|------|---------------|
| `core/cognitive.py` | LLM agentic loop execution, tool calls |
| `core/event_router.py` | Event → message formatting, tier selection, self-thinking task pool |
| `core/conversation.py` | Conversation buffer: add, trim, save to DB, restore from checkpoint |

### Dashboard (legacy):
| File | Status |
|------|--------|
| `dashboard/page.py` | **Legacy** — 1536 lines of Python string HTML. Do not add to this. |
| `dashboard/server.py` | Active — aiohttp API + WebSocket. Serves both legacy dashboard and desktop frontend. |
| `desktop/frontend/` | **Active** — all new UI work goes here. |
