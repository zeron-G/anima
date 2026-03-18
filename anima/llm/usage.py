"""LLM usage tracker — records every call to SQLite."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anima.memory.store import MemoryStore


class UsageTracker:
    """Wraps MemoryStore to provide a simple interface for recording LLM usage."""

    def __init__(self, memory_store: MemoryStore) -> None:
        self._store = memory_store

    def record(
        self,
        model: str,
        tier: str,
        prompt_tokens: int,
        completion_tokens: int,
        auth_mode: str = "apikey",
        event_type: str = "",
        success: bool = True,
    ) -> None:
        """Record a single LLM call.

        Detects the provider from the model name and delegates to the store.
        """
        provider = self._detect_provider(model)
        self._store.log_llm_usage(
            model=model,
            provider=provider,
            auth_mode=auth_mode,
            tier=tier,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            event_type=event_type,
            success=success,
        )

    def get_history(self, limit: int = 100) -> list[dict]:
        """Return recent LLM usage records."""
        return self._store.get_usage_history(limit)

    def get_summary(self) -> dict:
        """Return aggregated usage summary."""
        return self._store.get_usage_summary()

    @staticmethod
    def _detect_provider(model: str) -> str:
        """Infer the provider from a model name (supports prefix routing)."""
        model_lower = model.lower()
        # Prefix-based (explicit)
        if model_lower.startswith("local/"):
            return "local"
        if model_lower.startswith("openai/"):
            return "openai"
        if model_lower.startswith("anthropic/"):
            return "anthropic"
        # Name-based (implicit)
        if "claude" in model_lower or "haiku" in model_lower or "sonnet" in model_lower or "opus" in model_lower:
            return "anthropic"
        if "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower:
            return "openai"
        if "gemini" in model_lower:
            return "google"
        if "gguf" in model_lower or "llama" in model_lower or "qwen" in model_lower:
            return "local"
        return "unknown"
