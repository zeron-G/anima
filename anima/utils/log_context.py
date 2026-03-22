"""Correlation ID context for structured logging.

Uses contextvars to thread a correlation_id through async code.
Each event gets a unique ID that appears in all log entries during its processing.
"""

from __future__ import annotations

import contextvars

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)

def set_correlation_id(cid: str) -> None:
    """Set correlation ID for the current async context."""
    _correlation_id.set(cid)

def get_correlation_id() -> str:
    """Get correlation ID for the current async context."""
    return _correlation_id.get()

def clear_correlation_id() -> None:
    """Clear correlation ID."""
    _correlation_id.set("")
