"""CognitiveContext — single container for all AgenticLoop dependencies.

Replaces the 11-setter injection pattern in the old AgenticLoop (H-24).
All required dependencies are validated at construction time — if any
are missing, the error is immediate and clear instead of a NoneType
crash at runtime.

Usage in main.py::

    ctx = CognitiveContext(
        event_queue=event_queue,
        snapshot_cache=snapshot_cache,
        memory_store=memory_store,
        emotion=emotion_state,
        llm_router=llm_router,
        tool_executor=tool_executor,
        tool_registry=tool_registry,
        prompt_compiler=prompt_compiler,
        memory_retriever=memory_retriever,
        config=config,
    )
    loop = AgenticLoop(ctx)

Architecture:

    CognitiveContext is immutable after construction.  Components that
    need to modify shared state (conversation buffer, emotion) access
    it through the context, but the context itself is not mutated.

    Optional dependencies (gossip_mesh, summarizer, etc.) default to
    None.  Code that uses them checks ``ctx.gossip_mesh is not None``
    before access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class CognitiveContext:
    """Immutable dependency container for the cognitive loop.

    All fields required for basic operation are non-optional.
    Network, summarizer, and callback fields are optional and
    gracefully degrade when absent.
    """

    # ── Required core dependencies ──

    event_queue: Any  # EventQueue
    """Async priority queue for incoming events."""

    snapshot_cache: Any  # SnapshotCache
    """Cache of recent system state snapshots."""

    memory_store: Any  # MemoryStore
    """SQLite-backed episodic memory storage."""

    emotion: Any  # EmotionState
    """4-dimensional emotion vector (engagement, confidence, curiosity, concern)."""

    llm_router: Any  # LLMRouter
    """Routes LLM calls with circuit breaker and model cascade."""

    tool_executor: Any  # ToolExecutor
    """Executes tool handlers with safety checks and timeouts."""

    tool_registry: Any  # ToolRegistry
    """Central registry of all available tools."""

    prompt_compiler: Any  # PromptCompiler
    """6-layer prompt compilation with token budget enforcement."""

    config: dict = field(default_factory=dict)
    """Application configuration dictionary."""

    # ── Optional enhanced dependencies ──

    memory_retriever: Any | None = None  # MemoryRetriever
    """Unified RRF memory retrieval (Tier 0-3 fusion)."""

    summarizer: Any | None = None  # ConversationSummarizer
    """Conversation compression manager."""

    importance_scorer: Any | None = None  # ImportanceScorer
    """Dynamic memory importance scoring."""

    token_budget: Any | None = None  # TokenBudget
    """Token allocation manager for prompt layers."""

    # ── Optional network dependencies ──

    gossip_mesh: Any | None = None
    """ZMQ gossip mesh for distributed operation."""

    heartbeat: Any | None = None
    """Heartbeat engine reference (for tick count in checkpoints)."""

    user_activity: Any | None = None
    """User activity detector (for idle detection)."""

    idle_scheduler: Any | None = None
    """Idle task scheduler."""

    reload_manager: Any | None = None
    """Hot-reload / restart manager."""

    # ── Callbacks ──

    output_callback: Callable[..., Any] | None = None
    """Called with (text, source) when the agent produces user-facing output."""

    status_callback: Callable[[dict], Any] | None = None
    """Called with status dict for dashboard activity feed."""

    stream_callback: Callable[[str, str], None] | None = None
    """Called with (chunk, event_type) for streaming text output."""

    # ── Shared mutable state ──

    conversation: list[dict] = field(default_factory=list)
    """Conversation buffer shared across components."""

    max_conversation_turns: int = 50
    """Maximum conversation turns to retain."""

    def __post_init__(self) -> None:
        """Validate that core infrastructure dependencies are provided.

        Note: prompt_compiler is validated at run() time, not here,
        because main.py sets it via a setter after construction.
        """
        # These must be present at construction time
        required_at_init = [
            "event_queue", "snapshot_cache", "memory_store",
            "emotion", "llm_router", "tool_executor",
            "tool_registry",
        ]
        missing = [name for name in required_at_init if getattr(self, name) is None]
        if missing:
            raise ValueError(
                f"CognitiveContext missing required dependencies: {missing}. "
                f"Check initialization order in main.py."
            )

    def validate_ready(self) -> None:
        """Validate that ALL dependencies are set (call before run()).

        prompt_compiler is required but may be set after construction.
        """
        if self.prompt_compiler is None:
            raise ValueError(
                "CognitiveContext.prompt_compiler is None. "
                "Call set_prompt_compiler() before running the agentic loop."
            )

    # ── Convenience methods ──

    def emit_status(self, status: dict) -> None:
        """Emit a status update to the dashboard."""
        if self.status_callback:
            try:
                self.status_callback(status)
            except Exception:
                pass

    def emit_output(self, text: str, source: str = "") -> None:
        """Emit agent output to the terminal/channels."""
        if self.output_callback:
            self.output_callback(text, source=source)

    def emit_stream(self, chunk: str, event_type: str = "text") -> None:
        """Emit a streaming text chunk."""
        if self.stream_callback:
            self.stream_callback(chunk, event_type)

    def trim_conversation(self) -> None:
        """Trim conversation buffer to max_conversation_turns * 2."""
        max_msgs = self.max_conversation_turns * 2
        if len(self.conversation) > max_msgs:
            self.conversation[:] = self.conversation[-max_msgs:]

    def get_recent_conversation(self, n: int = 4) -> list[dict]:
        """Get the last N conversation entries."""
        return self.conversation[-n:] if self.conversation else []
