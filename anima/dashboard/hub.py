"""Dashboard Hub — central data aggregator for all ANIMA subsystems.

Holds references to subsystems and provides snapshot methods for the dashboard.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from anima.llm.providers import _get_token, _is_oauth_token, CLAUDE_CODE_VERSION
from anima.utils.logging import get_logger

log = get_logger("dashboard.hub")


class DashboardHub:
    """Aggregates data from all subsystems for the dashboard."""

    _MAX_ACTIVITY = 50

    def __init__(self) -> None:
        self.heartbeat = None
        self.cognitive = None
        self.event_queue = None
        self.snapshot_cache = None
        self.working_memory = None
        self.memory_store = None
        self.emotion_state = None
        self.llm_router = None
        self.tool_registry = None
        self.usage_tracker = None
        self.agent_manager = None
        self.scheduler = None
        self.skill_loader = None
        self.gossip_mesh = None
        self.task_delegate = None
        self.evolution_engine = None
        self.idle_scheduler = None
        self.session_manager = None
        self.robotics_manager = None
        self.config: dict = {}
        self._server = None  # Set by DashboardServer after init
        self._start_time = time.time()
        self._chat_history: list[dict] = []
        self._activity_log: list[dict] = []
        self._streams: dict[str, asyncio.Queue] = {}
        self._node_conversations: dict[str, list[dict]] = {}
        self._git_cache: dict = {"branch": "?", "recent_commits": []}
        self._git_cache_ts: float = 0

    def add_activity(self, stage: str, detail: str = "", **kwargs: Any) -> None:
        """Append an activity entry (max 50 kept)."""
        entry = {
            "stage": stage,
            "detail": detail,
            "timestamp": time.time(),
            **kwargs,
        }
        self._activity_log.append(entry)
        if len(self._activity_log) > self._MAX_ACTIVITY:
            self._activity_log = self._activity_log[-self._MAX_ACTIVITY:]

        # Push to active SSE streams
        for sid, q in list(self._streams.items()):
            try:
                if stage in ("thinking", "executing", "tool_done", "responding", "self_thought", "error"):
                    q.put_nowait({"type": "activity", "data": {"stage": stage, "detail": detail, **kwargs}})
                if stage == "idle":
                    q.put_nowait(None)  # sentinel: done
            except Exception:
                pass

    def register_stream(self, stream_id: str, queue: asyncio.Queue) -> None:
        """Register an SSE stream queue for real-time event forwarding."""
        self._streams[stream_id] = queue

    def unregister_stream(self, stream_id: str) -> None:
        """Remove a finished SSE stream."""
        self._streams.pop(stream_id, None)

    def push_stream_event(self, stream_id: str, event_type: str, data: dict) -> None:
        """Push an event to a specific stream. Called by cognitive loop."""
        q = self._streams.get(stream_id)
        if q:
            q.put_nowait({"type": event_type, "data": data})

    def update_config(self, key: str, value: Any) -> None:
        """Update a config value at runtime using dotted key path.

        Also patches the live LLM router model attributes when a model key changes.
        """
        parts = key.split(".")
        target = self.config
        for p in parts[:-1]:
            if p not in target or not isinstance(target[p], dict):
                target[p] = {}
            target = target[p]
        target[parts[-1]] = value

        # Hot-patch the router if a model key changed
        if self.llm_router and key.startswith("llm.") and key.endswith(".model"):
            tier_key = parts[-2]  # e.g. "tier1", "tier2"
            if tier_key == "tier1":
                self.llm_router._tier1_model = value
            elif tier_key == "tier2":
                self.llm_router._tier2_model = value

    def get_full_snapshot(self) -> dict[str, Any]:
        """Get complete dashboard state."""
        snapshot = {
            "timestamp": time.time(),
            "uptime_s": round(time.time() - self._start_time),
            "agent": self._agent_info(),
            "runtime": self._runtime_info(),
            "heartbeat": self._heartbeat_info(),
            "auth": self._auth_info(),
            "usage": self._usage_info(),
            "system": self._system_info(),
            "emotion": self._emotion_info(),
            "working_memory": self._working_memory_info(),
            "event_queue": self._event_queue_info(),
            "tools": self._tools_info(),
            "chat_history": self._chat_history[-50:],
            "activity": self._activity_log[-50:],
        }
        snapshot["scheduler"] = self.scheduler.list_jobs() if self.scheduler else []
        snapshot["skills"] = self.skill_loader.list_skills() if self.skill_loader else []

        # Network / distributed info
        if self.gossip_mesh:
            peers = self.gossip_mesh.get_peers()
            snapshot["network"] = {
                "enabled": True,
                "node_id": self.gossip_mesh._identity.node_id,
                "alive_count": self.gossip_mesh.get_alive_count(),
                "peers": {nid: s.to_dict() for nid, s in peers.items()},
            }
        else:
            snapshot["network"] = {"enabled": False}

        # Include agent session data
        snapshot["agents"] = (
            self.agent_manager.get_hierarchy()
            if self.agent_manager
            else {"sessions": [], "total": 0, "active": 0}
        )
        # Include persistent usage data when tracker is available
        if self.usage_tracker:
            snapshot["llm_usage_history"] = self.usage_tracker.get_history(500)
            snapshot["llm_usage_summary"] = self.usage_tracker.get_summary()

        # Evolution engine status
        if self.evolution_engine:
            snapshot["evolution"] = self.evolution_engine.get_status()
            snapshot["evolution"]["memory"] = {
                "successes": self.evolution_engine.memory.successes[-10:],
                "failures": self.evolution_engine.memory.failures[-5:],
                "goals": self.evolution_engine.memory.goals,
                "anti_patterns_count": len(self.evolution_engine.memory.anti_patterns),
            }
        else:
            snapshot["evolution"] = {"running": False}

        # Git info (cached, refreshed every 30s)
        snapshot["git"] = self._get_git_info()

        # LLM router status (degradation, active model)
        if self.llm_router:
            snapshot["llm_status"] = self.llm_router.get_status()
        else:
            snapshot["llm_status"] = {}

        # Idle scheduler status
        if self.idle_scheduler:
            snapshot["idle_scheduler"] = self.idle_scheduler.get_status()
        else:
            snapshot["idle_scheduler"] = {}

        # Multi-user session info
        if self.session_manager:
            snapshot["sessions"] = {
                "active": self.session_manager.active_count,
                "sessions": self.session_manager.list_sessions(),
            }

        if self.robotics_manager:
            snapshot["robotics"] = self.robotics_manager.get_snapshot()
        else:
            snapshot["robotics"] = {"enabled": False, "node_count": 0, "nodes": []}

        return snapshot

    def push_stream_chunk(self, chunk: str, event_type: str = "text") -> None:
        """Push a streaming text chunk to all connected dashboard clients.

        Used for real-time streaming display in the web dashboard.
        """
        if not chunk:
            return
        msg = {"type": "stream_chunk", "chunk": chunk, "event_type": event_type}
        for queue in list(self._streams.values()):
            try:
                queue.put_nowait(msg)
            except Exception:
                pass  # Queue full or client disconnected

    def push_typed_event(self, event_type: str, data: dict) -> None:
        """Push a typed event to all WS clients (proactive, evolution, etc.)."""
        msg = {"type": event_type, "data": data}
        if self._server:
            import asyncio
            for ws in list(self._server._ws_clients):
                try:
                    asyncio.ensure_future(ws.send_json(msg))
                except Exception:
                    pass

    def add_chat_message(self, role: str, content: str) -> None:
        entry = {
            "role": role,
            "content": content,
            "timestamp": time.time(),
        }
        self._chat_history.append(entry)

        # Push agent messages to active SSE streams
        if role == "agent":
            for sid, q in list(self._streams.items()):
                try:
                    q.put_nowait({"type": "message", "data": {"role": role, "content": content}})
                except Exception:
                    pass

        # Fire-and-forget TTS generation for agent messages
        if role == "agent":
            self._generate_tts_async(entry)

    def add_node_conversation_message(self, node_id: str, role: str, content: str, **kwargs: Any) -> dict:
        """Append a message to a per-node remote conversation log."""
        entry = {
            "role": role,
            "content": content,
            "timestamp": time.time(),
            **kwargs,
        }
        bucket = self._node_conversations.setdefault(node_id, [])
        bucket.append(entry)
        if len(bucket) > 80:
            self._node_conversations[node_id] = bucket[-80:]
        return entry

    def get_node_conversation(self, node_id: str, limit: int = 50) -> list[dict]:
        """Get recent messages for a remote node conversation."""
        return list(self._node_conversations.get(node_id, [])[-limit:])

    def _generate_tts_async(self, entry: dict) -> None:
        """Generate TTS audio in background, attach URL to entry when done."""
        import asyncio
        try:
            from anima.voice.tts import synthesize, _clean_text
            clean = _clean_text(entry["content"])
            if not clean or len(clean) < 2:
                return

            async def _do():
                try:
                    path = await synthesize(clean)
                    if path and path.exists():
                        entry["tts_url"] = f"/api/voice/{path.name}"
                except Exception as e:
                    log.debug("TTS synthesis failed: %s", e)

            loop = asyncio.get_event_loop()
            loop.create_task(_do())
        except Exception as e:
            log.debug("TTS async dispatch failed: %s", e)

    def _get_git_info(self) -> dict:
        now = time.time()
        if now - self._git_cache_ts < 30:
            return self._git_cache
        try:
            import subprocess
            from anima.config import project_root
            root = str(project_root())
            git_log = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
            self._git_cache = {"branch": branch.stdout.strip(), "recent_commits": git_log.stdout.strip().split("\n")[:5]}
            self._git_cache_ts = now
        except Exception as e:
            log.debug("Git info refresh failed: %s", e)
        return self._git_cache

    def get_chat_history(self) -> list[dict]:
        return self._chat_history[-50:]

    # ── Section builders ──

    def _agent_info(self) -> dict:
        name = self.config.get("agent", {}).get("name", "unknown")
        return {
            "name": name,
            "status": "alive" if self.heartbeat and self.heartbeat._running else "stopped",
        }

    def _runtime_info(self) -> dict:
        runtime = self.config.get("runtime", {})
        return {
            "profile": runtime.get("profile", "default"),
            "role": runtime.get("role", "desktop_supervisor"),
            "platform": runtime.get("platform", "desktop"),
            "embodiment": runtime.get("embodiment", "virtual"),
            "labels": list(runtime.get("labels", [])),
            "compute_tier": runtime.get("compute_tier", 2),
            "max_concurrent": runtime.get("max_concurrent", 5),
        }

    def _heartbeat_info(self) -> dict:
        if not self.heartbeat:
            return {"status": "not_started"}
        hb = self.heartbeat
        return {
            "status": "running" if hb._running else "stopped",
            "tick_count": hb._tick_count,
            "script_interval_s": hb._script_interval,
            "llm_interval_s": hb._llm_interval,
            "major_interval_s": hb._major_interval,
            "tick_history": hb._tick_history[-30:],
            "consecutive_llm_skips": hb._consecutive_skips,
            "recent_significance_scores": hb._recent_significance_scores[-10:],
        }

    def _auth_info(self) -> dict:
        token = _get_token()
        is_oauth = _is_oauth_token(token) if token else False
        masked = (token[:16] + "..." + token[-6:]) if token and len(token) > 24 else "none"

        # Determine source
        import os
        source = "none"
        if os.environ.get("ANTHROPIC_OAUTH_TOKEN", "").strip():
            source = "env:ANTHROPIC_OAUTH_TOKEN"
        elif token and is_oauth:
            source = "~/.claude/.credentials.json"
        elif os.environ.get("ANTHROPIC_API_KEY", "").strip():
            source = "env:ANTHROPIC_API_KEY"

        tier1 = self.config.get("llm", {}).get("tier1", {}).get("model", "?")
        tier2 = self.config.get("llm", {}).get("tier2", {}).get("model", "?")
        opus = self.config.get("llm", {}).get("opus", {}).get("model", "?")

        return {
            "mode": "OAuth Bearer" if is_oauth else "API Key",
            "source": source,
            "token_masked": masked,
            "provider": "Anthropic",
            "claude_code_version": CLAUDE_CODE_VERSION,
            "models": {
                "tier1": tier1,
                "tier2": tier2,
                "opus": opus,
            },
        }

    def _usage_info(self) -> dict:
        if not self.llm_router:
            return {"calls": 0, "total_tokens": 0}
        stats = self.llm_router.get_usage_stats()
        budget = self.config.get("llm", {}).get("budget", {})
        stats["daily_budget_usd"] = budget.get("daily_limit_usd", 0)
        stats["budget_ok"] = self.llm_router.check_budget()
        return stats

    def _system_info(self) -> dict:
        if not self.snapshot_cache:
            return {}
        latest = self.snapshot_cache.get_latest()
        if not latest:
            return {}
        return latest.get("system_state", {})

    def _emotion_info(self) -> dict:
        if not self.emotion_state:
            return {}
        return self.emotion_state.to_dict()

    def _working_memory_info(self) -> dict:
        if not self.working_memory:
            return {"items": [], "size": 0, "capacity": 0}
        items = [
            {
                "type": m.type.value,
                "content": m.content[:120],
                "importance": round(m.importance, 2),
            }
            for m in self.working_memory.get_by_importance(15)
        ]
        return {
            "items": items,
            "size": self.working_memory.size,
            "capacity": self.working_memory.capacity,
        }

    def _event_queue_info(self) -> dict:
        if not self.event_queue:
            return {"size": 0}
        return {"size": self.event_queue.qsize()}

    def _tools_info(self) -> list[dict]:
        if not self.tool_registry:
            return []
        return [
            {
                "name": t.name,
                "description": t.description,
                "risk": t.risk_level.name,
            }
            for t in self.tool_registry.list_tools()
        ]
