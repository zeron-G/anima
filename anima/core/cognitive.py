"""Agentic loop — thin orchestrator delegating to Pipeline stages.

Sprint 4 refactor (H-24): The old 866-line AgenticLoop has been decomposed into:
  - event_routing.py:     Event classification, tier selection, SELF_THINKING tasks
  - tool_orchestrator.py: Tool execution, schema generation, dynamic selection, streaming
  - response_handler.py:  Output routing, memory persistence, evolution transitions
  - context.py:           CognitiveContext dataclass replacing 11 setters

Step 2 refactor: _process_event_traced replaced by a Pipeline of 6 composable stages:
  - pipeline.py:          Pipeline/PipelineStage/PipelineContext abstractions
  - stages.py:            EventRouting, EmotionPerception, MemoryRetrieval,
                          PromptCompilation, ToolLoop, ResponseHandling stages

This file is now a thin orchestrator that wires the Pipeline together.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from anima.core.context import CognitiveContext
from anima.core.event_queue import EventQueue
from anima.core.event_routing import EventRouter
from anima.core.pipeline import Pipeline, PipelineContext
from anima.core.stages import (
    EventRoutingStage, EmotionPerceptionStage,
    MemoryRetrievalStage, PromptCompilationStage,
    ToolLoopStage, ResponseHandlingStage,
)
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

        # Pipeline — composable stages replacing inline _process_event_traced
        self._pipeline = Pipeline([
            EventRoutingStage(self._router),
            EmotionPerceptionStage(),
            MemoryRetrievalStage(),
            PromptCompilationStage(self._orchestrator),
            ToolLoopStage(self._orchestrator),
            ResponseHandlingStage(self._response_handler, self._router),
        ])

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
        """Inner traced implementation — delegates to the composable Pipeline."""
        pctx = PipelineContext(event=event, cognitive_ctx=ctx, trace=trace)
        await self._pipeline.run(pctx)


# Backward-compatible alias
CognitiveCycle = AgenticLoop
