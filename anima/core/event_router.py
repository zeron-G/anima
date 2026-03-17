"""Event routing — determines how each event type is processed.

Extracted from cognitive.py to separate concerns:
- event_router.py: WHAT to do with each event type (routing, tier selection, message formatting)
- cognitive.py: HOW to execute (LLM agentic loop, tool calls)
"""

from __future__ import annotations

import json
import random
from typing import Any

from anima.models.event import Event, EventType
from anima.utils.logging import get_logger

log = get_logger("event_router")

# Self-thinking task pool — each task has a keyword for dedup
TASK_POOL = [
    ("log_errors",   "Scan your logs for errors: read_file on data/logs/anima.log (offset=-80, limit=80). Find any ERROR or repeated failures. If you spot something fixable, fix it."),
    ("projects",     "Read data/projects.md and check your active projects. Are any todos overdue?"),
    ("evolution",    "Think about your own evolution. What feature or fix would make you most useful to 主人?"),
    ("disk",         "Check disk usage: run system_info to get current disk %. Report if anything needs attention."),
    ("feelings",     "Read your feelings file (agents/eva/feelings.md) and reflect honestly. Write a brief update if your mood has shifted."),
    ("memory",       "Review your recent saved notes: glob_search('data/notes/*.md'). Pick one important and follow up."),
    ("tools_audit",  "Check the log for 'Tool.*failed' patterns. Identify the most common failure."),
    ("network",      "Check network sync status: read the last 20 lines of the log for 'network.sync' entries."),
]

TASK_COOLDOWN_TICKS = 4  # ~40 min between repeating same task


def event_to_message(event: Event, self_thinking_ticks: dict[str, int]) -> str:
    """Convert an event into a message string for the LLM.

    Args:
        event: The event to convert.
        self_thinking_ticks: Dict tracking last tick# per task keyword (mutated in-place).

    Returns:
        Formatted message string.
    """
    t = event.type
    p = event.payload

    if t == EventType.USER_MESSAGE:
        return p.get("text", "")

    if t == EventType.STARTUP:
        if p and p.get("is_restart"):
            return (
                "[INTERNAL: EVOLUTION RESTART]\n"
                f"You just restarted after evolution: {p.get('reason', 'code update')}.\n"
                "Briefly confirm you're back online."
            )
        return "[INTERNAL: STARTUP]\nYou just booted. Check time and system status, then greet briefly."

    if t == EventType.SELF_THINKING and p.get("evolution"):
        return p.get("evolution_prompt", "[EVOLUTION: no prompt provided]")

    if t == EventType.SELF_THINKING:
        tick = p.get("tick_count", 0)
        return _build_self_thinking_message(tick, self_thinking_ticks)

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
        return (
            f"[DELEGATED TASK from {p.get('from_node', 'unknown')}]\n"
            f"{p.get('task', '')}\n"
            "Complete this task using your tools and respond with the result."
        )

    if t == EventType.IDLE_TASK:
        handler = p.get("handler", "unknown")
        return (
            f"[IDLE TASK: {p.get('task_name', 'unknown')}] (idle_score={p.get('idle_score', '?')}, level={p.get('idle_level', '?')})\n"
            f"{p.get('description', '')}\n"
            f"Handler: {handler}\n"
            f"Max duration: {p.get('max_duration_s', 300)}s\n"
            "Execute this background task efficiently — you're running in idle time. "
            "Use your tools to actually DO the task. Be concise and effective."
        )

    return f"[INTERNAL: {t.name}] {json.dumps(p, ensure_ascii=False)[:200]}"


def pick_tier(event: Event) -> int:
    """Determine which LLM tier to use for this event.

    Tier 1 = user messages (highest quality).
    Tier 2 = everything else (cheaper).
    """
    if event.type == EventType.USER_MESSAGE:
        return 1
    return 2


def is_self_event(event: Event) -> bool:
    """Check if this event was internally generated (not from user)."""
    return event.type not in (EventType.USER_MESSAGE, EventType.TASK_DELEGATE)


def _build_self_thinking_message(tick: int, last_ticks: dict[str, int]) -> str:
    """Build a self-thinking task message with dedup."""
    available = [
        (kw, task) for kw, task in TASK_POOL
        if (tick - last_ticks.get(kw, -9999)) >= TASK_COOLDOWN_TICKS
    ]
    if not available:
        available = sorted(TASK_POOL, key=lambda x: last_ticks.get(x[0], -9999))[:3]

    chosen_kw, chosen_task = random.choice(available)
    last_ticks[chosen_kw] = tick

    return (
        f"[INTERNAL: SELF_THINKING tick #{tick}]\n"
        f"PROACTIVE TASK ({chosen_kw}): {chosen_task}\n"
        "Use your tools to actually DO this task. Be concise."
    )
