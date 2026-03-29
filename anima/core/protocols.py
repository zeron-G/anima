"""Protocol interfaces for CognitiveContext subsystems.

Zero runtime cost — pure structural typing via typing.Protocol.
CognitiveContext satisfies all protocols automatically by duck typing.
Stages can type-hint against narrow protocols instead of the full context.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class HasMemory(Protocol):
    """Components that access the memory subsystem."""
    memory_store: Any
    memory_retriever: Any | None
    importance_scorer: Any | None


@runtime_checkable
class HasEmotion(Protocol):
    """Components that access emotion state."""
    emotion: Any


@runtime_checkable
class HasLLM(Protocol):
    """Components that access LLM routing and prompt compilation."""
    llm_router: Any
    prompt_compiler: Any
    token_budget: Any | None


@runtime_checkable
class HasTools(Protocol):
    """Components that access tool execution."""
    tool_executor: Any
    tool_registry: Any


@runtime_checkable
class HasSession(Protocol):
    """Components that access session and conversation state."""
    session_manager: Any | None
    conversation: list[dict]
    max_conversation_turns: int


@runtime_checkable
class HasCallbacks(Protocol):
    """Components that emit output/status/stream callbacks."""
    output_callback: Callable[..., Any] | None
    status_callback: Callable[[dict], Any] | None
    stream_callback: Callable[[str, str], None] | None


@runtime_checkable
class HasGovernance(Protocol):
    """Components that access the governance engine."""
    governance: Any | None
