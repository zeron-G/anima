# ANIMA Architecture Guide

## System Overview

ANIMA is a heartbeat-driven, distributed, self-evolving AI agent with a 4-component cognitive pipeline, multi-tier memory system, and autonomous evolution capability.

## Deployment & Data Model (Phases 0–4)

The runtime is split into three layers (full detail in [REFACTOR.md](REFACTOR.md)):

- **Kernel** — published code + read-only assets (`config/`, `prompts/`, `skills/`,
  and the persona **seed** `agents/_seed`). Resolved via `config.package_root()` /
  `source_tree()`; bundled into the wheel under `anima/_resources/`.
- **Frontend** (`eva-ui/`) — Vue SPA whose backend address comes from build-time env
  (`VITE_API_BASE`/`VITE_WS_BASE`); deployable same-origin or fully split — see
  [DEPLOYMENT.md](DEPLOYMENT.md).
- **ANIMA_HOME** — private user state: `data/` (logs, caches, …) + the live
  persona instance `agents/<name>` + `.env` + `config.yaml`. Memory itself lives
  in Postgres (Neon primary + local failover), not under `data/`. Resolved via
  `config.home_dir()` (`$ANIMA_HOME` → source tree → `~/.anima`).

`anima init` bootstraps a home from the seed. In installed mode (no source tree)
evolution/spawn/self-audit gracefully disable (they need a git repo). The full
path API is REFACTOR.md §4; the HTTP/WS contract is [API.md](API.md); auth is one
JWT middleware over `dashboard.auth.password`.

> Note: the module/line-count tables below predate the Phase 0–4 refactor and are
> indicative only (e.g. `anima/llm/` is now a `providers/` package; `anima/core/`
> adds governance, self_audit, idle_scheduler, scheduler, session_manager, …).
> Treat REFACTOR.md + API.md as authoritative.

## Component Diagram

```
┌─────────────────────────────────────────────────────────┐
│                     ANIMA SYSTEM                         │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌──────────────┐    ┌──────────────┐                   │
│  │  Heartbeat    │    │  Event Queue │                   │
│  │  Engine       │───>│  (Priority)  │                   │
│  │  (3 timers)   │    └──────┬───────┘                   │
│  └──────────────┘           │                            │
│                              ▼                            │
│  ┌───────────────────────────────────────────────┐       │
│  │            Cognitive Loop (Orchestrator)        │       │
│  │  ┌───────────┐ ┌──────────┐ ┌───────────────┐ │       │
│  │  │  Event    │ │  Tool    │ │  Response     │ │       │
│  │  │  Router   │→│  Orch.   │→│  Handler      │ │       │
│  │  └───────────┘ └──────────┘ └───────────────┘ │       │
│  │       ↑              ↑              ↑          │       │
│  │       └──────────────┴──────────────┘          │       │
│  │            CognitiveContext (shared)            │       │
│  └───────────────────────────────────────────────┘       │
│       │              │              │                     │
│  ┌────▼────┐   ┌────▼────┐   ┌────▼────┐                │
│  │ Memory  │   │  LLM    │   │  Tool   │                │
│  │ System  │   │ Router  │   │ System  │                │
│  │ (4-tier)│   │ (cascade)│   │ (30+)  │                │
│  └─────────┘   └─────────┘   └─────────┘                │
│                                                          │
│  ┌──────────┐  ┌───────────┐  ┌────────────┐            │
│  │ Evolution│  │  Gossip   │  │ Dashboard  │            │
│  │ Engine   │  │  Mesh     │  │ + Terminal │            │
│  └──────────┘  └───────────┘  └────────────┘            │
└─────────────────────────────────────────────────────────┘
```

## Module Responsibilities

### Core (anima/core/)
| Module | Lines | Responsibility |
|--------|-------|---------------|
| cognitive.py | ~200 | Thin orchestrator, wires 4 components |
| context.py | ~170 | CognitiveContext dependency container |
| event_routing.py | ~630 | Event → message, tier selection, task pool |
| tool_orchestrator.py | ~400 | Tool schemas, dynamic selection, execution |
| response_handler.py | ~470 | Output routing, memory, emotion, evolution |
| heartbeat.py | ~600 | 3 timers: script/LLM/major |
| event_queue.py | ~90 | Priority async queue |

### Memory (anima/memory/)
Backend is **Postgres + pgvector** (single engine). See [Data layer](#data-layer) below.
| Module | Responsibility |
|--------|---------------|
| store.py | `create_memory_store()` factory → PgMemoryStore |
| pg_store.py | The memory store: episodic/emotion/static/usage/env_catalog/documents |
| pg_db.py | Postgres connection manager: one conn + lock, Neon→local failover, runtime reconnect |
| pg_sync.py | Replica sync: warm local while online, replay local→primary on reconnect |
| pg_schema.sql | Schema (episodic + `vector(1536)` + HNSW; emotion; static_knowledge; documents; …) |
| retriever.py | 4-tier RRF fusion retrieval |
| embedder.py | OpenAI `text-embedding-3-small` (1536-dim); legacy local ST fallback |
| decay.py | Time-weighted importance decay + consolidation |
| summarizer.py | Conversation compression |
| importance.py | Dynamic memory importance scoring |

#### Data layer

ANIMA runs on a **single engine: Postgres + pgvector** — no SQLite, no ChromaDB.

- **Topology**: a cloud **primary** (`DATABASE_URL`, e.g. Neon) plus a **local
  failover** (`LOCAL_DATABASE_URL`, a local Postgres). `PgDatabaseManager`
  connects to the primary first and falls back to local. Connection strings are
  secrets (`.env` / secret store); `localhost` is pinned to `127.0.0.1` to avoid
  a Windows IPv6 connect stall.
- **One connection, one lock**: a psycopg3 connection isn't thread-safe, so all
  reads/writes funnel through a single lock; async methods wrap the sync core in
  `asyncio.to_thread`.
- **Runtime failover**: every query routes through a locked runner that, on a
  dropped connection mid-operation, reconnects (primary→local) and retries once —
  a cloud blip degrades to local instead of crashing.
- **Replica sync** (`pg_sync.PgSyncManager`, every `memory.sync_interval_s`):
  while **online** it warms the local replica (primary→local) so local is always
  a recent backup; while **offline** it waits for the primary, replays
  local-only writes (local→primary), then switches the live connection back.
  Reconciliation is per append-only table by `MAX(ts)` watermark +
  `INSERT … ON CONFLICT DO NOTHING` (idempotent). Node-local tables
  (`env_catalog`) are not synced.
- **Embeddings**: only `episodic_memories` and `documents` are vectorized, via
  OpenAI `text-embedding-3-small` (1536-dim) into a pgvector `vector(1536)`
  column with an HNSW cosine index. Without `OPENAI_API_KEY`, recall degrades to
  keyword search (ILIKE). Emotion is a numeric time-series (no vectors); persona
  prose stays in files (durability via backups, not the DB).

### LLM (anima/llm/)
| Module | Responsibility |
|--------|---------------|
| providers.py | Anthropic SDK + httpx fallback + OpenAI compat + streaming |
| router.py | Tier cascade + circuit breaker + budget + streaming |
| prompt_compiler.py | 6-layer compilation with TokenBudget |
| token_budget.py | Multi-tier token counting + layer allocation |
| structured.py | Pydantic-driven structured output |
| soul_container.py | Post-processing (tone, emoji, catchphrase) |
| lorebook.py | Keyword-triggered context injection |

### Safety (anima/tools/)
| Module | Responsibility |
|--------|---------------|
| safe_subprocess.py | Unified command execution (prevents injection) |
| safety.py | Structural command analysis (shlex + flags) |
| executor.py | Per-tool timeout + MCP routing |
| registry.py | Tool registration + hot-reload |

### Observability (anima/observability/)
| Module | Responsibility |
|--------|---------------|
| tracer.py | Span-based execution tracing |

### Defensive (anima/utils/)
| Module | Responsibility |
|--------|---------------|
| errors.py | 9-class exception hierarchy |
| path_safety.py | Path traversal prevention |
| invariants.py | Runtime precondition checks |

## Data Flow: User Message

```
1. User types message
2. Terminal/Discord → EventQueue (USER_MESSAGE, priority=HIGH)
3. EventRouter.route():
   - Classify: is_self=False, is_delegation=False
   - Try rule engine (greeting detection)
   - Convert to LLM message
   - Select tier=1 (Opus)
4. MemoryRetriever.retrieve():
   - Tier 0: Core identity + user profile
   - Tier 1: Static knowledge by category
   - Lorebook: Keyword scan
   - Tier 3: Time-weighted recent
   - Tier 2: Semantic search (cosine similarity)
   - RRF fusion → token budget truncation
5. PromptCompiler.compile():
   - 6 layers: identity, rules, context, memory, conversation, tools
   - TokenBudget allocation across layers
6. ToolOrchestrator.run_tool_loop():
   - Dynamic tool selection by intent
   - Streaming LLM call (text chunks → terminal)
   - Tool execution with per-tool timeout
   - Max 15 turns
7. ResponseHandler.handle():
   - Soul Container post-processing
   - Output to terminal + dashboard
   - Save to memory with importance scoring
   - Emotion feedback extraction
   - Conversation buffer update
```

## Security Model

- **Command execution**: All subprocess calls go through SafeSubprocess
- **Path validation**: All file I/O validates paths via path_safety
- **Tool safety**: Structural command analysis (shlex, not regex)
- **MCP tools**: Parameter validation + risk check + timeout
- **Agent recursion**: Blocked via _FORBIDDEN_IN_SUBAGENT
- **Evolution**: Safety tags + health checks + auto-rollback

## Testing

378+ tests across 9 test suites:
- test_sprint1_security.py: Injection, path traversal, safety
- test_sprint2_logic.py: Tier selection, budget, consensus
- test_sprint3_streaming.py: SSE, embedder, cache
- test_sprint4_architecture.py: Components, context, routing
- test_sprint5_memory.py: WAL, transactions, embeddings
- test_sprint6_final.py: Structured output, emotion, tracer
- test_sprint7_industrial.py: Token counting, startup, invariants
- test_sprint8_guardrails.py: Evolution safety, tool selection
- test_sprint9_integration.py: End-to-end pipeline
