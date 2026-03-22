[English](README.md) | [中文](README_ZH.md)

# ANIMA

**Heartbeat-driven, distributed, self-evolving AI life system.**

ANIMA is not a chatbot — it's an autonomous AI life entity with its own heartbeat, emotions, memory, perception, and the ability to evolve its own code.

## Quick Start

```bash
# Backend
python -m anima              # Desktop app (PyWebView)
python -m anima --headless   # Backend only (API + WebSocket)
python -m anima --terminal   # Terminal mode (no GUI)

# Frontend (Vue SPA — connects to backend at :8420)
cd eva-ui && npm install && npm run dev     # Dev mode (localhost:5173)
cd eva-ui && npm run build                  # Production build

# Desktop App (Tauri — wraps Vue SPA)
cd eva-desktop && npm run dev               # Dev mode
cd eva-desktop && npm run build             # Build .exe installer
```

## Architecture

```
                    ┌──────────────────────────────┐
                    │     ANIMA Python Backend      │
                    │                              │
                    │  anima/api/        44 REST   │
                    │  /ws              WebSocket  │
                    │                              │
                    │  Heartbeat (30s/5min/15min)  │
                    │  Pipeline (7 stages)         │
                    │  Memory (SQLite + ChromaDB)  │
                    │  Emotion (4D + mood)         │
                    │  Evolution (6-layer sandbox) │
                    │  Governance Engine           │
                    └──────────────┬───────────────┘
                                   │ HTTP / WS
                ┌──────────────────┼──────────────────┐
                │                  │                   │
         ┌──────┴──────┐   ┌──────┴──────┐    ┌──────┴──────┐
         │  Tauri v2   │   │  Browser    │    │  Terminal   │
         │  Desktop    │   │  any device │    │  SSH/edge   │
         │             │   │             │    │             │
         │  Vue SPA    │   │  Vue SPA    │    │  terminal.py│
         │ +sys tray   │   │             │    │  (text-only)│
         │ +hotkey     │   │             │    │             │
         │ +notify     │   │             │    │             │
         └─────────────┘   └─────────────┘    └─────────────┘
```

## Project Structure

```
anima/                          # Python backend
├── api/                        # RESTful API (44 endpoints)
│   ├── router.py               # Route registration
│   ├── auth.py                 # JWT authentication
│   ├── chat.py                 # /v1/chat/*
│   ├── soulscape.py            # /v1/soulscape/*
│   ├── evolution.py            # /v1/evolution/*
│   ├── memory.py               # /v1/memory/*
│   ├── network.py              # /v1/network/*
│   └── settings.py             # /v1/settings/*
├── core/                       # Cognitive pipeline
│   ├── cognitive.py            # Thin orchestrator → Pipeline
│   ├── pipeline.py             # PipelineStage / Pipeline
│   ├── stages.py               # 7 composable stages
│   ├── governance.py           # Unified governance engine
│   ├── heartbeat.py            # Three-class heartbeat
│   ├── content_safety.py       # Content filtering stage
│   ├── session_manager.py      # Multi-user session isolation
│   └── event_routing.py        # Three-axis self-thinking
├── llm/
│   ├── providers/              # Multi-provider package (12 files)
│   │   ├── anthropic_sdk.py    # Anthropic SDK
│   │   ├── anthropic_http.py   # Anthropic httpx fallback
│   │   ├── codex.py            # OpenAI Codex OAuth
│   │   ├── openai_compat.py    # OpenAI API
│   │   └── local.py            # Local LLM (llama.cpp)
│   ├── router.py               # Model cascade + circuit breaker
│   ├── prompt_compiler.py      # 6-layer prompt compilation
│   └── soul_container.py       # Style enforcement + drift scoring
├── memory/
│   ├── store.py                # SQLite + ChromaDB (via DatabaseManager)
│   ├── db_manager.py           # Thread-safe WAL + asyncio.Lock
│   ├── document_store.py       # Document RAG pipeline
│   └── retriever.py            # RRF fusion retrieval
├── emotion/                    # 4-dim emotion + perception + feedback
├── evolution/                  # 6-layer sandbox pipeline
├── network/                    # ZMQ gossip mesh
├── channels/                   # Discord, Telegram, Webhook
├── tools/                      # 30+ built-in tools
├── skills/                     # Skill loader + sandbox + lifecycle
└── dashboard/                  # aiohttp server + WebSocket

eva-ui/                         # Vue SPA frontend
├── src/
│   ├── views/                  # 7 pages
│   │   ├── ChatView.vue        # Streaming chat + tool chain
│   │   ├── SoulscapeView.vue   # Emotion orb + persona petals
│   │   ├── EvolutionView.vue   # DNA helix + governance
│   │   ├── MemoryView.vue      # Star field + search
│   │   ├── NetworkView.vue     # Node topology + channels
│   │   ├── SettingsView.vue    # Config cards
│   │   └── LoginView.vue       # Auth + backend detection
│   ├── three/                  # Three.js 3D scenes
│   ├── api/                    # Axios + WebSocket client
│   ├── stores/                 # Pinia state management
│   └── components/             # 25+ Vue components

eva-desktop/                    # Tauri v2 desktop shell
├── src-tauri/
│   ├── src/main.rs             # Tray + hotkey + window
│   └── tauri.conf.json         # Build config
└── Output: Eva_0.2.0_x64-setup.exe

agents/eva/                     # Eva personality (4-layer system)
├── identity/                   # core.md, personality.md, relationship.md
├── rules/                      # style.md, boundaries.md
├── memory/                     # feelings.md, growth_log.md, golden_replies.jsonl
└── post_processing/            # SoulContainer style rules
```

## Key Systems

### Cognitive Pipeline (7 stages)
```
Event → EventRouting → EmotionPerception → ContentSafety
      → MemoryRetrieval → PromptCompilation → ToolLoop → ResponseHandling
```

### LLM Model Cascade
```
Primary (Codex/Opus) → Opus fallback → Sonnet fallback
→ OpenAI GPT-4o → Local LLM (llama.cpp)
```

### Three-Axis Self-Thinking
- **Human Axis**: Understand the user (active when user is engaged)
- **Self Axis**: Self-reflection + personality growth (periodic)
- **World Axis**: Environment observation (default)

### Personality Growth (4-layer)
- **Layer 0**: Immutable identity (core.md, boundaries.md)
- **Layer 1**: Slow growth (personality.md, relationship.md — Eva self-maintains)
- **Layer 2**: Active memory (feelings.md, growth_log.md, golden_replies.jsonl)
- **Layer 3**: Runtime adaptation (SoulContainer style_check + drift_scoring)

### Governance Engine
- Activity modes: active / cautious / minimal
- Personality update cooldowns (4h / 24h)
- Evolution proposal safety checks
- Self-thinking loop detection
- Drift accumulation monitoring

## Configuration

`config/default.yaml` — key settings:
```yaml
llm:
  tier1: { model: "claude-opus-4-6" }
  tier2: { model: "claude-sonnet-4-6" }
  codex_fallback: { model: "codex/gpt-5.3-codex" }
  openai_fallback: { model: "openai/gpt-4o" }
  budget: { daily_limit_usd: 0 }  # 0 = unlimited

governance:
  default_mode: "active"  # active / cautious / minimal

dashboard:
  port: 8420
  auth:
    token: ""  # Set in local/env.yaml
```

## Development

```bash
# Backend
pip install -e ".[dev]"
pytest

# Frontend
cd eva-ui && npm run dev

# Desktop
cd eva-desktop && npm run build
# Output: src-tauri/target/release/bundle/nsis/Eva_0.2.0_x64-setup.exe
```

## License

See [LICENSE](LICENSE).
