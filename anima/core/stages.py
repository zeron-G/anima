"""Pipeline stages — each wraps one section of the old _process_event_traced.

The stages are:
  1. EventRoutingStage     — route event, record user activity, save to memory
  2. EmotionPerceptionStage — perceive user emotion from message text
  3. MemoryRetrievalStage   — retrieve relevant memories for context
  4. PromptCompilationStage — build system prompt, conversation buffer, messages
  5. ToolLoopStage          — run LLM + tool agentic loop
  6. ResponseHandlingStage  — handle response output, audit to memory
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from anima.core.pipeline import PipelineContext, PipelineStage
from anima.models.event import EventType
from anima.utils.logging import get_logger

if TYPE_CHECKING:
    from anima.core.event_routing import EventRouter
    from anima.core.tool_orchestrator import ToolOrchestrator
    from anima.core.response_handler import ResponseHandler

log = get_logger("stages")


class EventRoutingStage(PipelineStage):
    """Stage 1: Route event, record user activity, save user message to memory.

    Wraps the '# Step 1: Route event' block from _process_event_traced.
    """
    name = "event_routing"

    def __init__(self, router: EventRouter) -> None:
        self._router = router

    async def process(self, pctx: PipelineContext) -> PipelineContext:
        event = pctx.event
        ctx = pctx.cognitive_ctx
        trace = pctx.trace

        # Route the event
        with trace.span("event_routing") as s:
            decision = self._router.route(event, ctx)
            s.set("handled", decision.handled)
            s.set("tier", decision.tier)
            s.set("is_self", decision.is_self)

        pctx.decision = decision

        if decision.handled:
            pctx.handled = True
            return pctx

        pctx.user_message = decision.message

        # Record user activity
        if event.type == EventType.USER_MESSAGE and ctx.user_activity:
            ctx.user_activity.record_user_message()

        # Save user message to memory
        if event.type == EventType.USER_MESSAGE and pctx.user_message:
            imp = ctx.importance_scorer.score(pctx.user_message, "chat_user") if ctx.importance_scorer else 0.6
            await ctx.memory_store.save_memory_async(
                content=pctx.user_message, type="chat", importance=imp,
                metadata={"role": "user"},
            )

        # Track in summarizer
        if ctx.summarizer and event.type == EventType.USER_MESSAGE and pctx.user_message:
            asyncio.ensure_future(ctx.summarizer.add_message("user", pctx.user_message))

        return pctx


class EmotionPerceptionStage(PipelineStage):
    """Stage 2: Perceive user emotion from the message text.

    Wraps the '# Step 1.5: Perceive user emotion' block.
    Only runs for USER_MESSAGE events with non-empty text.
    """
    name = "emotion_perception"

    async def process(self, pctx: PipelineContext) -> PipelineContext:
        event = pctx.event
        ctx = pctx.cognitive_ctx

        if event.type == EventType.USER_MESSAGE and pctx.user_message:
            from anima.emotion.perception import perceive_user_emotion
            perception = perceive_user_emotion(pctx.user_message)
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

        return pctx


class MemoryRetrievalStage(PipelineStage):
    """Stage 3: Retrieve relevant memories for context.

    Wraps the '# Step 2: Memory retrieval' block plus compaction flush.
    """
    name = "memory_retrieval"

    async def process(self, pctx: PipelineContext) -> PipelineContext:
        event = pctx.event
        ctx = pctx.cognitive_ctx
        trace = pctx.trace

        event_type_name = event.type.name
        if ctx.memory_retriever and event_type_name in ("USER_MESSAGE", "SELF_THINKING", "STARTUP", "EVOLUTION", "SCHEDULED_TASK"):
            with trace.span("memory_retrieval") as s:
                try:
                    pctx.memory_context = await ctx.memory_retriever.retrieve(
                        query=pctx.user_message[:500] if pctx.user_message else "",
                        event_type=event_type_name,
                        recent_messages=ctx.get_recent_conversation(4),
                        max_tokens=4000,
                    )
                    if pctx.memory_context:
                        s.set("core_tokens", getattr(pctx.memory_context, "core_tokens", 0))
                        s.set("episodic_count", len(getattr(pctx.memory_context, "episodic", [])))
                except Exception as e:
                    s.set("error", str(e))
                    log.warning("MemoryRetriever failed: %s", e)

        # Compaction flush
        if ctx.summarizer and ctx.token_budget:
            other_tokens = 2000
            if ctx.summarizer.check_overflow(ctx.token_budget.get_conversation_budget(other_tokens)):
                await ctx.summarizer.compaction_flush()

        return pctx


class PromptCompilationStage(PipelineStage):
    """Stage 4: Build system prompt, conversation buffer, and final messages.

    Wraps the '# Step 3: Build prompt via compile()' block.
    """
    name = "prompt_compilation"

    def __init__(self, orchestrator: ToolOrchestrator) -> None:
        self._orchestrator = orchestrator

    async def process(self, pctx: PipelineContext) -> PipelineContext:
        event = pctx.event
        ctx = pctx.cognitive_ctx
        trace = pctx.trace
        decision = pctx.decision
        event_type_name = event.type.name

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

            tools_desc = self._orchestrator.build_tools_description(event_type_name, pctx.user_message) if decision.needs_tools else ""
            # Pass current model name for model_hints adaptation
            _current_model = ctx.llm_router._tier1_model if decision.tier == 1 else ctx.llm_router._tier2_model
            system_prompt, conv_messages = ctx.prompt_compiler.compile(
                event_type_name,
                tools_description=tools_desc,
                system_state=system_state,
                emotion_state=ctx.emotion.to_dict() if event_type_name in ("USER_MESSAGE", "SELF_THINKING") else None,
                memory_context=pctx.memory_context,
                conversation_buffer=conv_buffer,
                recent_self_thoughts=recent_self_thoughts,
                model_name=_current_model,
            )
            s.set("system_tokens", len(system_prompt) // 4)

        pctx.system_prompt = system_prompt
        pctx.conv_messages = conv_messages

        # Build final messages list
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        for conv_msg in conv_messages:
            role = conv_msg.get("role", "user")
            content = conv_msg.get("content", "")
            # Sanitize: list content (tool_use blocks from prev turns) -> string
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
        messages.append({"role": "user", "content": pctx.user_message})

        pctx.messages = messages
        pctx.tools = self._orchestrator.get_tool_schemas(event_type_name, pctx.user_message) if decision.needs_tools else []

        return pctx


class ToolLoopStage(PipelineStage):
    """Stage 5: Run the LLM + tool agentic loop.

    Wraps the '# Step 4: Agentic loop (LLM + tools)' block.
    """
    name = "tool_loop"

    def __init__(self, orchestrator: ToolOrchestrator) -> None:
        self._orchestrator = orchestrator

    async def process(self, pctx: PipelineContext) -> PipelineContext:
        ctx = pctx.cognitive_ctx
        trace = pctx.trace
        decision = pctx.decision
        event_type_name = pctx.event.type.name

        ctx.emit_status({"stage": "thinking", "detail": f"processing {event_type_name}"})

        with trace.span("tool_loop") as s:
            _stream_cb = ctx.stream_callback if not decision.is_self and not decision.is_delegation else None
            loop_result = await self._orchestrator.run_tool_loop(
                llm_router=ctx.llm_router,
                messages=pctx.messages,
                tools=pctx.tools,
                tier=decision.tier,
                stream_callback=_stream_cb,
                status_callback=ctx.status_callback,
                use_streaming=bool(_stream_cb),
            )
            pctx.content = loop_result.get("content", "")
            pctx.tool_calls_made = loop_result.get("tool_calls_made", 0)
            pctx.loop_error = loop_result.get("error")
            s.set("content_length", len(pctx.content))
            s.set("tool_calls", pctx.tool_calls_made)

            if pctx.loop_error:
                log.warning("LLM loop failed for %s: %s (turns=%d, tools=%d)",
                            event_type_name, pctx.loop_error,
                            loop_result.get("turns", 0), pctx.tool_calls_made)

        return pctx


class ResponseHandlingStage(PipelineStage):
    """Stage 6: Handle response output, audit to memory.

    Wraps the '# Step 5: Handle response' block.
    """
    name = "response_handling"

    def __init__(self, response_handler: ResponseHandler, router: EventRouter) -> None:
        self._response_handler = response_handler
        self._router = router

    async def process(self, pctx: PipelineContext) -> PipelineContext:
        ctx = pctx.cognitive_ctx
        trace = pctx.trace
        decision = pctx.decision
        event = pctx.event

        with trace.span("response_handling"):
            await self._response_handler.handle(
                ctx, pctx.content,
                is_self=decision.is_self,
                is_delegation=decision.is_delegation,
                event=event,
                user_message=pctx.user_message,
                current_source=decision.source,
                last_chosen_kw=self._router.last_chosen_keyword,
            )

            await ctx.memory_store.audit_async(
                action=f"event:{event.type.name}",
                details=pctx.user_message[:200],
            )

        return pctx
