"""Response handling — routes LLM output to the correct destination.

Extracted from the old monolithic AgenticLoop (H-24 refactor).
Responsibilities:
  - Output routing (delegation → gossip, self → memory, user → terminal)
  - Memory persistence with dynamic importance scoring
  - Conversation buffer management
  - Emotion adjustment after interactions
  - Soul Container post-processing for user-facing responses
  - Evolution phase state transitions
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from typing import Any, TYPE_CHECKING

from anima.utils.logging import get_logger

if TYPE_CHECKING:
    from anima.core.context import CognitiveContext
    from anima.models.event import Event

log = get_logger("response_handler")

# Core modules that require full restart if modified during evolution.
# Non-core .py changes get in-process hot-reload instead.
_CORE_MODULES = {
    "anima/core/cognitive.py", "anima/core/heartbeat.py",
    "anima/main.py", "anima/core/event_queue.py",
    "anima/core/reload.py", "anima/__main__.py",
}


class ResponseHandler:
    """Handles LLM response output, persistence, and side effects.

    Stateless — all mutable state lives on *CognitiveContext* or is
    returned to the caller.  The only instance state is a cache of the
    last proactive-task result string used for SELF_THINKING dedup.
    """

    def __init__(self) -> None:
        # SELF_THINKING dedup: updated after each self-thought so the
        # next heartbeat tick can inject "what I did last time" context.
        self.last_proactive_result: str = ""

    # ------------------------------------------------------------------ #
    #  Public entry point                                                  #
    # ------------------------------------------------------------------ #

    async def handle(
        self,
        ctx: CognitiveContext,
        content: str,
        *,
        is_self: bool = False,
        is_delegation: bool = False,
        event: Event,
        user_message: str = "",
        current_source: str = "",
        last_chosen_kw: str = "",
    ) -> None:
        """Route the LLM response to its destination.

        Args:
            ctx: CognitiveContext with all dependencies.
            content: LLM response text.
            is_self: True for internal events (self-thinking, startup, etc.).
            is_delegation: True for cross-node delegated tasks.
            event: The original Event object.
            user_message: The original user/event message text.
            current_source: Event source key for output routing.
            last_chosen_kw: SELF_THINKING keyword that was chosen (dedup).
        """

        # -- 1. Route the textual content --------------------------------
        if content and content.strip():
            if is_delegation:
                await self._handle_delegation(ctx, content, event)
            elif is_self:
                await self._handle_self_thought(
                    ctx, content, event, last_chosen_kw,
                )
            else:
                await self._handle_user_response(ctx, content, current_source)
        elif not is_self and not is_delegation and user_message:
            # LLM returned empty content for a user message — notify user
            # instead of silently dropping their message.
            log.warning("Empty LLM response for user message: %s", user_message[:80])
            fallback = "抱歉，我刚才处理出了点问题，没能正常回复你。可以再说一次吗？ (´;ω;`)"
            if ctx.output_callback:
                try:
                    ctx.output_callback(fallback)
                except Exception:
                    pass
            content = fallback  # So conversation buffer gets the fallback

        # -- 2. Conversation buffer: always append both sides ------------
        ctx.conversation.append({"role": "user", "content": user_message})
        conv_entry: dict = {"role": "assistant", "content": content or "(no response)"}
        if is_self:
            conv_entry["is_self_thought"] = True
        ctx.conversation.append(conv_entry)
        ctx.trim_conversation()

        # -- 3. ConversationSummarizer tracking --------------------------
        if ctx.summarizer:
            try:
                await ctx.summarizer.add_message(
                    "assistant", content or "", is_self_thought=is_self,
                )
            except Exception as e:
                log.debug("ConversationSummarizer tracking failed: %s", e)

        # -- 4. Emotion adjustment on user interaction -------------------
        # H-23 fix: extract emotion from response instead of flat +0.1
        if not is_self and not is_delegation:
            from anima.emotion.feedback import extract_emotion_adjustments

            tool_calls_made = sum(
                1
                for m in ctx.conversation
                if (
                    m.get("role") == "assistant"
                    and "tool_use" in str(m.get("content", ""))
                )
            )
            adjustments = extract_emotion_adjustments(
                content,
                had_tool_calls=bool(tool_calls_made),
                tool_success_rate=1.0,  # simplified for now
            )
            if adjustments:
                ctx.emotion.adjust(**adjustments)
            else:
                ctx.emotion.adjust(engagement=0.08)  # Minimal default

        # -- 5. Evolution phase transitions ------------------------------
        if event.payload and event.payload.get("evolution"):
            self._handle_evolution_transition(ctx, event)

        # -- 6. Audit trail ---------------------------------------------
        await ctx.memory_store.audit_async(
            action=f"event:{event.type.name}", details=user_message[:200],
        )

    # ------------------------------------------------------------------ #
    #  Output routing helpers                                              #
    # ------------------------------------------------------------------ #

    async def _handle_delegation(
        self,
        ctx: CognitiveContext,
        content: str,
        event: Event,
    ) -> None:
        """Delegation result → broadcast via gossip mesh, not terminal."""
        ctx.emit_status({"stage": "delegation_result", "detail": content[:200]})
        log.info("Delegation result: %s", content[:100])

        # Persist a summary to episodic memory
        await ctx.memory_store.save_memory_async(
            content=f"[delegation-result] {content[:300]}",
            type="observation",
            importance=0.5,
        )

        # Broadcast result back to the originating node
        if ctx.gossip_mesh:
            from_node = event.payload.get("from_node", "")
            task_id = event.payload.get("task_id", "")
            asyncio.create_task(ctx.gossip_mesh.broadcast_event({
                "type": "task_delegate_result",
                "task_id": task_id,
                "from_node": from_node,
                "result": content[:2000],
                "timestamp": time.time(),
            }))

    async def _handle_self_thought(
        self,
        ctx: CognitiveContext,
        content: str,
        event: Event,
        last_chosen_kw: str,
    ) -> None:
        """Self-thought → memory + activity feed, NOT terminal (unless notify_user)."""
        from anima.models.event import EventType

        ctx.emit_status({"stage": "self_thought", "detail": content[:200]})
        log.info("Self-thought: %s", content[:100])

        await ctx.memory_store.save_memory_async(
            content=f"[self-thought] {content[:300]}",
            type="observation",
            importance=0.4,
        )

        # Update last proactive result for next tick's dedup context.
        # Only for regular SELF_THINKING events (not evolution ticks).
        if (
            event.type == EventType.SELF_THINKING
            and not (event.payload or {}).get("evolution")
        ):
            summary = content[:200].replace("\n", " ")
            if last_chosen_kw:
                self.last_proactive_result = f"[上次任务 {last_chosen_kw}]: {summary}"
            else:
                self.last_proactive_result = f"[上次]: {summary}"

        # If flagged to notify user (e.g. agent status update, proactive outreach)
        if event.payload and event.payload.get("notify_user") and content.strip():
            # Apply Soul Container post-processing for user-facing messages
            output_content = content
            if ctx.prompt_compiler and hasattr(ctx.prompt_compiler, "post_process"):
                try:
                    output_content = ctx.prompt_compiler.post_process(content)
                except Exception:
                    pass
            await self._output(ctx, output_content, current_source="")
            await self._save_chat(ctx, "assistant", output_content)

            # Push as typed proactive message for frontend ProactiveTag display
            proactive_type = (event.payload or {}).get("proactive_type", "self_thinking")
            ctx.emit_status({
                "stage": "proactive",
                "detail": output_content[:200],
                "proactive_type": proactive_type,
            })

    async def _handle_user_response(
        self,
        ctx: CognitiveContext,
        content: str,
        current_source: str,
    ) -> None:
        """User response → Soul Container post-process → terminal + dashboard."""
        ctx.emit_status({"stage": "responding", "detail": content[:80]})

        # Soul Container post-processing for user-facing responses
        output_content = content
        if ctx.prompt_compiler and hasattr(ctx.prompt_compiler, "post_process"):
            try:
                output_content = ctx.prompt_compiler.post_process(content)
            except Exception as e:
                log.debug("Soul Container post-processing failed: %s", e)

        await self._output(ctx, output_content, current_source=current_source)
        await self._save_chat(ctx, "assistant", output_content)

    # ------------------------------------------------------------------ #
    #  Memory persistence                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    async def _save_chat(ctx: CognitiveContext, role: str, content: str) -> None:
        """Save a chat message to episodic memory with dynamic importance."""
        mem_type = "chat_user" if role == "user" else "chat_assistant"
        importance = (
            ctx.importance_scorer.score(content, mem_type)
            if ctx.importance_scorer
            else 0.6
        )
        await ctx.memory_store.save_memory_async(
            content=content,
            type="chat",
            importance=importance,
            metadata={"role": role},
        )

    # ------------------------------------------------------------------ #
    #  Output helpers                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    async def _output(
        ctx: CognitiveContext, text: str, *, current_source: str = "",
    ) -> None:
        """Emit text to the user-facing output channel."""
        if ctx.output_callback:
            ctx.output_callback(text, source=current_source)
        else:
            log.info("Output: %s", text)

    # ------------------------------------------------------------------ #
    #  Evolution phase transitions                                         #
    # ------------------------------------------------------------------ #

    def _handle_evolution_transition(
        self, ctx: CognitiveContext, event: Event,
    ) -> None:
        """Parse evolution results and advance the EvolutionState machine.

        Three paths:
          1. propose → formal proposal parsed → advance to 'executing'
          2. propose → no formal proposal, but tool work detected → auto-complete
          3. execute → complete the loop, adjust emotion
        """
        try:
            from anima.core.evolution import EvolutionState, parse_proposal

            evo_state = EvolutionState()
            phase = event.payload.get("evolution_phase", "")
            last_content = (
                ctx.conversation[-1].get("content", "")
                if ctx.conversation
                else ""
            )

            if phase == "propose":
                self._handle_propose_phase(
                    ctx, evo_state, last_content, parse_proposal,
                )
            elif phase == "execute":
                self._handle_execute_phase(ctx, evo_state, last_content)

        except Exception as e:
            log.error("Evolution state update failed: %s", e)

    def _handle_propose_phase(
        self,
        ctx: CognitiveContext,
        evo_state: Any,
        last_content: str,
        parse_proposal: Any,
    ) -> None:
        """Handle the 'propose' evolution phase.

        If a formal proposal is found, advance to executing. If Eva
        already did significant tool work (combined propose+execute),
        complete the loop immediately.  If no proposal but work was
        detected, auto-complete with a best-effort title.
        """
        # Try structured output parsing first (more reliable than regex)
        proposal = None
        try:
            from anima.llm.structured import EvolutionProposal
            import json as _json
            # Try to extract JSON from the content
            for prefix in ["{", "```json\n{"]:
                idx = last_content.find(prefix)
                if idx >= 0:
                    end = last_content.find("}", idx)
                    if end > idx:
                        raw = last_content[idx:end+1]
                        if prefix.startswith("```"):
                            raw = raw.removeprefix("```json\n")
                        data = _json.loads(raw)
                        ep = EvolutionProposal(**data)
                        if ep.title:
                            proposal = {
                                "title": ep.title, "type": ep.type,
                                "problem": ep.problem, "solution": ep.solution,
                                "files": ep.files, "risk": ep.risk,
                            }
                            log.info("Parsed evolution proposal via structured output: %s", ep.title)
                            break
        except Exception as e:
            log.debug("Structured proposal parsing failed: %s", e)

        # Fallback to old free-text parsing
        if not proposal:
            proposal = parse_proposal(last_content)

        if proposal.get("title"):
            # Formal proposal parsed successfully
            tool_calls_made = sum(
                1
                for m in ctx.conversation
                if (
                    m.get("role") == "assistant"
                    and "tool_use" in str(m.get("content", ""))
                )
            )
            if tool_calls_made > 3:
                # Eva did significant work — treat as combined propose+execute
                evo_state.advance_phase("executing", **proposal)
                evo_state.complete_loop(
                    result=last_content[:500],
                    title=proposal.get("title", ""),
                    evo_type=proposal.get("type", ""),
                )
                log.info(
                    "Evolution combined propose+execute: %s",
                    proposal.get("title"),
                )
                self._maybe_trigger_reload(ctx, proposal.get("title", ""))
            else:
                evo_state.advance_phase("executing", **proposal)
                log.info(
                    "Evolution proposal accepted: %s", proposal.get("title"),
                )
        else:
            # No formal proposal — check if Eva did the work anyway
            self._handle_informal_evolution(ctx, evo_state, last_content)

    def _handle_informal_evolution(
        self,
        ctx: CognitiveContext,
        evo_state: Any,
        last_content: str,
    ) -> None:
        """Handle evolution when no formal proposal was found.

        Scans conversation for tool-use evidence (edit_file, git commit,
        shell).  If work was detected, extract a title and auto-complete
        the loop.  Otherwise fail the loop.
        """
        did_work = any(
            "edit_file" in str(m.get("content", ""))
            or "git commit" in str(m.get("content", ""))
            or "shell" in str(m.get("content", ""))
            for m in ctx.conversation
            if m.get("role") == "assistant"
        )

        if did_work:
            title = self._extract_title_from_content(last_content)
            evo_state.advance_phase("executing", type="fix", title=title)
            evo_state.complete_loop(
                result=last_content[:500],
                title=title,
                evo_type="fix",
            )
            log.info("Evolution auto-completed (work detected): %s", title)
            self._maybe_trigger_reload(ctx, title)
        else:
            evo_state.fail_loop("No valid proposal generated")

    def _handle_execute_phase(
        self,
        ctx: CognitiveContext,
        evo_state: Any,
        last_content: str,
    ) -> None:
        """Handle the 'execute' evolution phase — complete the loop."""
        title = evo_state.current_loop.get("title", "")
        evo_state.complete_loop(
            result=last_content[:500],
            title=title,
            evo_type=evo_state.current_loop.get("type", ""),
        )
        ctx.emotion.adjust(confidence=0.1, curiosity=0.1)
        self._maybe_trigger_reload(ctx, title)

    # ------------------------------------------------------------------ #
    #  Evolution hot-reload                                                #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _maybe_trigger_reload(
        ctx: CognitiveContext, evolution_title: str,
    ) -> None:
        """Hot-reload after evolution.

        Strategy:
        - Tools/prompts/config → importlib.reload + re-register (instant)
        - Core modules (cognitive/heartbeat/main) → full restart via checkpoint
        """
        from anima.config import project_root

        try:
            result = subprocess.run(
                ["git", "diff", "HEAD~1", "--name-only", "--diff-filter=AM"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(project_root()),
            )
            changed_files = (
                result.stdout.strip().splitlines()
                if result.returncode == 0
                else []
            )
            py_changed = [f for f in changed_files if f.endswith(".py")]

            if not py_changed:
                log.info(
                    "Evolution completed (no .py changes) — no reload needed",
                )
                return

            # Check if any core module was modified
            core_changed = [f for f in py_changed if f in _CORE_MODULES]

            if core_changed:
                # Core module changed → full restart required
                log.info(
                    "Core module changed (%s) — full restart required",
                    core_changed,
                )
                tick_count = (
                    ctx.heartbeat._tick_count if ctx.heartbeat else 0
                )
                if ctx.reload_manager:
                    ctx.reload_manager.request_reload(
                        reason=f"Evolution: {evolution_title}",
                        conversation=ctx.conversation,
                        emotion_state=ctx.emotion.to_dict(),
                        tick_count=tick_count,
                        evolution_title=evolution_title,
                    )
                else:
                    log.warning(
                        "Core module changed but no reload_manager — "
                        "restart must be done manually",
                    )
            else:
                # Non-core changes → in-process hot-reload
                count = ctx.tool_registry.reload_tools()
                log.info(
                    "Hot-reloaded %d tools in-process (no restart): %s",
                    count,
                    [f.split("/")[-1] for f in py_changed],
                )
        except Exception as e:
            log.error("Reload check failed: %s", e)

    # ------------------------------------------------------------------ #
    #  Utility                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_title_from_content(content: str) -> str:
        """Best-effort extraction of an evolution title from free-form text.

        Tries two strategies:
        1. Look for an explicit ``TITLE: ...`` line.
        2. Fall back to the first line that looks like a heading
           (10-100 chars, stripped of markdown/bullet prefixes).
        """
        # Strategy 1: explicit TITLE: line
        for line in content.splitlines():
            line = line.strip().strip("*#- ")
            if "TITLE" in line.upper() and ":" in line:
                return line.split(":", 1)[1].strip()

        # Strategy 2: first reasonable-length line
        for line in content.splitlines():
            line = line.strip().strip("*#- ")
            if 10 < len(line) < 100:
                return line

        return "Autonomous improvement"
