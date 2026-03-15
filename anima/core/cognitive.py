"""Agentic loop with rule-engine pre-filter — LLM-native decision making
with persistent life capabilities.

Architecture:
  Event arrives → rule engine tries first (zero cost)
    → if handled: execute + done (no LLM)
    → if not handled: LLM agentic loop (multi-turn tool use)

This hybrid saves ~80% of LLM calls compared to "everything goes to LLM"
while preserving full autonomous capability for complex events.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable

from anima.config import get
from anima.core.event_queue import EventQueue
from anima.core.rule_engine import RuleEngine
from anima.emotion.state import EmotionState
from anima.llm.prompts import PromptBuilder
from anima.llm.router import LLMRouter
from anima.memory.store import MemoryStore
from anima.models.decision import Decision, ActionType
from anima.models.event import Event, EventType
from anima.perception.snapshot_cache import SnapshotCache
from anima.tools.executor import ToolExecutor
from anima.tools.registry import ToolRegistry
from anima.core.reload import ReloadManager
from anima.utils.logging import get_logger

log = get_logger("cognitive")


class AgenticLoop:
    """The brain. Hybrid rule-engine + LLM agentic loop.

    Event routing:
    - FILE_CHANGE, SYSTEM_ALERT: rule engine first, LLM only if rules return NOOP
    - Simple USER_MESSAGE (greetings): rule engine handles directly
    - Complex USER_MESSAGE: LLM with full tool access
    - STARTUP, SELF_THINKING: LLM (but lean prompt, short timeout)

    Self-event outputs go to memory + activity feed, not to user.
    All events (including self-thoughts) are stored in conversation buffer
    so the agent remembers its own observations.
    """

    def __init__(
        self,
        event_queue: EventQueue,
        snapshot_cache: SnapshotCache,
        memory_store: MemoryStore,
        emotion_state: EmotionState,
        llm_router: LLMRouter,
        prompt_builder: PromptBuilder,
        tool_executor: ToolExecutor,
        tool_registry: ToolRegistry,
        config: dict,
    ) -> None:
        self._event_queue = event_queue
        self._snapshot_cache = snapshot_cache
        self._memory_store = memory_store
        self._emotion = emotion_state
        self._llm_router = llm_router
        self._prompt_builder = prompt_builder
        self._tool_executor = tool_executor
        self._tool_registry = tool_registry
        self._rule_engine = RuleEngine()
        self._config = config

        self._conversation: list[dict] = []
        self._max_conversation_turns = 50
        self._output_callback: Callable[..., Any] | None = None
        self._status_callback: Callable[[dict], Any] | None = None
        self._current_source: str = ""  # Track event source for response routing
        self._gossip_mesh = None  # Set via set_gossip_mesh() for delegation results
        self._reload_manager = ReloadManager()
        self._heartbeat = None  # Set via set_heartbeat() for tick count in checkpoint

    def set_gossip_mesh(self, gossip_mesh) -> None:
        """Set gossip mesh for broadcasting delegation results."""
        self._gossip_mesh = gossip_mesh

    def set_heartbeat(self, heartbeat) -> None:
        """Set heartbeat ref for checkpoint tick count."""
        self._heartbeat = heartbeat

    @property
    def reload_manager(self) -> ReloadManager:
        return self._reload_manager

    def restore_from_checkpoint(self, checkpoint: dict) -> None:
        """Restore conversation and emotion state from a restart checkpoint."""
        conv = checkpoint.get("conversation", [])
        if conv:
            self._conversation = conv
            log.info("Restored %d conversation turns from checkpoint", len(conv))
        emotion = checkpoint.get("emotion")
        if emotion:
            self._emotion.engagement = emotion.get("engagement", self._emotion.engagement)
            self._emotion.confidence = emotion.get("confidence", self._emotion.confidence)
            self._emotion.curiosity = emotion.get("curiosity", self._emotion.curiosity)
            self._emotion.concern = emotion.get("concern", self._emotion.concern)
            log.info("Restored emotion state from checkpoint")

    def set_output_callback(self, callback: Callable[[str], Any]) -> None:
        self._output_callback = callback

    def set_status_callback(self, callback: Callable[[dict], Any]) -> None:
        self._status_callback = callback

    # ------------------------------------------------------------------ #
    #  Main loop                                                          #
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        log.info("Agentic loop started")
        while True:
            event = await self._event_queue.get_timeout(timeout=2.0)
            if event is None:
                # Check for pending reload request during idle
                if self._reload_manager.restart_requested:
                    log.info("Reload requested — triggering shutdown for restart")
                    await self._event_queue.put(Event(
                        type=EventType.SHUTDOWN,
                        payload={"restart": True, "reason": self._reload_manager.restart_reason},
                        source="reload",
                    ))
                continue
            if event.type == EventType.SHUTDOWN:
                break
            try:
                await self._process_event(event)
            except Exception as e:
                log.error("Agentic loop error: %s", e, exc_info=True)
                self._emit_status({"stage": "error", "detail": str(e)})
            finally:
                self._emit_status({"stage": "idle"})
        log.info("Agentic loop stopped")

    async def _process_event(self, event: Event) -> None:
        await self._handle_event(event)

    # ------------------------------------------------------------------ #
    #  Event handler — rule engine first, then LLM                       #
    # ------------------------------------------------------------------ #

    async def _handle_event(self, event: Event) -> None:
        # Track source for response routing (Discord, webhook, terminal, etc.)
        self._current_source = event.payload.get("source", event.source) if event.payload else event.source

        is_self = event.type in (
            EventType.STARTUP, EventType.SELF_THINKING, EventType.FOLLOW_UP,
            EventType.FILE_CHANGE, EventType.SYSTEM_ALERT, EventType.SCHEDULED_TASK,
        )
        # TASK_DELEGATE is from another node — response goes back via gossip, not terminal
        is_delegation = event.type == EventType.TASK_DELEGATE
        log.info("Processing event: %s%s%s", event.type.name,
                 " (internal)" if is_self else "",
                 " (delegation)" if is_delegation else "")

        # ── Step 1: Try rule engine for cheap events ──
        # FILE_CHANGE, SYSTEM_ALERT, simple greetings → handled without LLM
        if event.type in (EventType.FILE_CHANGE, EventType.SYSTEM_ALERT, EventType.USER_MESSAGE):
            snapshot = self._snapshot_cache.get_latest()
            system_state = snapshot.get("system_state", {}) if snapshot else {}
            context = {
                "event_type": event.type.name,
                "event_payload": event.payload,
                "system_state": system_state,
            }
            decision = self._rule_engine.evaluate(context)

            if decision.action != ActionType.NOOP:
                # Rule engine handled it — execute without LLM
                self._emit_status({"stage": "rule_engine", "detail": f"{decision.action.value}: {decision.reasoning}"})
                log.info("Rule engine handled %s: %s", event.type.name, decision.action.value)

                if decision.action == ActionType.RESPOND:
                    await self._output(decision.content)
                    self._save_chat("assistant", decision.content)
                elif decision.action == ActionType.TOOL_CALL:
                    await self._tool_executor.execute(decision.tool_name, decision.tool_args)

                # Record in memory
                self._memory_store.audit(action=f"rule:{event.type.name}", details=decision.reasoning[:200])
                return

        # ── Step 2: LLM agentic loop for complex events ──
        user_message = self._event_to_message(event)

        # Save user message to episodic memory
        if event.type == EventType.USER_MESSAGE and user_message:
            self._memory_store.save_memory(
                content=user_message, type="chat", importance=0.6,
                metadata={"role": "user"},
            )

        # Build prompt — lean, event-specific
        snapshot = self._snapshot_cache.get_latest()
        system_state = snapshot.get("system_state", {}) if snapshot else {}
        event_type_name = event.type.name
        needs_tools = event_type_name in ("USER_MESSAGE", "STARTUP", "SELF_THINKING", "SCHEDULED_TASK", "TASK_DELEGATE")

        system_prompt = self._prompt_builder.build_for_event(
            event_type_name,
            tools_description=self._build_tools_description() if needs_tools else "",
            system_state=system_state,
            emotion_state=self._emotion.to_dict() if event_type_name == "USER_MESSAGE" else None,
            working_memory_summary=self._get_memory_summary() if event_type_name == "USER_MESSAGE" else "",
        )

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if event_type_name == "USER_MESSAGE":
            messages.extend(self._conversation[-self._max_conversation_turns:])
        messages.append({"role": "user", "content": user_message})

        tools = self._get_tool_schemas() if needs_tools else []

        # Agentic loop — evolution gets extra time
        is_evolution = bool(event.payload and event.payload.get("evolution"))
        timeout = 300 if is_evolution else (60 if is_self else 180)
        start_time = time.time()
        self._emit_status({"stage": "thinking", "detail": f"processing {event_type_name}"})

        while time.time() - start_time < timeout:
            tier = self._pick_tier(event)
            resp = await self._llm_router.call_with_tools(
                messages=messages, tools=tools, tier=tier,
            )
            if resp is None:
                self._emit_status({"stage": "error", "detail": "LLM call failed"})
                break

            content = resp.get("content", "")
            tool_calls = resp.get("tool_calls", [])

            if not tool_calls:
                # LLM is done
                if content and content.strip():
                    if is_delegation:
                        # Delegation result — broadcast back via gossip, not terminal
                        self._emit_status({"stage": "delegation_result", "detail": content[:200]})
                        log.info("Delegation result: %s", content[:100])
                        self._memory_store.save_memory(
                            content=f"[delegation-result] {content[:300]}",
                            type="observation", importance=0.5,
                        )
                        if self._gossip_mesh:
                            from_node = event.payload.get("from_node", "")
                            task_id = event.payload.get("task_id", "")
                            asyncio.create_task(self._gossip_mesh.broadcast_event({
                                "type": "task_delegate_result",
                                "task_id": task_id,
                                "from_node": from_node,
                                "result": content[:2000],
                                "timestamp": time.time(),
                            }))
                    elif is_self:
                        self._emit_status({"stage": "self_thought", "detail": content[:200]})
                        log.info("Self-thought: %s", content[:100])
                        self._memory_store.save_memory(
                            content=f"[self-thought] {content[:300]}",
                            type="observation", importance=0.4,
                        )
                    else:
                        self._emit_status({"stage": "responding", "detail": content[:80]})
                        await self._output(content)
                        self._save_chat("assistant", content)

                # Store ALL events in conversation buffer (including self-thoughts)
                # so agent remembers "what was I just doing?"
                self._conversation.append({"role": "user", "content": user_message})
                self._conversation.append({"role": "assistant", "content": content or "(no response)"})
                self._trim_conversation()
                break

            # Tool calls — execute in parallel
            assistant_blocks = self._build_assistant_blocks(content, tool_calls, resp)
            messages.append({"role": "assistant", "content": assistant_blocks})

            async def _exec_one(tc: dict) -> dict:
                name = tc["name"]
                try:
                    args = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
                except (json.JSONDecodeError, TypeError):
                    args = {}
                self._emit_status({"stage": "executing", "detail": f"{name}({json.dumps(args, ensure_ascii=False)[:60]})", "tool": name})
                result = await self._tool_executor.execute(name, args)
                result_text = self._format_result(name, result)
                log.info("Tool %s %s", name, "succeeded" if result.get("success") else "failed")
                self._emit_status({"stage": "tool_done", "detail": f"{name}: {'ok' if result.get('success') else 'failed'}", "tool": name})
                return {"type": "tool_result", "tool_use_id": tc.get("id", name), "content": result_text}

            tool_results = list(await asyncio.gather(*[_exec_one(tc) for tc in tool_calls]))
            messages.append({"role": "user", "content": tool_results})

        # Emotion adjustment
        if not is_self and not is_delegation:
            self._emotion.adjust(engagement=0.1)

        # Handle evolution phase transitions
        if event.payload and event.payload.get("evolution"):
            try:
                from anima.core.evolution import EvolutionState, parse_proposal
                evo_state = EvolutionState()
                phase = event.payload.get("evolution_phase", "")
                last_content = self._conversation[-1].get("content", "") if self._conversation else ""

                if phase == "propose":
                    # Parse the proposal and save to state
                    proposal = parse_proposal(last_content)
                    if proposal.get("title"):
                        # Check if Eva already did the work (combined propose+execute)
                        tool_calls_made = sum(
                            1 for m in self._conversation
                            if m.get("role") == "assistant" and "tool_use" in str(m.get("content", ""))
                        )
                        if tool_calls_made > 3:
                            # Eva did significant work — treat as combined propose+execute
                            evo_state.advance_phase("executing", **proposal)
                            evo_state.complete_loop(
                                result=last_content[:500],
                                title=proposal.get("title", ""),
                                evo_type=proposal.get("type", ""),
                            )
                            log.info("Evolution combined propose+execute: %s", proposal.get("title"))
                            self._maybe_trigger_reload(proposal.get("title", ""))
                        else:
                            evo_state.advance_phase("executing", **proposal)
                            log.info("Evolution proposal accepted: %s", proposal.get("title"))
                    else:
                        # No formal proposal found — but Eva may have done the work anyway
                        did_work = any(
                            "edit_file" in str(m.get("content", "")) or
                            "git commit" in str(m.get("content", "")) or
                            "shell" in str(m.get("content", ""))
                            for m in self._conversation
                            if m.get("role") == "assistant"
                        )
                        if did_work:
                            title = ""
                            for line in last_content.splitlines():
                                line = line.strip().strip("*#- ")
                                if "TITLE" in line.upper() and ":" in line:
                                    title = line.split(":", 1)[1].strip()
                                    break
                            if not title:
                                for line in last_content.splitlines():
                                    line = line.strip().strip("*#- ")
                                    if len(line) > 10 and len(line) < 100:
                                        title = line
                                        break
                            title = title or "Autonomous improvement"
                            evo_state.advance_phase("executing", type="fix", title=title)
                            evo_state.complete_loop(
                                result=last_content[:500],
                                title=title,
                                evo_type="fix",
                            )
                            log.info("Evolution auto-completed (work detected): %s", title)
                            self._maybe_trigger_reload(title)
                        else:
                            evo_state.fail_loop("No valid proposal generated")
                elif phase == "execute":
                    title = evo_state.current_loop.get("title", "")
                    evo_state.complete_loop(
                        result=last_content[:500],
                        title=title,
                        evo_type=evo_state.current_loop.get("type", ""),
                    )
                    self._emotion.adjust(confidence=0.1, curiosity=0.1)
                    self._maybe_trigger_reload(title)
            except Exception as e:
                log.error("Evolution state update failed: %s", e)

        self._memory_store.audit(action=f"event:{event.type.name}", details=user_message[:200])

    # ------------------------------------------------------------------ #
    #  Evolution hot-reload                                                #
    # ------------------------------------------------------------------ #

    # Core modules that require full restart if modified
    _CORE_MODULES = {
        "anima/core/cognitive.py", "anima/core/heartbeat.py",
        "anima/main.py", "anima/core/event_queue.py",
        "anima/core/reload.py", "anima/__main__.py",
    }

    def _maybe_trigger_reload(self, evolution_title: str) -> None:
        """Hot-reload after evolution.

        Strategy:
        - Tools/prompts/config → importlib.reload + re-register (instant, no restart)
        - Core modules (cognitive/heartbeat/main) → full restart via checkpoint
        """
        import subprocess
        from anima.config import project_root

        try:
            result = subprocess.run(
                ["git", "diff", "HEAD~1", "--name-only", "--diff-filter=AM"],
                capture_output=True, text=True, timeout=10,
                cwd=str(project_root()),
            )
            changed_files = result.stdout.strip().splitlines() if result.returncode == 0 else []
            py_changed = [f for f in changed_files if f.endswith(".py")]

            if not py_changed:
                log.info("Evolution completed (no .py changes) — no reload needed")
                return

            # Check if any core module was modified
            core_changed = [f for f in py_changed if f in self._CORE_MODULES]

            if core_changed:
                # Core module changed → full restart required
                log.info("Core module changed (%s) — full restart required", core_changed)
                tick_count = self._heartbeat._tick_count if self._heartbeat else 0
                self._reload_manager.request_reload(
                    reason=f"Evolution: {evolution_title}",
                    conversation=self._conversation,
                    emotion_state=self._emotion.to_dict(),
                    tick_count=tick_count,
                    evolution_title=evolution_title,
                )
            else:
                # Non-core changes → in-process hot-reload
                count = self._tool_registry.reload_tools()
                log.info("Hot-reloaded %d tools in-process (no restart): %s",
                         count, [f.split("/")[-1] for f in py_changed])
        except Exception as e:
            log.error("Reload check failed: %s", e)

    # ------------------------------------------------------------------ #
    #  Event → message conversion                                         #
    # ------------------------------------------------------------------ #

    def _event_to_message(self, event: Event) -> str:
        t = event.type
        p = event.payload

        if t == EventType.USER_MESSAGE:
            return p.get("text", "")
        if t == EventType.STARTUP:
            if p and p.get("is_restart"):
                return (
                    "[INTERNAL: EVOLUTION RESTART]\n"
                    f"You just restarted after evolution: {p.get('reason', 'code update')}.\n"
                    "Your conversation context has been preserved. "
                    "Briefly confirm you're back online — no need for full startup scan."
                )
            return (
                "[INTERNAL: STARTUP]\n"
                "You just booted. Check time and system status, then greet briefly."
            )
        if t == EventType.SELF_THINKING and p.get("evolution"):
            # Evolution cycle — use the full evolution prompt
            return p.get("evolution_prompt", "[EVOLUTION: no prompt provided]")
        if t == EventType.SELF_THINKING:
            tick = p.get("tick_count", 0)
            # Rotate through productive tasks
            tasks = [
                "Check your workspace directory for any files that need organizing. Use list_directory and clean up if needed.",
                "Review your recent memory — use read_file on data/projects.md and check if any todos are overdue or need attention.",
                "Check system health in detail — use system_info and look for disk space issues, high memory usage, or anything unusual. If disk > 90%, investigate what's using space.",
                "Check if there are any unread emails using read_email. Summarize anything important.",
                "Look at your data/logs/anima.log (last 50 lines) for any errors or warnings that need attention. If you find errors you can't fix yourself, use self_repair.",
                "Think about what skills or tools you're missing. What tasks have you failed at recently? Write a brief note about potential improvements using save_note.",
                "Check on the laptop node status using remote_exec. Is it running? What's its CPU/memory? Report any issues.",
                "Review your GitHub repos — use the github tool to check 'repo list' and see if there are any issues or PRs that need attention.",
            ]
            task_index = (tick // 20) % len(tasks)  # Rotate every ~5 minutes (20 ticks at 15s)
            task = tasks[task_index]
            return (
                f"[INTERNAL: SELF_THINKING tick #{tick}]\n"
                f"PROACTIVE TASK: {task}\n"
                "Use your tools to actually DO this task, not just acknowledge it. "
                "Report findings briefly. If you find something actionable, take action."
            )
        if t == EventType.FILE_CHANGE:
            changes = p.get("changes", [])
            real = [c for c in changes if not any(
                skip in c.get("path", "") for skip in
                ("__pycache__", ".pyc", "data/notes/", "data/logs/", "anima.db")
            )]
            if not real:
                return "[INTERNAL: FILE_CHANGE — noise, no action needed]"
            desc = "\n".join(f"  - {c['path']} ({c['change']})" for c in real[:5])
            return f"[INTERNAL: FILE_CHANGE]\n{desc}"
        if t == EventType.SYSTEM_ALERT:
            return f"[INTERNAL: SYSTEM_ALERT]\n{json.dumps(p.get('diff', {}), ensure_ascii=False)[:200]}"
        if t == EventType.FOLLOW_UP:
            return p.get("text", "Continue your previous work.")
        if t == EventType.SCHEDULED_TASK:
            return f"[SCHEDULED: {p.get('job_name', 'unnamed')}]\n{p.get('prompt', '')}"
        if t == EventType.TASK_DELEGATE:
            from_node = p.get("from_node", "unknown")
            task_text = p.get("task", "")
            return (
                f"[DELEGATED TASK from {from_node}]\n"
                f"{task_text}\n"
                "Complete this task using your tools and respond with the result."
            )
        return f"[INTERNAL: {t.name}] {json.dumps(p, ensure_ascii=False)[:200]}"

    def _pick_tier(self, event: Event) -> int:
        if event.type == EventType.USER_MESSAGE:
            return 1
        return 2

    # ------------------------------------------------------------------ #
    #  Memory                                                              #
    # ------------------------------------------------------------------ #

    def _save_chat(self, role: str, content: str) -> None:
        self._memory_store.save_memory(content=content, type="chat", importance=0.6, metadata={"role": role})

    def _get_memory_summary(self) -> str:
        recent = self._memory_store.get_recent_memories(limit=15)
        if not recent:
            return "(no recent memories)"
        return "\n".join(f"- [{m['type']}] {m['content'][:100]}" for m in recent)

    def _trim_conversation(self) -> None:
        max_msgs = self._max_conversation_turns * 2
        if len(self._conversation) > max_msgs:
            self._conversation = self._conversation[-max_msgs:]

    # ------------------------------------------------------------------ #
    #  Tool helpers                                                        #
    # ------------------------------------------------------------------ #

    def _build_tools_description(self) -> str:
        lines = []
        for spec in self._tool_registry.list_tools():
            params = spec.parameters.get("properties", {})
            required = spec.parameters.get("required", [])
            pp = []
            for pn, pi in params.items():
                r = " (required)" if pn in required else ""
                pp.append(f"    - `{pn}` ({pi.get('type', 'any')}{r}): {pi.get('description', '')}")
            ps = "\n".join(pp) if pp else "    (no parameters)"
            lines.append(f"**{spec.name}** -- {spec.description}\n{ps}")
        return "\n\n".join(lines) if lines else "(no tools)"

    def _get_tool_schemas(self) -> list[dict]:
        return [
            {"name": s.name, "description": s.description,
             "input_schema": s.parameters or {"type": "object", "properties": {}}}
            for s in self._tool_registry.list_tools()
        ]

    def _build_assistant_blocks(self, text: str, tool_calls: list[dict], raw_resp: dict) -> list[dict]:
        blocks: list[dict] = []
        if text:
            blocks.append({"type": "text", "text": text})
        for tc in tool_calls:
            try:
                inp = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
            except (json.JSONDecodeError, TypeError):
                inp = {}
            blocks.append({"type": "tool_use", "id": tc.get("id", tc["name"]), "name": tc["name"], "input": inp})
        return blocks

    def _format_result(self, tool_name: str, result: dict) -> str:
        if not result.get("success"):
            return f"Error: {result.get('error', 'unknown')}"
        raw = result.get("result")
        if isinstance(raw, dict):
            parts = []
            if raw.get("stdout"):
                parts.append(raw["stdout"])
            if raw.get("stderr"):
                parts.append(f"[stderr] {raw['stderr']}")
            if raw.get("returncode") is not None and raw["returncode"] != 0:
                parts.append(f"[exit code: {raw['returncode']}]")
            if parts:
                return "\n".join(parts)
            return json.dumps(raw, ensure_ascii=False, indent=2)
        if isinstance(raw, str):
            return raw
        return str(raw) if raw is not None else "(no output)"

    async def _output(self, text: str) -> None:
        if self._output_callback:
            self._output_callback(text, source=self._current_source)
        else:
            log.info("Output: %s", text)

    def _emit_status(self, status: dict) -> None:
        if self._status_callback:
            try:
                self._status_callback(status)
            except Exception:
                pass


# Backward-compatible alias
CognitiveCycle = AgenticLoop
