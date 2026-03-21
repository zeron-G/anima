"""Agentic loop — thin orchestrator delegating to EventRouter, ToolOrchestrator, ResponseHandler.

Sprint 4 refactor (H-24): The old 866-line AgenticLoop has been decomposed into:
  - event_routing.py:     Event classification, tier selection, SELF_THINKING tasks
  - tool_orchestrator.py: Tool execution, schema generation, dynamic selection, streaming
  - response_handler.py:  Output routing, memory persistence, evolution transitions
  - context.py:           CognitiveContext dataclass replacing 11 setters

This file is now a ~200-line thin orchestrator that wires the components together.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from anima.core.context import CognitiveContext
from anima.core.event_queue import EventQueue
from anima.core.event_routing import EventRouter
from anima.core.tool_orchestrator import ToolOrchestrator
from anima.core.response_handler import ResponseHandler
from anima.core.reload import ReloadManager
from anima.emotion.state import EmotionState
from anima.llm.router import LLMRouter
from anima.memory.store import MemoryStore
from anima.models.event import Event, EventType
from anima.perception.snapshot_cache import SnapshotCache
from anima.tools.executor import ToolExecutor
from anima.tools.registry import ToolRegistry
from anima.observability.tracer import get_tracer
from anima.utils.logging import get_logger

log = get_logger("cognitive")


class AgenticLoop:
    """The brain — thin orchestrator for the cognitive pipeline.

    Delegates to three components:
    - EventRouter:       event → message + tier + classification
    - ToolOrchestrator:  LLM + tool loop with max_turns and streaming
    - ResponseHandler:   output routing + memory + emotion + evolution

    All state is shared through CognitiveContext.
    """

    def __init__(
        self,
        event_queue: EventQueue,
        snapshot_cache: SnapshotCache,
        memory_store: MemoryStore,
        emotion_state: EmotionState,
        llm_router: LLMRouter,
        tool_executor: ToolExecutor,
        tool_registry: ToolRegistry,
        config: dict,
    ) -> None:
        # Build context — will be enriched via setters before run()
        self._ctx = CognitiveContext(
            event_queue=event_queue,
            snapshot_cache=snapshot_cache,
            memory_store=memory_store,
            emotion=emotion_state,
            llm_router=llm_router,
            tool_executor=tool_executor,
            tool_registry=tool_registry,
            prompt_compiler=None,  # Set via setter before run()
            config=config,
        )
        # prompt_compiler is set via setter before run() starts

        self._reload_manager = ReloadManager()

        # Components
        self._router = EventRouter()
        self._orchestrator = ToolOrchestrator(tool_executor, tool_registry)
        self._response_handler = ResponseHandler()

    # ── Setters (backward compat with main.py wiring) ──
    # These forward to the context object.

    def set_gossip_mesh(self, gossip_mesh) -> None:
        self._ctx.gossip_mesh = gossip_mesh

    def set_heartbeat(self, heartbeat) -> None:
        self._ctx.heartbeat = heartbeat

    def set_user_activity(self, user_activity) -> None:
        self._ctx.user_activity = user_activity

    def set_idle_scheduler(self, idle_scheduler) -> None:
        self._ctx.idle_scheduler = idle_scheduler

    def set_stream_callback(self, callback) -> None:
        self._ctx.stream_callback = callback

    def set_prompt_compiler(self, compiler) -> None:
        self._ctx.prompt_compiler = compiler

    def set_memory_retriever(self, retriever) -> None:
        self._ctx.memory_retriever = retriever

    def set_conversation_summarizer(self, summarizer) -> None:
        self._ctx.summarizer = summarizer

    def set_importance_scorer(self, scorer) -> None:
        self._ctx.importance_scorer = scorer

    def set_token_budget(self, budget) -> None:
        self._ctx.token_budget = budget

    def set_output_callback(self, callback: Callable[[str], Any]) -> None:
        self._ctx.output_callback = callback

    def set_status_callback(self, callback: Callable[[dict], Any]) -> None:
        self._ctx.status_callback = callback

    @property
    def reload_manager(self) -> ReloadManager:
        return self._reload_manager

    @property
    def _conversation(self) -> list[dict]:
        """Backward compat: expose conversation buffer for main.py checkpoint."""
        return self._ctx.conversation

    @property
    def _emotion(self) -> EmotionState:
        """Backward compat: expose emotion for main.py checkpoint."""
        return self._ctx.emotion

    # ── Lifecycle ──

    def restore_from_checkpoint(self, checkpoint: dict) -> None:
        """Restore emotion state from checkpoint. Conversation restored from DB."""
        emotion = checkpoint.get("emotion")
        if emotion:
            self._ctx.emotion.engagement = emotion.get("engagement", self._ctx.emotion.engagement)
            self._ctx.emotion.confidence = emotion.get("confidence", self._ctx.emotion.confidence)
            self._ctx.emotion.curiosity = emotion.get("curiosity", self._ctx.emotion.curiosity)
            self._ctx.emotion.concern = emotion.get("concern", self._ctx.emotion.concern)
            log.info("Restored emotion state from checkpoint")

    def load_conversation_from_db(self) -> None:
        """Load recent conversation from SQLite on startup."""
        recent = self._ctx.memory_store.get_recent_memories(limit=30, type="chat")
        if not recent:
            return
        recent.reverse()
        for mem in recent:
            meta = {}
            try:
                meta = json.loads(mem.get("metadata_json", "{}"))
            except Exception:
                pass
            role = meta.get("role", "assistant")
            content = mem.get("content", "")
            self._ctx.conversation.append({"role": role, "content": content})
        log.info("Loaded %d conversation turns from DB", len(recent))

    # ── Main loop ──

    async def run(self) -> None:
        """Main event processing loop."""
        from anima.utils.invariants import require
        require(self._ctx.prompt_compiler is not None,
                "prompt_compiler must be set before run(). Call set_prompt_compiler() in main.py.")
        log.info("Agentic loop started (refactored)")
        ctx = self._ctx

        while True:
            event = await ctx.event_queue.get_timeout(timeout=2.0)
            if event is None:
                if self._reload_manager.restart_requested:
                    log.info("Reload requested — triggering shutdown")
                    await ctx.event_queue.put(Event(
                        type=EventType.SHUTDOWN,
                        payload={"restart": True, "reason": self._reload_manager.restart_reason},
                        source="reload",
                    ))
                continue
            if event.type == EventType.SHUTDOWN:
                break

            # Preemption: if this is a low-priority internal event and the queue
            # has a higher-priority event (e.g. USER_MESSAGE), skip this one to
            # prevent internal events from starving user messages.
            if (event.type in (EventType.SELF_THINKING, EventType.FILE_CHANGE,
                               EventType.SYSTEM_ALERT, EventType.IDLE_TASK)):
                front_priority = ctx.event_queue.peek_priority()
                if front_priority is not None and front_priority > event.priority:
                    log.info("Preempting %s (pri=%d) — higher priority event in queue (pri=%d)",
                             event.type.name, event.priority, front_priority)
                    continue

            try:
                await self._process_event(event)
            except Exception as e:
                log.error("Agentic loop error: %s", e, exc_info=True)
                ctx.emit_status({"stage": "error", "detail": str(e)})
            finally:
                ctx.emit_status({"stage": "idle"})

        log.info("Agentic loop stopped")

    async def _process_event(self, event: Event) -> None:
        """Process a single event through the full pipeline."""
        tracer = get_tracer()
        ctx = self._ctx

        with tracer.trace(f"event:{event.type.name}") as trace:
            await self._process_event_traced(event, ctx, trace)

    async def _process_event_traced(self, event: Event, ctx: CognitiveContext, trace) -> None:
        """Inner traced implementation of _process_event."""

        # ── Step 1: Route event ──
        with trace.span("event_routing") as s:
            decision = self._router.route(event, ctx)
            s.set("handled", decision.handled)
            s.set("tier", decision.tier)
            s.set("is_self", decision.is_self)
        if decision.handled:
            return

        user_message = decision.message

        # Record user activity
        if event.type == EventType.USER_MESSAGE and ctx.user_activity:
            ctx.user_activity.record_user_message()

        # Save user message to memory
        if event.type == EventType.USER_MESSAGE and user_message:
            imp = ctx.importance_scorer.score(user_message, "chat_user") if ctx.importance_scorer else 0.6
            await ctx.memory_store.save_memory_async(
                content=user_message, type="chat", importance=imp,
                metadata={"role": "user"},
            )

        # Track in summarizer
        if ctx.summarizer and event.type == EventType.USER_MESSAGE and user_message:
            asyncio.ensure_future(ctx.summarizer.add_message("user", user_message))

        # ── Step 1.5: Perceive user emotion ──
        if event.type == EventType.USER_MESSAGE and user_message:
            from anima.emotion.perception import perceive_user_emotion
            perception = perceive_user_emotion(user_message)
            if perception["adjustments"]:
                ctx.emotion.adjust(**perception["adjustments"])
            ctx.emotion.set_user_state(
                perception["user_state"],
                perception["intensity"],
            )
            log.debug(
                "User emotion: state=%s intensity=%.2f",
                perception["user_state"], perception["intensity"],
            )

        # ── Step 2: Memory retrieval ──
        memory_context = None
        event_type_name = event.type.name
        if ctx.memory_retriever and event_type_name in ("USER_MESSAGE", "SELF_THINKING", "STARTUP", "EVOLUTION", "SCHEDULED_TASK"):
            with trace.span("memory_retrieval") as s:
                try:
                    memory_context = await ctx.memory_retriever.retrieve(
                        query=user_message[:500] if user_message else "",
                        event_type=event_type_name,
                        recent_messages=ctx.get_recent_conversation(4),
                        max_tokens=4000,
                    )
                    if memory_context:
                        s.set("core_tokens", getattr(memory_context, "core_tokens", 0))
                        s.set("episodic_count", len(getattr(memory_context, "episodic", [])))
                except Exception as e:
                    s.set("error", str(e))
                    log.warning("MemoryRetriever failed: %s", e)

        # Compaction flush
        if ctx.summarizer and ctx.token_budget:
            other_tokens = 2000
            if ctx.summarizer.check_overflow(ctx.token_budget.get_conversation_budget(other_tokens)):
                await ctx.summarizer.compaction_flush()

        # ── Step 3: Build prompt via compile() ──
        with trace.span("prompt_compilation") as s:
            snapshot = ctx.snapshot_cache.get_latest()
            system_state = snapshot.get("system_state", {}) if snapshot else {}

            recent_self_thoughts = None
            if event_type_name == "SELF_THINKING":
                recent_self_thoughts = [
                    m["content"][:200]
                    for m in ctx.conversation[-50:]
                    if m.get("role") == "assistant" and m.get("is_self_thought") and m.get("content", "").strip()
                ][-5:]

            _API_KEYS = {"role", "content"}
            conv_buffer = None
            if event_type_name == "USER_MESSAGE":
                if ctx.summarizer:
                    conv_buffer = ctx.summarizer.get_context()
                else:
                    conv_buffer = [{k: v for k, v in m.items() if k in _API_KEYS}
                                   for m in ctx.conversation[-ctx.max_conversation_turns:]]

            tools_desc = self._orchestrator.build_tools_description(event_type_name, user_message) if decision.needs_tools else ""
            # Pass current model name for model_hints adaptation
            _current_model = ctx.llm_router._tier1_model if decision.tier == 1 else ctx.llm_router._tier2_model
            system_prompt, conv_messages = ctx.prompt_compiler.compile(
                event_type_name,
                tools_description=tools_desc,
                system_state=system_state,
                emotion_state=ctx.emotion.to_dict() if event_type_name in ("USER_MESSAGE", "SELF_THINKING") else None,
                memory_context=memory_context,
                conversation_buffer=conv_buffer,
                recent_self_thoughts=recent_self_thoughts,
                model_name=_current_model,
            )
            s.set("system_tokens", len(system_prompt) // 4)

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        for conv_msg in conv_messages:
            role = conv_msg.get("role", "user")
            content = conv_msg.get("content", "")
            # Sanitize: list content (tool_use blocks from prev turns) → string
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        parts.append(block.get("text", "") or f"[{block.get('type', 'block')}]")
                    elif isinstance(block, str):
                        parts.append(block)
                content = "\n".join(parts) if parts else ""
            if not isinstance(content, str):
                content = str(content) if content else ""
            if role == "system":
                messages[0]["content"] += f"\n\n{content}"
            elif role in ("user", "assistant") and content.strip():
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})

        tools = self._orchestrator.get_tool_schemas(event_type_name, user_message) if decision.needs_tools else []

        # ── Step 4: Agentic loop (LLM + tools) ──
        ctx.emit_status({"stage": "thinking", "detail": f"processing {event_type_name}"})

        with trace.span("tool_loop") as s:
            _stream_cb = ctx.stream_callback if not decision.is_self and not decision.is_delegation else None
            loop_result = await self._orchestrator.run_tool_loop(
                llm_router=ctx.llm_router,
                messages=messages,
                tools=tools,
                tier=decision.tier,
                stream_callback=_stream_cb,
                status_callback=ctx.status_callback,
                use_streaming=bool(_stream_cb),
            )
            content = loop_result.get("content", "")
            tool_calls_made = loop_result.get("tool_calls_made", 0)
            loop_error = loop_result.get("error")
            s.set("content_length", len(content))
            s.set("tool_calls", tool_calls_made)

            if loop_error:
                log.warning("LLM loop failed for %s: %s (turns=%d, tools=%d)",
                            event_type_name, loop_error,
                            loop_result.get("turns", 0), tool_calls_made)

        # ── Step 5: Handle response ──
        with trace.span("response_handling"):
            await self._response_handler.handle(
                ctx, content,
                is_self=decision.is_self,
                is_delegation=decision.is_delegation,
                event=event,
                user_message=user_message,
                current_source=decision.source,
                last_chosen_kw=self._router.last_chosen_keyword,
            )

            await ctx.memory_store.audit_async(
                action=f"event:{event.type.name}",
                details=user_message[:200],
            )


# Backward-compatible alias
CognitiveCycle = AgenticLoop
