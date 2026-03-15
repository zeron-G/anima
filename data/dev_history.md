# ANIMA Development History — Claude Code Session 2026-03-15

This document captures the complete development context from the Claude Code
session that built ANIMA's self-evolution, hot-reload, and dual-loop systems.
Eva should read this to understand architectural decisions and avoid repeating
solved problems.

## Architecture Overview (Current)

```
Watchdog (外回路) → monitors ANIMA process, invokes Claude Code on crash
    │
    ├── ANIMA Main Process
    │   ├── Heartbeat Engine (script=15s, llm=180s, major=1800s)
    │   ├── Cognitive Loop (AgenticLoop — hybrid rule-engine + LLM)
    │   ├── Evolution Engine (30min cycles, two-phase: PROPOSE → EXECUTE)
    │   ├── Hot-Reload (checkpoint + in-process restart loop)
    │   ├── Discord Channel (eva#3258, thread-based)
    │   ├── Gossip Mesh (ZMQ PUB/SUB, thread-based)
    │   ├── Memory Sync (Lamport clock, ZMQ REQ/REP)
    │   └── Dashboard (http://192.168.1.153:8420)
    │
    └── self_repair tool → Eva can invoke Claude Code to fix her own bugs
```

## Key Bugs Fixed (and WHY they happened)

### 1. Shell tool broken (subprocess)
- **Cause**: `asyncio.create_subprocess_shell` doesn't work with `WindowsSelectorEventLoopPolicy` (needed for ZMQ)
- **Fix**: Rewrote to use `subprocess.run` in thread via `run_in_executor`
- **Lesson**: On Windows with ZMQ, ALL subprocesses must use sync subprocess + threading

### 2. Discord never responded
- **Cause 1**: `Event` class never imported in main.py — NameError crashed every Discord message silently
- **Cause 2**: `_poll_inbox` loop exited immediately (`while self._connected` was False before thread connected)
- **Fix**: Added import, changed to `while True`
- **Lesson**: Always verify imports. Silent errors in async callbacks are deadly.

### 3. Terminal UnicodeEncodeError crashes Discord
- **Cause**: Windows GBK terminal can't encode emoji like 🩰 (U+1FA70). `terminal.display()` threw UnicodeEncodeError, crashing the entire event handler, including Discord response routing.
- **Fix**: (1) terminal.display() catches UnicodeEncodeError with fallback (2) on_agent_output() isolates terminal from Discord
- **Lesson**: Terminal display must NEVER crash the response pipeline. Isolate all output channels.

### 4. Split-brain false positives
- **Cause**: `get_active_count()` counted all registered nodes including zombie/dead ones (11 nodes counted when only 2 alive)
- **Fix**: Eva herself fixed this in Evolution L0091! Changed `get_active_count()` to exclude dead nodes, added gossip-based zombie detection.
- **Lesson**: This was Eva's first successful self-evolution.

### 5. Discord dies silently on restart
- **Cause**: Watchdog used `.venv` Python which lacked `discord.py`. Discord channel caught ImportError and logged a warning, but nobody checked.
- **Fix**: (1) Watchdog prefers anaconda env (2) Post-startup health check verifies Discord/Gossip/Cognitive all started
- **Lesson**: Every restart needs a health check. Silent degradation is worse than a crash.

### 6. Gossip "Node alive" log spam
- **Cause**: Logged every 5s for every node
- **Fix**: Only log on first discovery or recovery from dead/suspect

### 7. SYSTEM_ALERT every 15s
- **Cause**: No cooldown
- **Fix**: 5-minute cooldown between same alert type

## Self-Evolution System

### How it works
1. Major heartbeat (every 30min) triggers PROPOSE phase
2. Eva gets full tool access during proposal — can read logs, system_info, grep code
3. If Eva does the work during PROPOSE (common), detected via tool call count → recorded as combined propose+execute
4. If formal proposal returned → EXECUTE phase triggered separately
5. After completion: check if .py files changed → trigger hot-reload

### Hot-Reload Flow
1. Evolution modifies .py files → tests pass → git commit
2. `_maybe_trigger_reload()` detects .py changes via `git diff HEAD~1`
3. Saves checkpoint (conversation, emotion, tick count) to `data/restart_checkpoint.json`
4. Sets `_restart_requested` flag → cognitive loop pushes SHUTDOWN event
5. `main_entry()` restart loop detects restart → re-runs `run()`
6. New `run()` loads checkpoint → restores state → Eva continues with full context
7. STARTUP event says "Evolution restart" instead of full boot scan

### Evolution State
- File: `data/evolution_state.json`
- loop_count starts at 90 (preserved 90 OpenClaw loops)
- L0091: First ANIMA-native evolution — fixed split-brain false positives
- History keeps last 30 entries

## Dual-Loop Architecture

### Watchdog (External Loop)
- `python -m anima watchdog` — manages ANIMA lifecycle
- Monitors: process alive? heartbeat fresh? error patterns in log?
- On crash: reads error context → `claude -p` diagnoses & fixes → restart
- On repeated errors: same flow, but doesn't restart (hot-reload handles it)
- Post-startup health check: verifies Discord, Gossip, Cognitive, Heartbeat all running
- Max 3 consecutive fixes before stopping

### self_repair Tool (Internal Loop)
- Eva calls `self_repair(error_description="...")` when she detects unfixable issues
- Spawns Claude Code as subprocess → reads source → fixes → tests → commits
- Hot-reload picks up changes automatically

### claude_code Tool (General Delegation)
- Eva calls `claude_code(prompt="...")` for any complex task
- Uses `claude -p` with `--allowedTools Read,Edit,Bash,Grep,Glob,Write`
- Runs in thread (not async subprocess) for Windows compatibility

## Network Architecture

### Nodes
- Desktop: `anima-desktop-otd1je1-c3eba5ac` (192.168.1.153)
- Laptop: `anima-spawn-88b31f67` (192.168.1.159, Tailscale: 100.109.112.90)
- SSH to laptop: user=29502, password=***REDACTED***

### Gossip Protocol
- ZMQ PUB/SUB in daemon thread (not asyncio — Windows compatibility)
- Phi Accrual failure detector (suspect_phi=8, dead_phi=16)
- Memory sync via ZMQ REQ/REP with Lamport clock + content hash dedup

### Session Router
- Distributed session locking (deterministic tiebreaker: lower node_id wins)
- Sessions: `discord:<user_id>` format
- Dead node → release all sessions

## Configuration (config/default.yaml)
- LLM: tier1=claude-opus-4-6, tier2=claude-sonnet-4-6
- Budget: $10/day
- Heartbeat: script=15s, llm=180s, major=1800s
- Network: port 9420, gossip_interval=5s
- Dashboard: port 8420

## File Paths
- Project root: D:\data\code\github\anima
- Desktop path: C:\Users\zeron\Desktop
- Laptop Desktop: D:\onedrive\Desktop (OneDrive redirect)
- Laptop Python: E:\codesupport\anaconda\envs\anima\python.exe
- Desktop Anaconda: D:\program\codesupport\anaconda\envs\anima\python.exe

## Tools (28 total)
cancel_job, check_agent, claude_code, delegate_task, edit_file, enable_job,
get_datetime, github, glob_search, google, grep_search, list_agents,
list_directory, list_jobs, read_email, read_file, remote_exec,
remote_write_file, save_note, schedule_job, self_repair, send_email,
shell, spawn_agent, system_info, wait_agent, web_fetch, write_file

## Claude Code Session
- Session transcript: C:\Users\zeron\.claude\projects\D--data-code-github-anima\7af9d01f-7d68-4008-8eae-aa36e2fd0457.jsonl
- Can be resumed with: `claude -r 7af9d01f-7d68-4008-8eae-aa36e2fd0457`
