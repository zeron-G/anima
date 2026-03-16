[English](README.md) | [中文](README_ZH.md)

# ANIMA

**Heartbeat-driven, distributed, self-evolving AI life system.**

ANIMA is not a chatbot — it's an autonomous AI life entity with its own heartbeat, emotions, memory, perception, and the ability to evolve its own code.

## Quick Start

```bash
# Windows: double-click
ANIMA.bat

# Or from terminal
python -m anima              # Desktop app (PyWebView window)
python -m anima --headless   # Backend only (browser at localhost:8420/desktop)
python -m anima --legacy     # Terminal mode (no GUI)
```

## Architecture

```
ANIMA Desktop
│
├── PyWebView Window (WebView2)
│   ├── VRM 3D Avatar (Three.js + three-vrm)
│   ├── Live2D 2D Avatar (PIXI + pixi-live2d-display)
│   ├── Chat + Real-time Activity Stream
│   └── Qwen3-TTS Lip Sync
│
├── Python Backend (aiohttp)
│   ├── Heartbeat Engine (script 30s / LLM 10min / major 30min)
│   ├── Cognitive Loop (rule engine → LLM agentic)
│   ├── Memory (SQLite + working memory)
│   ├── Emotion (engagement / confidence / curiosity / concern)
│   ├── Tools (13+ built-in)
│   ├── Qwen3-TTS (local CUDA)
│   └── Evolution Engine
│
├── Distributed Network
│   ├── ZMQ Gossip + Memory Sync
│   └── Session Router + Split-Brain Detection
│
└── Channels (Discord / Webhook)
```

## Project Structure

```
anima/
├── __main__.py           # CLI entry point
├── main.py               # Async orchestration
├── config.py             # YAML config loader
├── core/                 # Heartbeat, cognitive loop, evolution, agents
├── perception/           # System monitor, file watcher, diff engine
├── memory/               # SQLite store + working memory
├── emotion/              # 4-dim emotion state
├── llm/                  # Router (Tier2→Tier1), prompts, usage tracking
├── voice/                # Qwen3-TTS (tts.py), faster-whisper (stt.py)
├── desktop/              # PyWebView app + frontend (HTML/CSS/JS)
│   └── frontend/         # VRM/Live2D avatars, chat UI, viseme engine
├── dashboard/            # Admin dashboard (aiohttp + WebSocket)
├── network/              # ZMQ gossip mesh, memory sync, session router
├── tools/                # Tool registry + 13 built-in tools
├── channels/             # Discord bot, webhook receiver
├── models/               # Event, Decision, MemoryItem dataclasses
├── skills/               # External skill loader
├── spawn/                # Remote deployment
└── utils/                # Logging (24h rotation), ID generation

config/default.yaml       # Master configuration
agents/eva/               # Eva personality (soul.md, feelings.md)
tools/                    # VRM development tools (viewer, lab, inspector)
```

## Key Systems

### Heartbeat
Three independent timer loops:
- **Script** (30s): sample CPU/memory/disk, detect file changes, decay emotions
- **LLM** (10min): aggregate context, trigger self-thinking if significance threshold met
- **Major** (30min): evaluate evolution candidates

### Cognitive Loop
```
Event → Rule Engine (zero cost) → match? → execute
                                → no match → LLM (Tier2 Sonnet → Tier1 Opus fallback)
                                              → multi-turn tool use → response
```

### VRM Avatar
- **Model**: Flare (72 expressions, 53 bones)
- **Lip sync**: Audio → PCM → ZCR+RMS per 25ms frame → vowel classification → viseme timeline → 60fps interpolated playback
- **Idle**: breathing + blink (2-7s random) + subtle sway
- **Pose**: `getNormalizedBoneNode()` + `quaternion.setFromEuler()`
- **Costume**: 3 toggleable clothing meshes

### TTS
- **Qwen3-TTS** (`Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice`)
- Local PyTorch CUDA, no external server
- Auto-generated on agent response, cached by content hash

## Configuration

`config/default.yaml` — key settings:
```yaml
heartbeat:
  script_interval_s: 30
  llm_interval_s: 600
llm:
  tier1: { model: "claude-opus-4-6" }
  tier2: { model: "claude-sonnet-4-6" }
  budget: { daily_limit_usd: 10.0 }
```

Secrets in `.env`:
```
ANTHROPIC_API_KEY=sk-...
DISCORD_BOT_TOKEN=...
```

## Development

```bash
pip install -e ".[dev]"
pytest

# VRM tools (standalone)
python tools/vrm_viewer.py   # Model viewer
python tools/vrm_lab.py      # Expression/pose/lip sync lab
```

## License

See [LICENSE](LICENSE).
