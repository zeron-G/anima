[English](README.md) | [中文](README_ZH.md)

# ANIMA

ANIMA is a distributed AI runtime built for long-running, stateful agents. It combines a desktop interface, a persistent Python backend, memory and retrieval, multi-provider model routing, tool execution, node networking, and optional edge embodiments such as robot-dog Linux nodes.

Instead of treating an agent as a single chat request, ANIMA treats it as a continuous process with its own runtime state, background activity, network identity, and deployment lifecycle. The current repository centers on EVA as the default persona, but the underlying architecture is a general system for building persistent AI nodes.

## Overview

ANIMA includes several layers that work together:

- A Python backend that runs the API, WebSocket streams, heartbeat loops, governance, memory, tools, and distributed-node services
- A Vue frontend and Tauri desktop shell for the main operator-facing experience
- A configuration system with committed profiles and local machine-specific overrides
- A node network that supports discovery, delegation, remote deployment, and coordination between desktop, headless, and edge nodes
- An embodied robotics layer that can connect ANIMA to PiDog-based robot endpoints

## Main Capabilities

- Persistent runtime state with heartbeat-driven background processing
- Memory and retrieval backed by SQLite and ChromaDB
- Multi-provider LLM routing, fallback, and tool-integrated execution
- Desktop, browser, terminal, and headless operation modes
- Distributed node communication and task delegation
- Edge deployment profiles for specialized environments
- Robotics integration for PiDog-style Linux nodes

## System Architecture

At a high level, ANIMA is organized like this:

```text
Client Surfaces
  - Tauri desktop app
  - Browser-based Vue interface
  - Terminal mode
  - Remote node and edge integrations

Core Runtime
  - REST API and WebSocket hub
  - Cognitive pipeline and heartbeat loops
  - Memory, retrieval, and emotion state
  - Tool system and skill loading
  - Governance and evolution workflows
  - Gossip networking and task delegation

Embodiment and Deployment
  - Desktop supervisor nodes
  - Headless nodes
  - Edge nodes with committed runtime profiles
  - Robot-dog nodes connected through the robotics layer
```

## Repository Layout

```text
anima/             Python backend and runtime modules
eva-ui/            Vue frontend
eva-desktop/       Tauri desktop shell
config/            Default config and committed runtime profiles
agents/            EVA identity, rules, and memory files
docs/              Architecture and subsystem documents
local/             Machine-local config templates
tests/             Backend test suite
```

Important backend areas:

- `anima/api/`: REST endpoints
- `anima/core/`: cognitive pipeline, heartbeat, governance
- `anima/llm/`: model routing and provider integration
- `anima/memory/`: storage and retrieval
- `anima/network/`: gossip mesh and distributed node behavior
- `anima/robotics/`: robot node manager, exploration, and PiDog integration
- `anima/spawn/`: packaging and deployment for new nodes
- `anima/tools/`: built-in tool registry and tool handlers

## Running ANIMA

```bash
# Desktop app
python -m anima

# Backend only
python -m anima --headless

# Terminal mode
python -m anima --terminal

# Frontend development
cd eva-ui
npm install
npm run dev

# Desktop shell development
cd eva-desktop
npm install
npm run dev
```

## Deployment Modes

ANIMA supports several runtime shapes:

- Desktop supervisor: the main local operator-facing node
- Headless node: a networked backend without the desktop shell
- Edge node: a specialized runtime selected through a committed profile such as `edge-pidog`

Examples:

```bash
# Run an edge profile locally
ANIMA_PROFILE=edge-pidog python -m anima --edge

# Deploy to a configured known node
python -m anima spawn --node pidog
python -m anima spawn --node laptop --profile default
```

## Configuration

Configuration is intentionally split between committed project defaults and local machine settings:

- `config/default.yaml`: shared project defaults
- `config/profiles/*.yaml`: committed runtime profiles such as edge deployments
- `local/env.yaml`: machine-specific settings, peers, addresses, and deployment targets
- `.env`: local secrets and provider credentials

`local/env.yaml` and `.env` are ignored by git so sensitive configuration stays local.

## Documentation

Additional design documents live in [docs](docs):

- [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [EDGE_ANIMA.md](docs/EDGE_ANIMA.md)
- [ROBOTICS_PIDOG.md](docs/ROBOTICS_PIDOG.md)
- [DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md)

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

See [LICENSE](LICENSE).
