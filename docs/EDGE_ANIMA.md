# Edge ANIMA for Robot Dog Linux Nodes

This document describes the onboard "edge ANIMA" runtime for the robot dog platform.

## What Edge ANIMA Means

Edge ANIMA is the machine-dog-side runtime, not the desktop supervisor.

It is designed to run directly on the Linux computer mounted on the robot and provide:

- onboard ANIMA process supervision
- local PiDog embodiment bridge
- local autonomy when the desktop is offline
- network identity inside the ANIMA cluster
- clean handoff to the desktop or other supervisor nodes

## Roles in the Two-ANIMA Model

There are now two complementary runtime roles:

1. Desktop supervisor ANIMA
   Runs on the user's desktop or laptop and provides the rich UI, operator control, and higher-level orchestration.
2. Edge embodied ANIMA
   Runs on the robot dog Linux node and is responsible for local motion, local perception, and degraded-mode autonomy.

## Runtime Profile

The edge runtime is now represented by the committed config profile:

- `config/profiles/edge-pidog.yaml`

When enabled, it sets:

- `runtime.profile = edge-pidog`
- `runtime.role = edge_embodied`
- `runtime.platform = linux`
- `runtime.embodiment = robot_dog`
- `robotics.enabled = true`
- a default local PiDog endpoint at `http://127.0.0.1:8888`
- cautious governance defaults

## Startup Commands

### Run locally on the robot

```bash
ANIMA_PROFILE=edge-pidog python -m anima --edge
```

`--edge` is now a first-class startup mode. It bypasses the desktop launcher and starts the backend runtime directly.

### Package an edge deployment

```bash
python -m anima spawn --pack-only --edge --profile edge-pidog
```

### Deploy to a remote Linux robot

```bash
python -m anima spawn user@robot-host --edge --profile edge-pidog --install-dir ~/.anima-edge
```

This now produces a package that can:

- bootstrap the Python environment
- start the edge runtime with `--edge`
- optionally install a user-level systemd service

## Deployment Artifacts

The spawn package now includes:

- source code
- config profiles
- docs
- `local/env.yaml.example`
- edge-aware bootstrap scripts
- optional `deploy/anima-edge.service`

## Network Identity

Edge nodes now advertise richer node metadata through gossip:

- `runtime_profile`
- `runtime_role`
- `platform_class`
- `embodiment`
- `labels`

This allows the desktop supervisor to distinguish:

- desktop virtual nodes
- remote general compute nodes
- embodied edge robot nodes

## Coordination Model

### Onboard responsibilities

- connect to local PiDog service
- monitor battery, distance, touch, tilt, lift
- run safe exploration when not under direct operator control
- continue functioning even when the desktop is unreachable

### Desktop responsibilities

- issue direct motion commands
- send high-level natural-language tasks
- inspect telemetry and autonomy traces
- coordinate multi-node behavior
- take over or stop exploration remotely

The desktop supervisor is also the preferred place for higher-level robot NLP planning.
In the current configuration, the desktop runtime can use Codex OAuth with
`codex/gpt-5.3-codex` to translate freer English requests into one safe PiDog command,
while the edge runtime stays on lightweight local parsing plus autonomy.

## Suggested Robot-Side Local Overrides

In `local/env.yaml` on the robot, typically provide:

```yaml
network:
  secret: "<shared-secret>"
  peers:
    - "192.168.1.10:9420"

dashboard:
  auth:
    token: "<shared-dashboard-token>"

robotics:
  nodes:
    - id: "pidog-local"
      base_urls:
        - "http://127.0.0.1:8888"
      metadata:
        lan_hint: "192.168.1.174:8888"
        tailscale_hint: "100.99.62.80:8888"
```

## Current Edge Scope

The current implementation is phase 1 of edge ANIMA:

- edge runtime profile
- edge startup mode
- edge-aware spawn packaging and deployment
- edge node self-description in the network
- local PiDog embodiment integration

## Next Extensions

Natural next steps after this phase:

- local VLM / camera perception ingestion
- dock / charge-seeking behavior
- map persistence on the robot
- task graph execution with local fallback
- richer memory sync policies between edge and desktop
