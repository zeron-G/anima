"""Event routing — converts raw events into LLM-ready messages.

Extracted from the old monolithic AgenticLoop (H-24 refactor).
Responsibilities:
  - Event classification (is_self, is_delegation)
  - Rule engine fast path (zero LLM cost for simple events)
  - Event → message conversion
  - Tier selection
  - SELF_THINKING task scheduling with dedup
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from anima.core.rule_engine import RuleEngine
from anima.models.decision import ActionType
from anima.models.event import Event, EventType
from anima.utils.logging import get_logger

if TYPE_CHECKING:
    from anima.core.context import CognitiveContext

log = get_logger("event_routing")


# ------------------------------------------------------------------ #
#  Data structures                                                     #
# ------------------------------------------------------------------ #

@dataclass
class RoutingDecision:
    """Result of routing an event.

    If ``handled`` is True, the rule engine already processed the event
    and no LLM call is needed.  The caller should execute the rule
    engine's ``decision`` directly.

    If ``handled`` is False, the caller should send ``message`` to the
    LLM on the chosen ``tier``.
    """
    message: str = ""
    tier: int = 2
    is_self: bool = False
    is_delegation: bool = False
    handled: bool = False          # True → rule engine took care of it
    source: str = ""               # event source for response routing
    needs_tools: bool = False
    is_evolution: bool = False
    # Rule engine decision (only set when handled=True)
    rule_decision: Any = None      # Decision from RuleEngine


# ── Three-axis thinking model ──
# Context-aware autonomous thinking (replaces the old 16-task random pool).
# Prompt text lives in axis_prompts.py so all autonomous-cognition prompts are
# centralized and auditable (S4); _pick_self_thinking_prompt() selects among them.
from anima.core.axis_prompts import (
    HUMAN_AXIS_PROMPT,
    SELF_AXIS_PROMPT,
    SELF_AXIS_PERSONALITY_REFLECT,
    SELF_AXIS_CURATE_EXAMPLES,
    WORLD_AXIS_PROMPT,
    WORLD_AXIS_LATE_NIGHT,
)

# Event types that are considered "self" (internal) events.
# Responses go to memory/activity feed, not the user terminal.
_SELF_EVENT_TYPES: frozenset[EventType] = frozenset({
    EventType.STARTUP,
    EventType.SELF_THINKING,
    EventType.FOLLOW_UP,
    EventType.FILE_CHANGE,
    EventType.SYSTEM_ALERT,
    EventType.EMBODIED_PERCEPTION,
    EventType.SCHEDULED_TASK,
    EventType.IDLE_TASK,
})

# Event types that get tool access in the LLM call. EMBODIED_PERCEPTION is included so
# Eva can RESPOND to what her body senses (move, look, express, explore) — not just
# note it. Robot commands still pass through the E1 safety clamp.
_TOOL_ENABLED_EVENTS: frozenset[str] = frozenset({
    "USER_MESSAGE", "STARTUP", "SELF_THINKING",
    "SCHEDULED_TASK", "TASK_DELEGATE", "IDLE_TASK", "EMBODIED_PERCEPTION",
})


# ------------------------------------------------------------------ #
#  EventRouter                                                         #
# ------------------------------------------------------------------ #

class EventRouter:
    """Routes events to the appropriate processing path.

    Handles:
    - Event classification (self-event, delegation, user message)
    - Rule engine fast path for cheap events
    - Event → message conversion for LLM path
    - Tier selection (tier1=Opus for users, tier2=Sonnet for internal)
    - SELF_THINKING task scheduling with cooldown dedup
    """

    def __init__(self) -> None:
        self._rule_engine = RuleEngine()

        # SELF_THINKING dedup state --
        # Maps task keyword → last tick# it was chosen.
        self._self_thinking_last_tick: dict[str, int] = {}

        # Track the last proactive task result so the next tick's
        # SELF_THINKING message can include context about what was
        # already done, preventing the "system normal" repetition loop.
        self._last_proactive_result: str = ""
        self._last_chosen_kw: str = ""

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def route(self, event: Event, ctx: CognitiveContext) -> RoutingDecision:
        """Route an event to the correct processing path.

        Args:
            event: Incoming event from the event queue.
            ctx: CognitiveContext with snapshot_cache and other deps.

        Returns:
            RoutingDecision describing how to process this event.
        """
        # ── Step 0: Classify ──
        is_self = event.type in _SELF_EVENT_TYPES
        is_delegation = event.type == EventType.TASK_DELEGATE
        source = (
            event.payload.get("source", event.source)
            if event.payload is not None
            else event.source
        )
        is_evolution = bool(event.payload and event.payload.get("evolution"))

        event_type_name = event.type.name
        needs_tools = event_type_name in _TOOL_ENABLED_EVENTS

        log.info(
            "Routing event: %s%s%s",
            event_type_name,
            " (internal)" if is_self else "",
            " (delegation)" if is_delegation else "",
        )

        # ── Step 1: Rule engine fast path ──
        # FILE_CHANGE, SYSTEM_ALERT, simple USER_MESSAGE greetings
        # are handled without any LLM call.
        if event.type in (
            EventType.FILE_CHANGE,
            EventType.SYSTEM_ALERT,
            EventType.USER_MESSAGE,
        ):
            rule_decision = self._try_rule_engine(event, ctx)
            if rule_decision is not None:
                return RoutingDecision(
                    handled=True,
                    is_self=is_self,
                    is_delegation=is_delegation,
                    source=source,
                    needs_tools=False,
                    is_evolution=False,
                    rule_decision=rule_decision,
                )

        # ── Step 2: Convert event → message for LLM path ──
        message = self._event_to_message(event, ctx=ctx)
        tier = self._pick_tier(event)

        return RoutingDecision(
            message=message,
            tier=tier,
            is_self=is_self,
            is_delegation=is_delegation,
            handled=False,
            source=source,
            needs_tools=needs_tools,
            is_evolution=is_evolution,
        )

    @property
    def last_chosen_keyword(self) -> str:
        """The keyword of the most recently chosen SELF_THINKING task."""
        return self._last_chosen_kw

    def update_proactive_result(self, keyword: str, result_text: str) -> None:
        """Update dedup context after processing a self-thought.

        Called by the cognitive loop after a SELF_THINKING event
        completes.  The result is injected into the next tick's
        SELF_THINKING message so the LLM knows what was just done
        and doesn't repeat itself.

        Args:
            keyword: Task keyword that was just completed (e.g. "log_errors").
            result_text: Brief summary of what the task produced.
        """
        summary = result_text[:200].replace("\n", " ")
        if keyword:
            self._last_proactive_result = f"[上次任务 {keyword}]: {summary}"
        else:
            self._last_proactive_result = f"[上次]: {summary}"
        self._last_chosen_kw = keyword

    # ------------------------------------------------------------------ #
    #  Rule engine fast path                                               #
    # ------------------------------------------------------------------ #

    def _try_rule_engine(self, event: Event, ctx: CognitiveContext) -> Any | None:
        """Try to handle the event with the rule engine.

        Returns the rule engine Decision if it handled the event,
        or None if the event needs LLM processing.
        """
        snapshot = ctx.snapshot_cache.get_latest()
        system_state = snapshot.get("system_state", {}) if snapshot else {}
        context = {
            "event_type": event.type.name,
            "event_payload": event.payload,
            "system_state": system_state,
        }
        decision = self._rule_engine.evaluate(context)

        if decision.action != ActionType.NOOP:
            ctx.emit_status({
                "stage": "rule_engine",
                "detail": f"{decision.action.value}: {decision.reasoning}",
            })
            log.info(
                "Rule engine handled %s: %s",
                event.type.name,
                decision.action.value,
            )
            return decision

        return None

    # ------------------------------------------------------------------ #
    #  Event classification helpers                                        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def classify_self(event: Event) -> bool:
        """Check if this event is internally generated (not from user)."""
        return event.type in _SELF_EVENT_TYPES

    @staticmethod
    def classify_delegation(event: Event) -> bool:
        """Check if this event is a delegation from another node."""
        return event.type == EventType.TASK_DELEGATE

    # ------------------------------------------------------------------ #
    #  Tier selection                                                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _pick_tier(event: Event) -> int:
        """Select LLM tier based on event type.

        Tier 1 (Opus / highest quality) — user messages deserve the
        best model.  Tier 2 (Sonnet / cheaper) — internal thoughts,
        system events, delegations.
        """
        if event.type == EventType.USER_MESSAGE:
            return 1
        return 2

    # ------------------------------------------------------------------ #
    #  Event → message conversion                                          #
    # ------------------------------------------------------------------ #

    def _event_to_message(self, event: Event, ctx: CognitiveContext | None = None) -> str:
        """Convert an event into a message string for the LLM.

        Each event type has its own formatting:
        - USER_MESSAGE: pass-through user text
        - STARTUP: boot/restart instructions
        - SELF_THINKING: proactive task with 3-axis thinking
        - FILE_CHANGE: filtered change list
        - SYSTEM_ALERT: system diff summary
        - FOLLOW_UP: continuation prompt
        - SCHEDULED_TASK: cron job prompt
        - TASK_DELEGATE: inter-node task
        - IDLE_TASK: background idle work
        """
        t = event.type
        p = event.payload or {}

        if t == EventType.USER_MESSAGE:
            return p.get("text", "")

        if t == EventType.STARTUP:
            return self._format_startup(p)

        if t == EventType.SELF_THINKING:
            return self._format_self_thinking(p, ctx=ctx)

        if t == EventType.FILE_CHANGE:
            return self._format_file_change(p)

        if t == EventType.SYSTEM_ALERT:
            return self._format_system_alert(p)

        if t == EventType.EMBODIED_PERCEPTION:
            return self._format_embodied_perception(p)

        if t == EventType.FOLLOW_UP:
            return p.get("text", "Continue your previous work.")

        if t == EventType.SCHEDULED_TASK:
            return self._format_scheduled_task(p)

        if t == EventType.TASK_DELEGATE:
            return self._format_task_delegate(p)

        if t == EventType.IDLE_TASK:
            return self._format_idle_task(p)

        # Fallback for unknown event types
        return f"[INTERNAL: {t.name}] {json.dumps(p, ensure_ascii=False)[:200]}"

    # ── Per-type formatters ──

    @staticmethod
    def _format_startup(p: dict) -> str:
        if p.get("is_restart"):
            return (
                "[INTERNAL: EVOLUTION RESTART]\n"
                f"You just restarted after evolution: "
                f"{p.get('reason', 'code update')}.\n"
                "Your conversation context has been preserved. "
                "Briefly confirm you're back online — no need for "
                "full startup scan."
            )
        return (
            "[INTERNAL: STARTUP]\n"
            "You just booted. Check time and system status, "
            "then greet briefly."
        )

    def _format_self_thinking(self, p: dict, ctx: CognitiveContext | None = None) -> str:
        """Format a SELF_THINKING event message.

        Handles three sub-types:
        1. Agent status check (running_agents in payload)
        2. Evolution cycle (evolution flag in payload)
        3. Regular proactive task (3-axis thinking model)
        """
        # Sub-type: running agents status check
        if p.get("running_agents"):
            return self._format_agent_status(p)

        # Sub-type: evolution cycle — pass through the evolution prompt
        if p.get("evolution"):
            return p.get("evolution_prompt", "[EVOLUTION: no prompt provided]")

        # Sub-type: regular proactive task
        tick = p.get("tick_count", 0)

        # ── Memory health check — every 20 ticks ──
        if tick > 0 and tick % 20 == 0:
            self._last_chosen_kw = "memory_health"
            return self._format_memory_health_check(tick)

        # ── Three-axis thinking selection ──
        if ctx is not None:
            return self._select_thinking_axis(tick, ctx)

        # Fallback if ctx not available (shouldn't happen in normal flow)
        self._last_chosen_kw = "world_axis"
        return WORLD_AXIS_PROMPT

    @staticmethod
    def _format_agent_status(p: dict) -> str:
        """Format a running-agents status check message."""
        agents_info = "\n".join(
            f"  - {a['type']} agent (id={a['id'][:12]}): "
            f"running {a['runtime_s']}s — task: {a['prompt']}"
            for a in p["running_agents"]
        )
        return (
            f"[INTERNAL: AGENT_STATUS tick #{p.get('tick_count', 0)}]\n"
            f"You have sub-agents that have been running for a while:\n"
            f"{agents_info}\n\n"
            "TASK: For each agent, use check_agent(session_id=...) to "
            "get its real-time status. Then send the user a brief "
            "friendly update, e.g. 'Still working on: [task summary] "
            "(running Xs, status: running/done/error)'. "
            "If an agent is done or errored, report its result."
        )

    @staticmethod
    def _format_memory_health_check(tick: int) -> str:
        """Format a periodic memory health check message.

        Runs every 20 ticks (~100 min) to ensure the memory system
        is healthy: feelings file, user profile, notes directory.
        """
        return (
            f"[INTERNAL: MEMORY_HEALTH_CHECK tick #{tick}]\n"
            "Lightweight self-check of your memory system. Do ALL of these:\n"
            "1. read_file('agents/eva/memory/feelings.md', offset=-10, "
            "limit=10) — check the last timestamp. If >48h since last "
            "entry, record a concern.\n"
            "2. read_file('data/user_profile.md') — confirm the file "
            "exists and is non-empty.\n"
            "3. glob_search('data/notes/*.md') — count files. If >50, "
            "flag abnormal growth.\n"
            "4. Write a ONE-LINE emoji summary to feelings via "
            "update_feelings, e.g. '🩺 记忆自检: feelings ✅ | "
            "profile ✅ | notes(4) ✅' or flag ⚠️ for issues.\n"
            "5. If everything is normal, do NOT notify 主人 — this is "
            "self-care only.\n"
            "   If you find a real problem (file missing/corrupt, "
            "feelings stale >48h), THEN gently tell 主人."
        )

    def _select_thinking_axis(self, tick: int, ctx: CognitiveContext) -> str:
        """Select thinking axis based on context.

        - User active recently -> Human Axis (understand them)
        - Periodic deep reflection -> Self Axis (every 50 ticks)
        - Curate examples -> Self Axis sub-task (every 100 ticks)
        - Default -> World Axis (environment observation)
        - Late night -> World Axis late night variant
        """
        # Deep personality reflection every ~50 ticks (~4 hours)
        if tick > 0 and tick % 50 == 0:
            self._last_chosen_kw = "personality_reflect"
            return SELF_AXIS_PERSONALITY_REFLECT

        # Curate golden examples every ~100 ticks (~8 hours)
        if tick > 0 and tick % 100 == 0:
            self._last_chosen_kw = "curate_examples"
            return SELF_AXIS_CURATE_EXAMPLES

        # Check user activity — if user was active recently, focus on understanding them
        user_active = False
        if ctx.user_activity:
            try:
                user_active = ctx.user_activity.is_recently_active(minutes=10)
            except Exception:
                pass

        if user_active:
            # Alternate between Human and Self axis when user is active
            if tick % 3 == 0:
                self._last_chosen_kw = "self_axis"
                return SELF_AXIS_PROMPT
            else:
                self._last_chosen_kw = "human_axis"
                return HUMAN_AXIS_PROMPT

        # Default: World axis (environment observation)
        # With late night variant
        import time as _time
        hour = int(_time.strftime("%H"))
        if 23 <= hour or hour < 5:
            self._last_chosen_kw = "late_night"
            return WORLD_AXIS_LATE_NIGHT

        self._last_chosen_kw = "world_axis"
        return WORLD_AXIS_PROMPT

    @staticmethod
    def _format_file_change(p: dict) -> str:
        """Format a FILE_CHANGE event, filtering out noise."""
        changes = p.get("changes", [])
        # Filter out build artifacts, notes, logs, and DB changes
        real = [
            c for c in changes
            if not any(
                skip in c.get("path", "")
                for skip in (
                    "__pycache__", ".pyc", "data/notes/",
                    "data/logs/", "anima.db",
                )
            )
        ]
        if not real:
            return "[INTERNAL: FILE_CHANGE — noise, no action needed]"
        desc = "\n".join(
            f"  - {c['path']} ({c['change']})" for c in real[:5]
        )
        return f"[INTERNAL: FILE_CHANGE]\n{desc}"

    @staticmethod
    def _format_system_alert(p: dict) -> str:
        """Format a SYSTEM_ALERT event with the diff summary."""
        return (
            f"[INTERNAL: SYSTEM_ALERT]\n"
            f"{json.dumps(p.get('diff', {}), ensure_ascii=False)[:200]}"
        )

    @staticmethod
    def _format_embodied_perception(p: dict) -> str:
        """Format an EMBODIED_PERCEPTION event — what Eva's robot body just sensed,
        plus its body state, framed so she can respond as herself (move/look/express/
        explore) or simply take note."""
        perc = p.get("perception", {}) or {}
        node = p.get("node_id", "robot")
        summary = p.get("summary", "Something changed in my body's senses.")
        bits: list[str] = []
        if "distance_cm" in perc:
            bits.append(f"obstacle distance ~{float(perc.get('distance_cm', 999)):.0f}cm")
        if str(perc.get("touch", "N")) != "N":
            bits.append(f"touch={perc.get('touch')}")
        bits.append(f"tilt pitch={float(perc.get('pitch_deg', 0)):.0f}deg roll={float(perc.get('roll_deg', 0)):.0f}deg")
        if perc.get("is_lifted"):
            bits.append("lifted off ground")
        if float(perc.get("battery_v", 0) or 0) > 0:
            bits.append(f"battery {float(perc.get('battery_v', 0)):.1f}V")
        return (
            f"[Embodied perception · {node}] {summary}\n"
            f"Body state: {', '.join(bits)}.\n"
            "You are Eva, present right now in this robot body. Respond as yourself — you may move, "
            "look around, express with light/tail/voice, explore your surroundings, or simply take note "
            "of it. Keep physical safety in mind (unsafe motion is clamped)."
        )

    @staticmethod
    def _format_scheduled_task(p: dict) -> str:
        """Format a SCHEDULED_TASK event with job name and prompt."""
        return (
            f"[SCHEDULED: {p.get('job_name', 'unnamed')}]\n"
            f"{p.get('prompt', '')}"
        )

    @staticmethod
    def _format_task_delegate(p: dict) -> str:
        """Format a TASK_DELEGATE event from another node."""
        from_node = p.get("from_node", "unknown")
        task_text = p.get("task", "")
        return (
            f"[DELEGATED TASK from {from_node}]\n"
            f"{task_text}\n"
            "Complete this task using your tools and respond "
            "with the result."
        )

    @staticmethod
    def _format_idle_task(p: dict) -> str:
        """Format an IDLE_TASK event dispatched by the idle scheduler."""
        handler = p.get("handler", "unknown")
        return (
            f"[IDLE TASK: {p.get('task_name', 'unknown')}] "
            f"(idle_score={p.get('idle_score', '?')}, "
            f"level={p.get('idle_level', '?')})\n"
            f"{p.get('description', '')}\n"
            f"Handler: {handler}\n"
            f"Max duration: {p.get('max_duration_s', 300)}s\n"
            "Execute this background task efficiently — you're running "
            "in idle time. Use your tools to actually DO the task. "
            "Be concise and effective."
        )
