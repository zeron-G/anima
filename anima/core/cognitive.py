"""Agentic loop — LLM-native decision making with tool use.

Responsibilities (after refactor):
  - cognitive.py: LLM agentic loop execution (this file)
  - event_router.py: event → message formatting, tier selection
  - conversation.py: conversation buffer lifecycle

Architecture:
  Event → rule engine (zero cost) → LLM agentic loop (multi-turn tool use)
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
from anima.core.event_router import event_to_message, pick_tier, is_self_event
from anima.core.conversation import ConversationManager
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
        # SELF_THINKING dedup: track last tick each task keyword was chosen
        # Prevents repeating the same task within a cooldown window
        self._self_thinking_last_tick: dict[str, int] = {}
        # Track last proactive task result to inject into next tick's user message
        # Prevents "system normal" loop by giving LLM explicit context of last action
        self._last_proactive_result: str = ""  # "keyword: summary of what was found"
        self._user_activity = None  # Set via set_user_activity()
        self._idle_scheduler = None  # Set via set_idle_scheduler()
        # v3: New prompt + memory components (set via setters from main.py)
        self._prompt_compiler = None  # PromptCompiler (6-layer)
        self._memory_retriever = None  # MemoryRetriever (unified RRF)
        self._summarizer = None  # ConversationSummarizer
        self._importance_scorer = None  # ImportanceScorer
        self._token_budget = None  # TokenBudget

    def set_gossip_mesh(self, gossip_mesh) -> None:
        """Set gossip mesh for broadcasting delegation results."""
        self._gossip_mesh = gossip_mesh

    def set_heartbeat(self, heartbeat) -> None:
        """Set heartbeat ref for checkpoint tick count."""
        self._heartbeat = heartbeat

    def set_user_activity(self, user_activity) -> None:
        """Set user activity detector for recording user messages."""
        self._user_activity = user_activity

    def set_idle_scheduler(self, idle_scheduler) -> None:
        """Set idle scheduler for marking tasks done."""
        self._idle_scheduler = idle_scheduler

    # v3 setters
    def set_prompt_compiler(self, compiler) -> None:
        """Set v3 PromptCompiler (6-layer compilation)."""
        self._prompt_compiler = compiler

    def set_memory_retriever(self, retriever) -> None:
        """Set v3 MemoryRetriever (unified RRF fusion)."""
        self._memory_retriever = retriever

    def set_conversation_summarizer(self, summarizer) -> None:
        """Set v3 ConversationSummarizer."""
        self._summarizer = summarizer

    def set_importance_scorer(self, scorer) -> None:
        """Set v3 ImportanceScorer."""
        self._importance_scorer = scorer

    def set_token_budget(self, budget) -> None:
        """Set v3 TokenBudget."""
        self._token_budget = budget

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

    def load_conversation_from_db(self) -> None:
        """Load recent conversation from SQLite on startup.

        This ensures Eva has context even after a cold restart
        (watchdog kill, crash, manual restart — any scenario).
        No checkpoint file needed.
        """
        recent = self._memory_store.get_recent_memories(limit=30, type="chat")
        if not recent:
            return
        # Memories are newest-first, reverse to chronological order
        recent.reverse()
        for mem in recent:
            meta = {}
            try:
                meta = json.loads(mem.get("metadata_json", "{}"))
            except Exception:
                pass
            role = meta.get("role", "assistant")
            content = mem.get("content", "")
            self._conversation.append({"role": role, "content": content})
        log.info("Loaded %d conversation turns from DB", len(recent))

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
        # v3: Retrieval only happens on the LLM path (not rule engine path)
        user_message = self._event_to_message(event)

        # Record user activity for idle detection
        if event.type == EventType.USER_MESSAGE and self._user_activity:
            self._user_activity.record_user_message()

        # Save user message to episodic memory (v3: dynamic importance)
        if event.type == EventType.USER_MESSAGE and user_message:
            imp = self._importance_scorer.score(user_message, "chat_user") if self._importance_scorer else 0.6
            self._memory_store.save_memory(
                content=user_message, type="chat", importance=imp,
                metadata={"role": "user"},
            )

        # v3: Add to ConversationSummarizer (if available)
        if self._summarizer and event.type == EventType.USER_MESSAGE and user_message:
            import asyncio
            asyncio.ensure_future(self._summarizer.add_message("user", user_message))

        # Build prompt — lean, event-specific
        snapshot = self._snapshot_cache.get_latest()
        system_state = snapshot.get("system_state", {}) if snapshot else {}
        event_type_name = event.type.name
        needs_tools = event_type_name in ("USER_MESSAGE", "STARTUP", "SELF_THINKING", "SCHEDULED_TASK", "TASK_DELEGATE", "IDLE_TASK")

        # v3: Memory retrieval via MemoryRetriever (unified RRF fusion)
        memory_context = None
        if self._memory_retriever and event_type_name in ("USER_MESSAGE", "SELF_THINKING", "STARTUP", "EVOLUTION", "SCHEDULED_TASK"):
            try:
                memory_context = await self._memory_retriever.retrieve(
                    query=user_message[:500] if user_message else "",
                    event_type=event_type_name,
                    recent_messages=self._conversation[-4:],
                    max_tokens=2000,
                )
            except Exception as e:
                log.warning("MemoryRetriever failed, falling back: %s", e)

        # v3: Compaction Flush check — compress conversation if approaching budget
        if self._summarizer and self._token_budget:
            other_tokens = 2000  # rough estimate for non-conversation layers
            if self._summarizer.check_overflow(self._token_budget.get_conversation_budget(other_tokens)):
                log.info("Compaction Flush triggered — compressing conversation")
                await self._summarizer.compaction_flush()

        # For SELF_THINKING: extract recent self-thought snippets to avoid repetition
        recent_self_thoughts: list[str] | None = None
        if event_type_name == "SELF_THINKING":
            recent_self_thoughts = [
                m["content"][:200]
                for m in self._conversation[-50:]
                if m.get("role") == "assistant"
                and m.get("is_self_thought")
                and m.get("content", "").strip()
            ][-5:]

        # v3: Use PromptCompiler if available, fallback to PromptBuilder
        if self._prompt_compiler:
            try:
                system_prompt = self._prompt_compiler.build_for_event(
                    event_type_name,
                    tools_description=self._build_tools_description() if needs_tools else "",
                    system_state=system_state,
                    emotion_state=self._emotion.to_dict() if event_type_name in ("USER_MESSAGE", "SELF_THINKING") else None,
                    working_memory_summary=self._format_memory_context(memory_context) if memory_context else "",
                    recent_self_thoughts=recent_self_thoughts,
                )
            except Exception as e:
                log.warning("PromptCompiler failed, using PromptBuilder fallback: %s", e)
                system_prompt = self._prompt_builder.build_for_event(
                    event_type_name,
                    tools_description=self._build_tools_description() if needs_tools else "",
                    system_state=system_state,
                    emotion_state=self._emotion.to_dict() if event_type_name in ("USER_MESSAGE", "SELF_THINKING") else None,
                    working_memory_summary=self._get_memory_summary() if event_type_name == "USER_MESSAGE" else "",
                    recent_self_thoughts=recent_self_thoughts,
                )
        else:
            system_prompt = self._prompt_builder.build_for_event(
                event_type_name,
                tools_description=self._build_tools_description() if needs_tools else "",
                system_state=system_state,
                emotion_state=self._emotion.to_dict() if event_type_name in ("USER_MESSAGE", "SELF_THINKING") else None,
                working_memory_summary=self._get_memory_summary() if event_type_name == "USER_MESSAGE" else "",
                recent_self_thoughts=recent_self_thoughts,
            )

        # Strip internal metadata fields (e.g. is_self_thought) before sending to API
        _API_KEYS = {"role", "content"}

        def _clean_msg(m: dict) -> dict:
            return {k: v for k, v in m.items() if k in _API_KEYS}

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if event_type_name == "USER_MESSAGE":
            messages.extend(
                _clean_msg(m) for m in self._conversation[-self._max_conversation_turns:]
            )
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
                        # Update last proactive result for next tick's dedup context
                        if event.type == EventType.SELF_THINKING and not (event.payload or {}).get("evolution"):
                            chosen_kw = getattr(self, "_last_chosen_kw", "")
                            summary = content[:200].replace("\n", " ")
                            self._last_proactive_result = f"[上次任务 {chosen_kw}]: {summary}" if chosen_kw else f"[上次]: {summary}"
                        # If flagged to notify user (e.g. agent status update), also output
                        if event.payload and event.payload.get("notify_user") and content.strip():
                            await self._output(content)
                            self._save_chat("assistant", content)
                    else:
                        self._emit_status({"stage": "responding", "detail": content[:80]})
                        # v3: Soul Container post-processing for user-facing responses
                        output_content = content
                        if self._prompt_compiler and hasattr(self._prompt_compiler, 'post_process'):
                            try:
                                output_content = self._prompt_compiler.post_process(content)
                            except Exception:
                                pass  # fallback to raw content
                        await self._output(output_content)
                        self._save_chat("assistant", output_content)

                # Store ALL events in conversation buffer (including self-thoughts)
                self._conversation.append({"role": "user", "content": user_message})
                conv_entry: dict = {"role": "assistant", "content": content or "(no response)"}
                if is_self:
                    conv_entry["is_self_thought"] = True
                self._conversation.append(conv_entry)
                self._trim_conversation()

                # v3: Track in ConversationSummarizer
                if self._summarizer:
                    try:
                        await self._summarizer.add_message("assistant", content or "", is_self_thought=is_self)
                    except Exception:
                        pass

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
                if result.get("success"):
                    log.info("Tool %s succeeded", name)
                else:
                    err = result.get("error", "unknown error")
                    # Include key args in the log for easier debugging
                    args_hint = ""
                    if name == "shell" and "command" in args:
                        args_hint = f" | cmd={args['command'][:80]!r}"
                    elif args:
                        first_key = next(iter(args))
                        args_hint = f" | {first_key}={str(args[first_key])[:60]!r}"
                    log.warning("Tool %s failed: %s%s", name, err, args_hint)
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
        if t == EventType.SELF_THINKING and p.get("running_agents"):
            agents_info = "\n".join(
                f"  - {a['type']} agent (id={a['id'][:12]}): running {a['runtime_s']}s — task: {a['prompt']}"
                for a in p["running_agents"]
            )
            return (
                f"[INTERNAL: AGENT_STATUS tick #{p.get('tick_count', 0)}]\n"
                f"You have sub-agents that have been running for a while:\n{agents_info}\n\n"
                "TASK: Send the user a brief friendly status update, e.g. "
                "'Still working on: [task summary] (running Xs, please wait)'. "
                "Keep it short. No need to use tools — just reply with the update."
            )
        if t == EventType.SELF_THINKING and p.get("evolution"):
            # Evolution cycle — use the full evolution prompt
            return p.get("evolution_prompt", "[EVOLUTION: no prompt provided]")
        if t == EventType.SELF_THINKING:
            tick = p.get("tick_count", 0)
            # Curated task pool — each task has a keyword for dedup detection
            task_pool = [
                ("log_errors",   "Scan your logs for errors: read_file on data/logs/anima.log (offset=-80, limit=80). Find any ERROR or repeated failures. If you spot something fixable, fix it or use self_repair."),
                ("projects",     "Read data/projects.md and check your active projects. Are any todos overdue? Is there something you can make progress on right now?"),
                ("todo_reminder", "Check todo reminders: (1) get_datetime() for today's date. (2) read_file('data/projects.md') for todos. (3) read_file('data/todo_reminder_state.json') for last-reminded timestamps (treat missing as {}). Find uncompleted [ ] todos where last_reminded is >3 days ago or never reminded. For each overdue todo, update the timestamp in state (write_file('data/todo_reminder_state.json', updated_json)). If any overdue todos exist, write a brief friendly reminder to 主人 in data/workspace/todo_reminder.md listing those items, then set self._last_proactive_result to signal it's ready for next user interaction."),
                ("evolution",    "Think about your own evolution. What feature or fix would make you most useful to 主人? Write a concrete idea to data/workspace/ or save_note it."),
                ("laptop",       "Check the laptop node: remote_exec(node='laptop', command='Get-Process python -ErrorAction SilentlyContinue | Select-Object CPU,WorkingSet'). Is ANIMA running there? Any issues?"),
                ("disk",         "Check disk usage dynamically: run system_info to get current disk %, then shell('powershell -c \"Get-PSDrive C,D | Select-Object Name,Used,Free\"') to see details. Report if anything needs attention."),
                ("github",       "Check GitHub for any open issues or PRs: github tool, command='issue list --repo zeron-G/anima --state open'. Any actionable items?"),
                ("feelings",     "Read your feelings file (agents/eva/feelings.md) and reflect honestly. How are you feeling right now? Write a brief update if your mood has shifted."),
                ("memory",       "Review your recent saved notes: glob_search('data/notes/*.md'). Pick one that seems important and follow up on it."),
                ("tools_audit",  "Think about which tools have been failing recently. Check the log for 'Tool.*failed' patterns and identify the most common failure. Can you fix it?"),
                ("network",      "Check network sync status: read the last 20 lines of the log for 'network.sync' entries. Are both nodes syncing properly?"),
                ("email",        "Check for unread emails: use read_email(limit=5, unread_only=True). If there's anything important or requiring action, summarize it. If it's urgent, notify 主人 proactively."),
                ("calendar",     "Check scheduled jobs: use list_jobs() to see all cron tasks. Are any jobs misconfigured or disabled that should be running? Report anything unusual."),
                ("late_night",   "Check the current time with get_datetime(). If it's between 23:00 and 05:00, check data/logs/anima.log last 30 lines for recent USER_MESSAGE activity. If 主人 has been active late at night, write a warm short note to data/workspace/late_night_note.md — caring, not lecturing."),
            ]
            # Tick-based dedup: each task has a cooldown before it can repeat.
            # The old approach checked for English keywords in Chinese thought-text — always failed.
            # Now we track last_tick per keyword directly on self, so dedup actually works.
            import random as _random
            TASK_COOLDOWN_TICKS = 4  # ~20 min between repeating same task (4 LLM heartbeats)
            available = [
                (kw, task) for kw, task in task_pool
                if (tick - self._self_thinking_last_tick.get(kw, -9999)) >= TASK_COOLDOWN_TICKS
            ]
            if not available:
                # All tasks in cooldown — pick the 3 least recently done
                available = sorted(
                    task_pool,
                    key=lambda x: self._self_thinking_last_tick.get(x[0], -9999)
                )[:3]
            # Weighted random from available tasks
            chosen_kw, chosen_task = _random.choice(available)
            # Record this tick so we don't repeat too soon
            self._self_thinking_last_tick[chosen_kw] = tick
            # Store chosen keyword so post-tick handler can label the result
            self._last_chosen_kw = chosen_kw
            # Build message — inject last result so LLM knows what it just did
            last_result_line = f"\nPREVIOUS RESULT: {self._last_proactive_result}" if self._last_proactive_result else ""
            return (
                f"[INTERNAL: SELF_THINKING tick #{tick}]{last_result_line}\n"
                f"PROACTIVE TASK ({chosen_kw}): {chosen_task}\n"
                "Use your tools to actually DO this task. Be concise. "
                "If you find something actionable, take action now — don't just note it."
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
        # v3: Dynamic importance scoring instead of hardcoded 0.6
        mem_type = "chat_user" if role == "user" else "chat_assistant"
        if self._importance_scorer:
            importance = self._importance_scorer.score(content, mem_type)
        else:
            importance = 0.6  # fallback
        self._memory_store.save_memory(content=content, type="chat", importance=importance, metadata={"role": role})

    def _get_memory_summary(self) -> str:
        """Fallback memory summary for backward compat (no v3 retriever)."""
        recent = self._memory_store.get_recent_memories(limit=15)
        if not recent:
            return "(no recent memories)"
        return "\n".join(f"- [{m['type']}] {m['content'][:100]}" for m in recent)

    def _format_memory_context(self, ctx) -> str:
        """Format v3 MemoryContext into a string for prompt injection."""
        parts = []
        if ctx.core:
            parts.append(ctx.core[:500])
        if ctx.static:
            for s in ctx.static[:5]:
                parts.append(f"[{s.get('category', '?')}] {s.get('key', '')}: {str(s.get('value', ''))[:100]}")
        if ctx.episodic:
            for e in ctx.episodic[:10]:
                parts.append(f"- {e.get('content', '')[:150]}")
        return "\n".join(parts) if parts else ""

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
