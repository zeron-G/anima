# Sprint 3 Completion Report — Streaming & Semantic Search

**Date**: 2026-03-19
**Status**: COMPLETE
**Tests**: 22 new + 316 total (all passing, 0 regressions)

## Major Features Added

### 1. Streaming LLM Output (H-03)
The single highest-impact UX improvement. Users now see text appear token-by-token instead of waiting 30-60 seconds.

**Architecture**:
```
providers.py → completion_stream() → yields StreamEvent
    ↓
router.py → call_with_tools_stream() → model cascade + circuit breaker
    ↓
cognitive.py → _stream_llm_call() → pushes chunks to callback
    ↓
main.py → on_stream_chunk() → terminal.display_chunk() + dashboard.push_stream_chunk()
```

**StreamEvent types**: text_delta, tool_use_start, tool_input_delta, tool_use_done, message_complete, error

**Protocol support**: Anthropic SSE (content_block_delta, input_json_delta), OpenAI SSE (choices[0].delta)

### 2. Local Embedding Engine (H-08)
Semantic search no longer silently degrades to SQL LIKE when ChromaDB is absent.

- **Model**: paraphrase-multilingual-MiniLM-L12-v2 (384-dim, Chinese+English)
- **Storage**: SQLite BLOB via memory_embeddings table (1536 bytes per vector)
- **Fallback chain**: ChromaDB → local embedder → FTS5 → LIKE

### 3. Remaining MEDIUM Fixes
- M-06: Summarizer rule fallback no longer appends old summary
- M-23: Alert cooldown tracks last attempt time (not just last send)
- M-33: TokenBudget distributes rounding remainder
- M-35: PromptCompiler caches with file mtime invalidation
- M-43: OAuth token uses startswith() not substring

## New Files Created

| File | Purpose | Lines |
|------|---------|-------|
| `anima/memory/embedder.py` | Local sentence-transformer embedding engine | ~175 |
| `tests/test_sprint3_streaming.py` | Sprint 3 test suite (22 tests) | ~310 |

## Files Modified

| File | Changes |
|------|---------|
| `llm/providers.py` | Added StreamEvent, completion_stream(), _anthropic_completion_stream(), _openai_completion_stream(), M-43 OAuth fix |
| `llm/router.py` | Added call_with_tools_stream() with model cascade |
| `core/cognitive.py` | Added _stream_callback, set_stream_callback(), _stream_llm_call() |
| `ui/terminal.py` | Added display_chunk() for streaming output |
| `dashboard/hub.py` | Added push_stream_chunk() for SSE to clients |
| `main.py` | Wired on_stream_chunk callback |
| `memory/store.py` | Added memory_embeddings table to schema |
| `memory/summarizer.py` | M-06: rule fallback fix |
| `core/heartbeat.py` | M-23: cooldown fix |
| `llm/token_budget.py` | M-33: rounding remainder fix |
| `llm/prompt_compiler.py` | M-35: mtime-based cache invalidation |

## Cumulative Progress (Sprint 1+2+3)

| Severity | Total | Resolved | Remaining |
|----------|-------|----------|-----------|
| CRITICAL | 14 | 9 | 5 |
| HIGH | 28 | 27 | 1 |
| MEDIUM | 51 | 22 | 29 |
| LOW | 30 | 2 | 28 |
| **Total** | **123** | **60** | **63** |

**Remaining HIGH**: H-23 (emotion feedback loop — deferred to Sprint 7 as architecture upgrade)

**88 new tests added across 3 sprints, 316 total tests, 0 regressions.**
