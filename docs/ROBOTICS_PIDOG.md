# ANIMA PiDog Robotics Architecture

This document describes the embodied-control architecture now wired into ANIMA for the robot dog Linux platform.

## Goals

- Let the robot dog run ANIMA-compatible autonomy on its own Linux node.
- Let the main ANIMA runtime call the dog as a remote embodied node through tools and REST.
- Let the EVA desktop app directly drive the dog with low-latency actions.
- Let the dog sense nearby obstacles and explore on its own when desktop control is not actively steering it.

For the dedicated onboard runtime shape, see [EDGE_ANIMA.md](EDGE_ANIMA.md).

## Layer Mapping

The current design intentionally mirrors the four-layer PiDog Eva stack already running on the robot:

1. Layer 0: hardware primitives on the robot.
   Motion servos, ultrasonic distance, IMU tilt, touch, RGB, and audio are exposed as low-level capabilities.
2. Layer 1: motion manager on the robot.
   Structured actions such as `stand`, `walk_forward`, `turn_left`, `bark`, `sleep_mode`, and `resume` are normalized here.
3. Layer 2: local autonomy loop on the robot.
   The robot can react to battery, obstacle, lift, tilt, and touch conditions without waiting for the desktop.
4. Layer 3: Eva HTTP interface on the robot.
   The node exposes `/status`, `/command`, `/nlp`, and `/speak` so external ANIMA runtimes can orchestrate it.

ANIMA adds a fifth coordination surface above that stack:

5. ANIMA orchestration layer.
   The central runtime treats the dog as an embodied remote node and exposes it to the tool system, REST API, dashboard snapshots, and EVA desktop UI.

## Control Paths

There are now three first-class control paths:

- Onboard autonomy.
  The robot node can keep exploring using its local PiDog service and ANIMA-triggered exploration loop.
- Remote ANIMA control.
  The main runtime can call `robot_dog_status`, `robot_dog_command`, `robot_dog_nlp`, `robot_dog_speak`, and `robot_dog_exploration`.
- EVA desktop direct control.
  The Vue desktop page `/robotics` lets the user click motion commands, submit natural-language requests, trigger robot speech, and start or stop exploration.

Natural-language control now resolves in three tiers:

1. Robot-local PiDog `/nlp`.
   Fast keyword parsing on the dog, now intended for low-latency basic actions.
2. Supervisor-side deterministic fallback.
   ANIMA can map common English motion phrases such as `sit down`, `stand up`, `turn left`, or `wag your tail` even when the robot-local parser does not understand them.
3. Supervisor-side LLM fallback.
   The desktop supervisor can escalate unresolved phrases to a higher-level planner model and then emit a structured PiDog command.

## Node Contract

The PiDog Linux node contract assumed by ANIMA is:

- `GET /health`
- `GET /status`
- `POST /command` with `{ "command": "...", "params": { ... } }`
- `POST /nlp` with `{ "text": "..." }`
- `POST /speak` with `{ "text": "...", "blocking": false }`

The status payload is normalized into ANIMA perception fields:

- `distance` -> `distance_cm`
- `touch` -> `touch`
- `pitch` -> `pitch_deg`
- `roll` -> `roll_deg`
- `battery` -> `battery_v`
- `is_lifted`
- `is_obstacle_near`
- `is_obstacle_warn`
- `queue_size`
- `state`
- `emotion`

## ANIMA Backend Modules

The embodied-node backend lives in these modules:

- `anima/robotics/models.py`
  Shared node config, perception, exploration state, and snapshot models.
- `anima/robotics/pidog.py`
  Resilient HTTP client with multi-endpoint failover across LAN or overlay-network addresses.
- `anima/robotics/exploration.py`
  Reactive exploration controller that converts sensor state into motion bursts and scan sweeps.
- `anima/robotics/manager.py`
  Runtime registry for configured robot nodes, polling, tool execution, and exploration lifecycle.
- `anima/api/robotics.py`
  `/v1/robotics/*` endpoints for desktop and external clients.
- `anima/tools/builtin/robotics.py`
  Tool layer that makes the robot dog callable from ANIMA cognition.

## EVA Desktop Integration

The desktop control surface lives in:

- `eva-ui/src/views/RoboticsView.vue`
- `eva-ui/src/api/robotics.ts`
- `eva-ui/src/router.ts`
- `eva-ui/src/components/global/OrbitNav.vue`

The page supports:

- node selection
- live perception cards
- direct motion buttons
- natural-language command dispatch
- robot-side speech
- exploration start and stop
- autonomy trace review

## Exploration Strategy

The current exploration controller is deliberately conservative and hardware-safe.

Decision order:

1. Recover posture.
   If the dog is sitting, lying, or idle when exploration should continue, stand it up first.
2. Recover from emergency.
   If the robot reports emergency state, send `resume`.
3. Battery protection.
   Critical battery sends `sleep_mode`; low battery sends `sit`.
4. Stability protection.
   Lift or extreme tilt causes an immediate sit command.
5. Touch avoidance.
   Left touch turns right; right touch turns left.
6. Obstacle avoidance.
   Near obstacles trigger turn bursts, and every third blocked tick backs the dog away.
7. Scan sweep.
   Every few ticks the head cycles through a scan sequence.
8. Forward exploration.
   If the path is clear, the dog walks forward in short bursts and then auto-stops.

This is phase-1 autonomy: reactive wandering with safety gating. It is intentionally structured so later phases can swap in richer planners without changing the desktop or tool API.

## Configuration

Put machine-specific addresses in `local/env.yaml`, not in the committed default config.

Example:

```yaml
robotics:
  enabled: true
  poll_interval_s: 2.0
  nlp_supervisor:
    enabled: true
    model: "codex/gpt-5.3-codex"
    max_tokens: 480
    min_confidence: 0.55
  exploration:
    walk_speed: 45
    turn_speed: 55
    avoid_distance_cm: 32
  nodes:
    - id: "pidog-eva"
      name: "PiDog Eva"
      kind: "pidog"
      role: "robot_dog"
      base_urls:
        - "http://<lan-ip>:8888"
        - "http://<overlay-ip>:8888"
      exploration:
        turn_speed: 60
      tags: ["lab", "embodied", "dog"]
      metadata:
        platform: "linux"
        bridge: "eva-http-layer3"
```

Notes:

- `base_urls` should contain both the direct LAN address and the overlay-network address when available.
- Global `robotics.exploration` acts as the fleet default.
- Per-node `exploration` overrides only that node.
- `robotics.nlp_supervisor` is intended for the desktop supervisor node, where the user's Codex OAuth session is available.
- The committed default model is `codex/gpt-5.3-codex`; the edge robot profile keeps this layer disabled so the robot can stay self-contained when offline.

## Operational Model

At runtime the flow is:

1. `anima.main` creates `RoboticsManager`.
2. The manager builds one `PiDogApiClient` and one `ExplorationController` per configured node.
3. The dashboard hub publishes robotics snapshots into the main WebSocket payload.
4. `/v1/robotics/*` routes provide structured desktop access.
5. Built-in tools let ANIMA cognition invoke robot actions during agent execution.

## Deployment and Replication Model

The robot-side environment is now embedded into the main ANIMA deployment path,
not maintained as a separate ad hoc installer.

In practice that means:

- the reusable robot runtime shape lives in `config/profiles/edge-pidog.yaml`
- machine-local robot addresses, peers, and credentials live in `local/env.yaml`
- the same spawn system can package and deploy the robot runtime with either:
  - `python -m anima spawn user@robot-host --edge --profile edge-pidog`
  - `python -m anima spawn --node pidog`
- ANIMA's built-in remote tool layer can also trigger `spawn_remote_node` for trusted known nodes

This keeps the robot as a special environment, but still a native part of the
ANIMA architecture rather than a disconnected side project.

## Testing Scope

The implementation is covered by:

- manager tests against a fake PiDog HTTP service
- exploration policy tests
- tool execution tests
- robotics REST API tests
- EVA frontend production build

## Next-Phase Extensions

The current design leaves clear extension points for deeper embodiment:

- camera and VLM perception ingestion
- room-scale frontier mapping instead of reactive wandering
- docking and charge-seeking behavior
- multi-node coordination between desktop ANIMA and onboard ANIMA
- task-level action graphs that combine motion, speech, and perception goals
