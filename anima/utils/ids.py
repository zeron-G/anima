"""ID generation utilities."""

import uuid
import time


def gen_id(prefix: str = "") -> str:
    """Generate a unique ID with optional prefix."""
    short = uuid.uuid4().hex[:12]
    if prefix:
        return f"{prefix}_{short}"
    return short


def timestamp_id(prefix: str = "") -> str:
    """Generate a timestamp-based ID for ordering."""
    ts = int(time.time() * 1000)
    short = uuid.uuid4().hex[:6]
    if prefix:
        return f"{prefix}_{ts}_{short}"
    return f"{ts}_{short}"
