"""Message format conversion utilities for LLM providers."""
from __future__ import annotations


def _fix_api_messages(messages: list[dict]) -> list[dict]:
    """Merge consecutive same-role messages for Anthropic API compliance."""
    if not messages:
        return messages
    fixed = [messages[0]]
    for msg in messages[1:]:
        if msg["role"] == fixed[-1]["role"]:
            # Merge content
            prev_content = fixed[-1].get("content", "")
            new_content = msg.get("content", "")
            if isinstance(prev_content, str) and isinstance(new_content, str):
                fixed[-1]["content"] = prev_content + "\n\n" + new_content
            else:
                fixed.append(msg)  # Can't merge non-string content
        else:
            fixed.append(msg)
    return fixed
