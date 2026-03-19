# ANIMA Architecture Guide

## System Overview

ANIMA is a heartbeat-driven, distributed, self-evolving AI agent with a 4-component cognitive pipeline, multi-tier memory system, and autonomous evolution capability.

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
| Module | Responsibility |
|--------|---------------|
| store.py | SQLite + ChromaDB + local embeddings |
| retriever.py | 4-tier RRF fusion retrieval |
| embedder.py | Local sentence-transformer embeddings |
| decay.py | Time-weighted importance decay + consolidation |
| summarizer.py | Conversation compression |
| importance.py | Dynamic memory importance scoring |
| db_manager.py | Thread-safe SQLite (WAL + Lock + transactions) |

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
