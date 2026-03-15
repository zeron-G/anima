"""ToolSpec model — tool definition and risk levels."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Coroutine


class RiskLevel(IntEnum):
    SAFE = 0       # read-only, no side effects
    LOW = 1        # minor side effects (e.g., write note)
    MEDIUM = 2     # moderate side effects (e.g., write file)
    HIGH = 3       # significant side effects (e.g., shell command)
    BLOCKED = 99   # never allowed (e.g., rm -rf /)


@dataclass
class ToolSpec:
    """Specification for a registered tool."""
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.SAFE
    handler: Callable[..., Coroutine[Any, Any, Any]] | None = None

    def to_llm_schema(self) -> dict:
        """Convert to LLM function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
