"""Decision model — unified format for rule engine and LLM decisions."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from anima.utils.ids import gen_id


class ActionType(Enum):
    RESPOND = "respond"
    TOOL_CALL = "tool_call"
    NOOP = "noop"


@dataclass
class Decision:
    """A decision produced by either the rule engine or LLM.

    Act stage doesn't care about the source — it only reads the fields.
    """
    action: ActionType
    reasoning: str = ""
    # For RESPOND
    content: str = ""
    # For TOOL_CALL
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    # Metadata
    id: str = field(default_factory=lambda: gen_id("dec"))
    source: str = ""  # "rule_engine" | "tier1" | "tier2"
    significance: float = 0.0
    timestamp: float = field(default_factory=time.time)
