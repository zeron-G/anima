"""Agentic loop cognitive engine — LLM-native decision making with persistent life.

The core loop mirrors Claude Code's architecture:
  LLM thinks -> calls tools -> sees results -> thinks again -> repeats until done

What makes it BETTER than Claude Code:
  - Runs continuously (not just during conversations)
  - Has persistent memory across sessions
  - Has emotional state that evolves
  - Self-generates work via heartbeat events
  - Perceives environment changes autonomously
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable

from anima.config import get
from anima.core.event_queue import EventQueue
from anima.emotion.state import EmotionState
from anima.llm.prompts import PromptBuilder
from anima.llm.router import LLMRouter
from anima.memory.store import MemoryStore
from anima.models.event import Event, EventType
from anima.perception.snapshot_cache import SnapshotCache
from anima.tools.executor import ToolExecutor
from anima.tools.registry import ToolRegistry
from anima.utils.logging import get_logger

log = get_logger("cognitive")


class AgenticLoop:
    """The brain. An LLM-native agentic loop with persistent life.

    Core loop (same as Claude Code):
      LLM thinks -> calls tools -> sees results -> thinks again -> done

    What makes it BETTER than Claude Code:
      - Runs continuously (not just during conversations)
      - Has persistent memory across sessions
      - Has emotional state that evolves
      - Self-generates work via heartbeat events
      - Perceives environment changes autonomously

    No significance routing. No rule engine. No PODAR stages.
    The LLM IS the decision engine.
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
        self._config = config

        self._conversation: list[dict] = []  # Running conversation buffer
        self._max_conversation_turns = 50  # Sliding window (in messages)
        self._output_callback: Callable[[str], Any] | None = None
        self._status_callback: Callable[[dict], Any] | None = None
        self._pending_agents: list = []  # Background agent tasks

    def set_output_callback(self, callback: Callable[[str], Any]) -> None:
        """Set callback for outputting messages to user."""
        self._output_callback = callback

    def set_status_callback(self, callback: Callable[[dict], Any]) -> None:
        """Set callback for emitting activity/status updates to the UI."""
        self._status_callback = callback

    # ------------------------------------------------------------------ #
    #  Main loop                                                          #
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        """Main loop — process events and check agent status."""
        log.info("Agentic loop started")
        while True:
            # Non-blocking get with timeout — allows checking agent status
            event = await self._event_queue.get_timeout(timeout=2.0)
            if event is None:
                continue  # timeout, check again
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
        """Handle any event through the agentic loop."""
        await self._handle_event(event)

    # ------------------------------------------------------------------ #
    #  Agentic loop                                                       #
    # ------------------------------------------------------------------ #

    async def _handle_event(self, event: Event) -> None:
        """Handle any event through the agentic loop.

        Every event goes to the LLM. The LLM decides what matters.
        No significance routing, no rule engine.
        """
        is_self = event.type in (
            EventType.STARTUP, EventType.SELF_THINKING, EventType.FOLLOW_UP,
            EventType.FILE_CHANGE, EventType.SYSTEM_ALERT,
        )

        log.info(
            "Processing event: %s%s",
            event.type.name,
            " (self-generated)" if is_self else "",
        )

        # Build the user message for this event
        user_message = self._event_to_message(event)

        # Save user message to episodic memory (only for actual user messages)
        if event.type == EventType.USER_MESSAGE and user_message:
            self._memory_store.save_memory(
                content=user_message,
                type="chat",
                importance=0.6,
                metadata={"role": "user"},
            )

        # Get current environment context
        snapshot = self._snapshot_cache.get_latest()
        system_state = snapshot.get("system_state", {}) if snapshot else {}

        # Build system prompt — lean, tailored to event type
        # Only inject tools/memory/emotion when the event type needs it
        event_type_name = event.type.name
        needs_tools = event_type_name in ("USER_MESSAGE", "STARTUP", "SELF_THINKING")

        system_prompt = self._prompt_builder.build_for_event(
            event_type_name,
            tools_description=self._build_tools_description() if needs_tools else "",
            system_state=system_state,
            emotion_state=self._emotion.to_dict() if not is_self else None,
            working_memory_summary=self._get_memory_summary() if not is_self else "",
        )

        # Build messages: system + conversation history + new message
        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        # Only include conversation history for user messages (not self-events)
        if event_type_name == "USER_MESSAGE":
            messages.extend(self._conversation[-self._max_conversation_turns:])
        messages.append({"role": "user", "content": user_message})

        # Tool schemas — only attach when needed
        tools = self._get_tool_schemas() if needs_tools else []

        # THE AGENTIC LOOP -- keep going until LLM is done or timeout
        timeout = 60 if is_self else 180  # self-events get shorter timeout
        start_time = time.time()

        self._emit_status({
            "stage": "thinking",
            "detail": f"processing {event.type.name}",
        })

        while time.time() - start_time < timeout:
            tier = self._pick_tier(event)
            resp = await self._llm_router.call_with_tools(
                messages=messages, tools=tools, tier=tier,
            )

            if resp is None:
                self._emit_status({"stage": "error", "detail": "LLM call failed"})
                log.warning("LLM call failed for event %s", event.type.name)
                break

            content = resp.get("content", "")
            tool_calls = resp.get("tool_calls", [])

            if not tool_calls:
                # LLM is done -- it has something to say (or nothing)
                if content and content.strip():
                    if is_self:
                        # Self-thought: log it, show in activity feed
                        self._emit_status({
                            "stage": "self_thought",
                            "detail": content[:200],
                        })
                        log.info("Self-thought: %s", content[:100])
                        self._memory_store.save_memory(
                            content=f"[self-thought] {content[:300]}",
                            type="observation",
                            importance=0.4,
                        )
                    else:
                        # User-facing response
                        self._emit_status({
                            "stage": "responding",
                            "detail": content[:80],
                        })
                        await self._output(content)
                        self._save_chat("assistant", content)

                # Add to conversation buffer
                self._conversation.append({"role": "user", "content": user_message})
                self._conversation.append({
                    "role": "assistant",
                    "content": content or "(no response)",
                })
                self._trim_conversation()
                break

            # LLM wants tools -- execute them
            assistant_blocks = self._build_assistant_blocks(content, tool_calls, resp)
            messages.append({"role": "assistant", "content": assistant_blocks})

            # Execute all tools in parallel
            async def _exec_one(tc: dict) -> dict:
                name = tc["name"]
                try:
                    args = (
                        json.loads(tc["arguments"])
                        if isinstance(tc["arguments"], str)
                        else tc["arguments"]
                    )
                except (json.JSONDecodeError, TypeError):
                    args = {}
                self._emit_status({
                    "stage": "executing",
                    "detail": f"{name}(...)",
                    "tool": name,
                })
                result = await self._tool_executor.execute(name, args)
                result_text = self._format_result(name, result)
                log.info(
                    "Tool %s %s",
                    name,
                    "succeeded" if result.get("success") else "failed",
                )
                self._emit_status({
                    "stage": "tool_done",
                    "detail": f"{name}: {'ok' if result.get('success') else 'failed'}",
                    "tool": name,
                })
                return {
                    "type": "tool_result",
                    "tool_use_id": tc.get("id", name),
                    "content": result_text,
                }

            tool_results = list(await asyncio.gather(*[_exec_one(tc) for tc in tool_calls]))

            messages.append({"role": "user", "content": tool_results})

        # Adjust emotion
        if not is_self:
            self._emotion.adjust(engagement=0.1)

        # Audit
        self._memory_store.audit(
            action=f"event:{event.type.name}",
            details=user_message[:200],
        )

    # ------------------------------------------------------------------ #
    #  Event → message conversion                                         #
    # ------------------------------------------------------------------ #

    def _event_to_message(self, event: Event) -> str:
        """Convert any event type to a user message for the LLM.

        CRITICAL: Self-events are clearly marked as INTERNAL so the LLM
        does not confuse them with user instructions.
        """
        t = event.type
        p = event.payload

        if t == EventType.USER_MESSAGE:
            return p.get("text", "")

        if t == EventType.STARTUP:
            return (
                "[INTERNAL: STARTUP — this is NOT a user message]\n"
                "You just booted up. Quickly check the time and system status "
                "using tools, then greet your user briefly in character. "
                "Do NOT narrate your boot process in detail."
            )

        if t == EventType.SELF_THINKING:
            return (
                "[INTERNAL: SELF_THINKING — this is NOT a user message]\n"
                f"Periodic check (tick #{p.get('tick_count', '?')}). "
                "Briefly scan if anything needs attention. "
                "If nothing notable, just stay alert — do NOT output to user. "
                "Only speak to user if you find something genuinely important."
            )

        if t == EventType.FILE_CHANGE:
            changes = p.get("changes", [])
            # Filter out noise (notes dir, __pycache__, etc.)
            real_changes = [
                c for c in changes
                if not any(skip in c.get("path", "") for skip in
                          ("__pycache__", ".pyc", "data/notes/", "data/logs/", "anima.db"))
            ]
            if not real_changes:
                return (
                    "[INTERNAL: FILE_CHANGE — noise filtered out, no action needed]\n"
                    "Only internal files changed (cache/logs/notes). No action required."
                )
            desc = "\n".join(f"  - {c['path']} ({c['change']})" for c in real_changes[:5])
            return (
                "[INTERNAL: FILE_CHANGE — this is NOT a user message]\n"
                f"Your heartbeat detected file changes:\n{desc}\n"
                "Silently note this. Only tell the user if it seems important."
            )

        if t == EventType.SYSTEM_ALERT:
            return (
                "[INTERNAL: SYSTEM_ALERT — this is NOT a user message]\n"
                f"System alert detected: {json.dumps(p.get('diff', {}), ensure_ascii=False)[:200]}\n"
                "Assess severity. Alert the user only if action is needed."
            )

        if t == EventType.FOLLOW_UP:
            return p.get("text", "Continue your previous work.")

        return f"[INTERNAL: {t.name}] {json.dumps(p, ensure_ascii=False)[:200]}"

    # ------------------------------------------------------------------ #
    #  LLM tier selection                                                  #
    # ------------------------------------------------------------------ #

    def _pick_tier(self, event: Event) -> int:
        """Pick LLM tier based on event type."""
        if event.type == EventType.USER_MESSAGE:
            return 1  # Best model for user
        if event.type == EventType.STARTUP:
            return 2  # Good model for startup
        return 2  # Default to tier2 for self-events (cost efficient)

    # ------------------------------------------------------------------ #
    #  Memory helpers                                                      #
    # ------------------------------------------------------------------ #

    def _save_chat(self, role: str, content: str) -> None:
        """Save a chat message to episodic memory."""
        self._memory_store.save_memory(
            content=content,
            type="chat",
            importance=0.6,
            metadata={"role": role},
        )

    def _get_memory_summary(self) -> str:
        """Get recent observations and decisions from memory."""
        recent = self._memory_store.get_recent_memories(limit=15)
        if not recent:
            return "(no recent memories)"
        lines = []
        for m in recent:
            lines.append(f"- [{m['type']}] {m['content'][:100]}")
        return "\n".join(lines)

    def _trim_conversation(self) -> None:
        """Keep conversation buffer within limits."""
        max_msgs = self._max_conversation_turns * 2
        if len(self._conversation) > max_msgs:
            self._conversation = self._conversation[-max_msgs:]

    # ------------------------------------------------------------------ #
    #  Tool helpers                                                        #
    # ------------------------------------------------------------------ #

    def _build_tools_description(self) -> str:
        """Build human-readable tool descriptions for the system prompt."""
        lines = []
        for spec in self._tool_registry.list_tools():
            params = spec.parameters.get("properties", {})
            required = spec.parameters.get("required", [])
            param_parts = []
            for pname, pinfo in params.items():
                ptype = pinfo.get("type", "any")
                pdesc = pinfo.get("description", "")
                req = " (required)" if pname in required else ""
                param_parts.append(f"    - `{pname}` ({ptype}{req}): {pdesc}")
            params_str = "\n".join(param_parts) if param_parts else "    (no parameters)"
            lines.append(f"**{spec.name}** -- {spec.description}\n{params_str}")
        return "\n\n".join(lines) if lines else "(no tools registered)"

    def _get_tool_schemas(self) -> list[dict]:
        """Convert tool registry to Anthropic-native tool schemas.

        Anthropic format:
          {"name": ..., "description": ..., "input_schema": {...}}
        """
        schemas = []
        for spec in self._tool_registry.list_tools():
            schemas.append({
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.parameters or {"type": "object", "properties": {}},
            })
        return schemas

    def _build_assistant_blocks(
        self, text: str, tool_calls: list[dict], raw_resp: dict,
    ) -> list[dict]:
        """Build Anthropic-format assistant content blocks from response.

        This reconstructs the content array the way Anthropic expects it
        when feeding tool results back in a multi-turn conversation.
        """
        blocks: list[dict] = []
        if text:
            blocks.append({"type": "text", "text": text})
        for tc in tool_calls:
            try:
                input_data = (
                    json.loads(tc["arguments"])
                    if isinstance(tc["arguments"], str)
                    else tc["arguments"]
                )
            except (json.JSONDecodeError, TypeError):
                input_data = {}
            tool_id = tc.get("id", tc["name"])
            blocks.append({
                "type": "tool_use",
                "id": tool_id,
                "name": tc["name"],
                "input": input_data,
            })
        return blocks

    def _format_result(self, tool_name: str, result: dict) -> str:
        """Format a tool execution result into readable text for the LLM."""
        if not result.get("success"):
            return f"Error: {result.get('error', 'unknown error')}"

        raw = result.get("result")

        # Shell commands: combine stdout/stderr
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
            # Dict result without stdout/stderr (e.g. system_info)
            return json.dumps(raw, ensure_ascii=False, indent=2)

        if isinstance(raw, str):
            return raw

        if raw is None:
            return "(no output)"

        return str(raw)

    # ------------------------------------------------------------------ #
    #  Output helpers                                                      #
    # ------------------------------------------------------------------ #

    async def _output(self, text: str) -> None:
        """Output text to user via callback."""
        if self._output_callback:
            self._output_callback(text)
        else:
            log.info("ANIMA says: %s", text)

    def _emit_status(self, status: dict) -> None:
        """Emit a status update to the UI via callback."""
        if self._status_callback:
            try:
                self._status_callback(status)
            except Exception as e:
                log.debug("Status callback error: %s", e)


# Backward-compatible alias
CognitiveCycle = AgenticLoop
