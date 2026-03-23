"""message_user tool — Eva's voice to initiate conversation with the user.

During self-thinking, Eva normally just thinks silently. This tool lets
her *choose* to send a message to the user when she decides it's worth it.

The decision to speak is Eva's — not triggered by external conditions.
She might want to report something, share a thought, check in, or just chat.
"""

from __future__ import annotations

from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger

log = get_logger("tools.message_user")

# Set by main.py during wiring
_output_callback = None
_dashboard_hub = None


def set_output_callback(cb) -> None:
    global _output_callback
    _output_callback = cb


def set_dashboard_hub(hub) -> None:
    global _dashboard_hub
    _dashboard_hub = hub


async def _message_user(
    message: str = "",
    reason: str = "proactive",
) -> dict:
    """Send a proactive message to the user.

    Use this during self-thinking when you decide you want to tell
    the user something. The message will appear in their chat.
    """
    if not message or not message.strip():
        return {"error": "message cannot be empty"}

    text = message.strip()

    # Push to chat via output callback (terminal + dashboard)
    if _output_callback:
        _output_callback(text, source="")

    # Also push as typed proactive WebSocket event for ProactiveTag display
    if _dashboard_hub:
        try:
            _dashboard_hub.push_typed_event("proactive", {
                "text": text,
                "source": reason,
                "timestamp": __import__("time").time(),
            })
        except Exception as e:
            log.debug("Proactive WS push failed: %s", e)

    # Save to chat memory
    try:
        from anima.memory.store import get_memory_store
        store = get_memory_store()
        if store:
            await store.save_memory_async(
                content=text,
                type="chat",
                importance=0.6,
            )
    except Exception as e:
        log.debug("Memory save failed: %s", e)

    log.info("Eva proactively messaged user (%s): %s", reason, text[:80])
    return {"sent": True, "reason": reason}


def get_message_user_tool() -> ToolSpec:
    return ToolSpec(
        name="message_user",
        description=(
            "Send a message to the user during self-thinking. Use this when you "
            "DECIDE you want to tell the user something — report an issue, share "
            "a thought, check in, say good morning, etc. The message appears in "
            "their chat. Only use when you genuinely have something to say."
        ),
        parameters={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to send to the user",
                },
                "reason": {
                    "type": "string",
                    "enum": ["system_alert", "greeting", "curiosity",
                             "evolution_report", "check_in", "emotional", "general"],
                    "description": "Why you're reaching out (for display tag)",
                    "default": "general",
                },
            },
            "required": ["message"],
        },
        risk_level=RiskLevel.LOW,
        handler=_message_user,
    )
