"""LLM Router — Tier1 + Tier2 with fallback chain."""

from __future__ import annotations

import time

from anima.llm.providers import completion
from anima.utils.logging import get_logger

log = get_logger("llm_router")


class LLMRouter:
    """Routes LLM calls: Tier2 → fallback Tier1 → fallback None.

    Tracks usage for budget enforcement.
    """

    def __init__(
        self,
        tier1_model: str,
        tier2_model: str,
        tier1_max_tokens: int = 4096,
        tier2_max_tokens: int = 2048,
        daily_budget: float = 1.0,
    ) -> None:
        self._tier1_model = tier1_model
        self._tier2_model = tier2_model
        self._tier1_max_tokens = tier1_max_tokens
        self._tier2_max_tokens = tier2_max_tokens
        self._daily_budget = daily_budget
        self._usage: list[dict] = []
        self._day_start = self._today()
        self._usage_tracker = None  # Optional: set via set_usage_tracker()

    def set_usage_tracker(self, tracker) -> None:
        """Attach a UsageTracker for persistent LLM call logging."""
        self._usage_tracker = tracker

    async def call(
        self,
        messages: list[dict],
        tier: int = 2,
        temperature: float = 0.7,
    ) -> str | None:
        """Call LLM at specified tier. Falls back on failure.

        Returns content string, or None if all tiers fail.
        """
        if not self.check_budget():
            log.warning("Daily budget exceeded, skipping LLM call")
            return None

        if tier <= 2:
            try:
                resp = await completion(
                    model=self._tier2_model,
                    messages=messages,
                    max_tokens=self._tier2_max_tokens,
                    temperature=temperature,
                )
                self._record_usage(resp, tier=2)
                return resp["content"]
            except Exception as e:
                log.warning("Tier2 failed: %s, falling back to Tier1", e)

        # Tier1 fallback
        try:
            resp = await completion(
                model=self._tier1_model,
                messages=messages,
                max_tokens=self._tier1_max_tokens,
                temperature=temperature,
            )
            self._record_usage(resp, tier=1)
            return resp["content"]
        except Exception as e:
            log.error("Tier1 also failed: %s", e)
            return None

    async def call_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tier: int = 2,
        temperature: float = 0.7,
    ) -> dict | None:
        """Call LLM with tool definitions. Returns full response dict or None."""
        if not self.check_budget():
            return None

        if tier <= 2:
            try:
                resp = await completion(
                    model=self._tier2_model,
                    messages=messages,
                    max_tokens=self._tier2_max_tokens,
                    temperature=temperature,
                    tools=tools,
                )
                self._record_usage(resp, tier=2)
                return resp
            except Exception as e:
                log.warning("Tier2 (tools) failed: %s", e)

        try:
            resp = await completion(
                model=self._tier1_model,
                messages=messages,
                max_tokens=self._tier1_max_tokens,
                temperature=temperature,
                tools=tools,
            )
            self._record_usage(resp, tier=1)
            return resp
        except Exception as e:
            log.error("Tier1 (tools) also failed: %s", e)
            return None

    # Per-1M-token pricing
    _PRICING = {
        "haiku": (0.25, 1.25),    # input, output per 1M
        "sonnet": (3.0, 15.0),
        "opus": (15.0, 75.0),
    }

    def check_budget(self) -> bool:
        """Check if we're within daily budget using real pricing."""
        today = self._today()
        if today != self._day_start:
            self._usage.clear()
            self._day_start = today
        total_cost = 0.0
        for u in self._usage:
            model = u.get("model", "").lower()
            inp = u.get("prompt_tokens", 0)
            out = u.get("completion_tokens", 0)
            # Match pricing by model name
            price = self._PRICING.get("sonnet")  # default
            for key, rates in self._PRICING.items():
                if key in model:
                    price = rates
                    break
            total_cost += (inp * price[0] + out * price[1]) / 1_000_000
        return total_cost < self._daily_budget

    def get_usage_stats(self) -> dict:
        total_prompt = sum(u.get("prompt_tokens", 0) for u in self._usage)
        total_completion = sum(u.get("completion_tokens", 0) for u in self._usage)
        return {
            "calls": len(self._usage),
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
        }

    def _record_usage(self, resp: dict, tier: int) -> None:
        usage = resp.get("usage", {})
        usage["tier"] = tier
        usage["timestamp"] = time.time()
        usage["model"] = resp.get("model", "")
        self._usage.append(usage)

        # Persist to SQLite via tracker
        if self._usage_tracker:
            from anima.llm.providers import _get_token, _is_oauth_token
            token = _get_token()
            auth = "oauth" if _is_oauth_token(token) else "apikey"
            model = resp.get("model", self._tier1_model if tier == 1 else self._tier2_model)
            tier_name = f"tier{tier}"
            try:
                self._usage_tracker.record(
                    model=model,
                    tier=tier_name,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    auth_mode=auth,
                )
            except Exception as e:
                log.debug("Usage tracking failed: %s", e)

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d")
