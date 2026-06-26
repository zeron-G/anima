# ANIMA Developer Guide

## Setup

```bash
conda activate anima
pip install -e ".[dev,all]"        # base install is slim; [all] adds network/desktop/voice/discord
# Optional but recommended:
pip install sentence-transformers  # Local semantic search
pip install anthropic              # Anthropic SDK (auto-retry, connection pooling)
pip install tiktoken               # Precise token counting
pip install chromadb               # Vector database
```

## Running

```bash
python -m anima init               # First run: bootstrap a home from the persona seed
python -m anima                    # Start + open the web UI in a browser
python -m anima --headless         # Headless (API + WebSocket only)
python -m anima --legacy           # Terminal mode
python -m anima watchdog           # Watchdog supervisor (subcommand, NOT a flag)
```

## Running Tests

```bash
pytest tests/ --ignore=tests/test_oauth_live.py --ignore=tests/stress_test.py -v
```

## Adding a New Tool

1. Create `anima/tools/builtin/my_tool.py`:

```python
from anima.tools.safe_subprocess import split_command, run_safe
from anima.models.tool_spec import ToolSpec, RiskLevel

async def _my_tool(query: str, limit: int = 10) -> dict:
    """Handler — must be async, return dict."""
    # For external commands, ALWAYS use safe_subprocess:
    cmd = split_command("my-cli", query)
    return await run_safe(cmd, tool_name="my_tool", timeout=30)

def get_my_tool() -> ToolSpec:
    return ToolSpec(
        name="my_tool",
        description="Description for the LLM",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
        risk_level=RiskLevel.LOW,
        handler=_my_tool,
    )
```

2. Register in `anima/tools/registry.py`:
```python
from anima.tools.builtin.my_tool import get_my_tool
self.register(get_my_tool())
```

3. Add to `_BUILTIN_MODULES` list for hot-reload support.

## Modifying Prompts

The persona has two halves (see `docs/REFACTOR.md`):
- **Seed** (the published default) — edit under `agents/_seed/`: `identity/*.md`,
  `rules/*.md` (boundaries/events/memory/output/safety/style/tools/evolution),
  `lorebook/_index.yaml`, `examples/*.md`, `memory/persona_state.yaml` (baseline).
- **Live instance** (private, created by `anima init`) — `<ANIMA_HOME>/agents/<name>/`
  is where runtime self-edits + evolved memory land; it is gitignored. In a source
  checkout that path is `agents/<name>/` (e.g. `agents/eva/`), also gitignored.

Files auto-reload via mtime check.

## Configuration

Load order (later overrides earlier): `config/default.yaml` →
`config/profiles/*.yaml` → `agents/<name>/config.yaml` → `<ANIMA_HOME>/config.yaml`
→ (compat) `local/env.yaml` → `.env`. The home `config.yaml` is the canonical
machine-local override; `local/env.yaml` is a backward-compat layer.

Key settings:
```yaml
llm:
  tier1: { model: "claude-opus-4-6", max_tokens: 16384 }
  tier2: { model: "claude-sonnet-4-6", max_tokens: 8192 }
  budget: { daily_limit_usd: 10.0 }

memory:
  decay:
    cluster_window_hours: 6.0
    consolidation_threshold: 0.1
  retrieval:
    rrf_weights: { lorebook: 1.5, recent: 1.0, knowledge: 0.8 }

tools:
  timeouts: { shell: 60, github: 30, default: 30 }
```

## Architecture Decisions

1. **CognitiveContext over setters**: All dependencies in one dataclass, validated at construction
2. **SafeSubprocess over shell=True**: Eliminates command injection by design
3. **3-tier semantic search**: ChromaDB → local embedder → LIKE ensures search always works
4. **Streaming-first**: USER_MESSAGE always streams; internal events use non-streaming
5. **TokenBudget enforcement**: All prompts go through compile() with layer budgets
6. **Emotion feedback loop**: LLM responses analyzed for sentiment signals
