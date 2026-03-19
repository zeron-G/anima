"""Structured output — force LLM responses into validated Pydantic models.

Replaces free-text parsing for critical outputs like evolution proposals,
tool selection decisions, and importance assessments. The LLM is instructed
to respond with JSON matching a Pydantic model's schema, and the response
is validated and deserialized automatically.

Usage:
    proposal = await get_structured_output(
        llm_router,
        messages=[{"role": "user", "content": "Propose an improvement..."}],
        output_type=EvolutionProposal,
        tier=2,
    )
    if proposal:
        print(proposal.title)  # Typed, validated
"""

from __future__ import annotations

import json
import re
from typing import TypeVar, Type, Any

from anima.utils.logging import get_logger

log = get_logger("structured_output")

T = TypeVar("T")


# ── Pydantic models for structured outputs ──

try:
    from pydantic import BaseModel, Field
    HAS_PYDANTIC = True
except ImportError:
    # Fallback: use dataclasses if pydantic not installed
    HAS_PYDANTIC = False

    class BaseModel:  # type: ignore[no-redef]
        """Minimal BaseModel stub when pydantic is not installed."""
        @classmethod
        def model_json_schema(cls) -> dict:
            return {"type": "object", "properties": {}}
        @classmethod
        def model_validate(cls, data: dict):
            return cls(**data)

    def Field(*args, **kwargs):  # type: ignore[no-redef]
        return kwargs.get("default", None)


class EvolutionProposal(BaseModel):
    """Structured evolution proposal — replaces free-text parsing."""
    type: str = Field(default="fix", description="One of: fix, feature, optimization, cleanup, personality, research")
    title: str = Field(default="", description="Concise title (max 100 chars)")
    problem: str = Field(default="", description="Problem being solved (max 500 chars)")
    solution: str = Field(default="", description="Proposed solution (max 500 chars)")
    files: list[str] = Field(default_factory=list, description="Files to modify")
    risk: str = Field(default="low", description="Risk level: low, medium, high")
    breaking_change: bool = Field(default=False, description="Whether this is a breaking change")

    if HAS_PYDANTIC:
        class Config:
            extra = "ignore"  # Ignore extra fields from LLM


class ImportanceAssessment(BaseModel):
    """Structured importance assessment for memory scoring."""
    importance: float = Field(default=0.5, description="Importance score 0.0-1.0")
    reasoning: str = Field(default="", description="Brief reasoning (max 200 chars)")
    tags: list[str] = Field(default_factory=list, description="Relevant tags")

    if HAS_PYDANTIC:
        class Config:
            extra = "ignore"


# ── Core function ──


def extract_json_from_response(text: str) -> str | None:
    """Extract JSON from an LLM response that may contain markdown fences or preamble.

    Handles:
      - Pure JSON: {"key": "value"}
      - Markdown fenced: ```json\n{...}\n```
      - Preamble + JSON: "Here is the result:\n{...}"

    Returns the extracted JSON string or None if no valid JSON found.
    """
    text = text.strip()

    # Try 1: entire response is JSON
    if text.startswith("{") or text.startswith("["):
        return text

    # Try 2: markdown code fence
    fence_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text)
    if fence_match:
        return fence_match.group(1).strip()

    # Try 3: find first { ... } or [ ... ] block
    brace_match = re.search(r"\{[\s\S]*\}", text)
    if brace_match:
        return brace_match.group(0)

    bracket_match = re.search(r"\[[\s\S]*\]", text)
    if bracket_match:
        return bracket_match.group(0)

    return None


async def get_structured_output(
    llm_router: Any,
    messages: list[dict],
    output_type: Type[T],
    *,
    tier: int = 2,
    temperature: float = 0.3,
    max_retries: int = 1,
) -> T | None:
    """Force the LLM to output JSON matching a Pydantic model.

    Appends a schema instruction to the messages, calls the LLM,
    extracts JSON from the response, and validates it against the model.

    Args:
        llm_router: LLMRouter instance
        messages: Conversation messages
        output_type: Pydantic model class to validate against
        tier: LLM tier (1=Opus, 2=Sonnet)
        temperature: Lower = more deterministic JSON
        max_retries: Retry on parse failure

    Returns:
        Validated Pydantic model instance, or None on failure.
    """
    schema = output_type.model_json_schema() if HAS_PYDANTIC else {"type": "object"}

    # Build schema instruction
    schema_instruction = (
        "Respond with ONLY a valid JSON object matching this schema. "
        "No markdown fences, no preamble, no explanation — just the JSON.\n\n"
        f"Schema:\n```json\n{json.dumps(schema, indent=2, ensure_ascii=False)}\n```"
    )

    augmented_messages = list(messages) + [
        {"role": "user", "content": schema_instruction}
    ]

    for attempt in range(max_retries + 1):
        try:
            resp = await llm_router.call(augmented_messages, tier=tier, temperature=temperature)
            if not resp:
                log.warning("Structured output: LLM returned None (attempt %d)", attempt + 1)
                continue

            # Extract JSON
            json_str = extract_json_from_response(resp)
            if not json_str:
                log.warning("Structured output: no JSON found in response (attempt %d)", attempt + 1)
                if attempt < max_retries:
                    augmented_messages.append({"role": "assistant", "content": resp})
                    augmented_messages.append({"role": "user", "content": "That response was not valid JSON. Please respond with ONLY a JSON object."})
                continue

            # Parse and validate
            data = json.loads(json_str)
            result = output_type.model_validate(data)
            log.debug("Structured output parsed: %s", type(result).__name__)
            return result

        except json.JSONDecodeError as e:
            log.warning("Structured output: JSON parse error (attempt %d): %s", attempt + 1, e)
        except Exception as e:
            log.warning("Structured output: validation error (attempt %d): %s", attempt + 1, e)

    log.warning("Structured output: all attempts failed for %s", output_type.__name__)
    return None
