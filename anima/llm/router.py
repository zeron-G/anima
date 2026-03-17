"""LLM Router — Tier1 + Tier2 with fallback chain + circuit breaker.

Circuit breaker (silent mode):
  When consecutive failures exceed threshold, the router enters OPEN state.
  In OPEN state, all LLM calls return None immediately (no API request).
  A probe call is attempted every `probe_interval_s` seconds.
  If the probe succeeds, the circuit closes and normal operation resumes.

  This prevents Eva from hanging when the API is overloaded — heartbeat
  continues, events queue up, and once the API recovers, queued events
  are processed by priority.
"""

from __future__ import annotations

import time

from anima.llm.providers import completion
from anima.utils.logging import get_logger

log = get_logger("llm_router")

# Circuit breaker constants
_CB_FAILURE_THRESHOLD = 5       # consecutive failures to open circuit
_CB_PROBE_INTERVAL_S = 60      # seconds between probe attempts when open
_CB_HALF_OPEN_TIMEOUT_S = 30   # timeout for probe call


class LLMRouter:
    """Routes LLM calls: Tier2 → fallback Tier1 → fallback None.

    Includes circuit breaker: after N consecutive failures, enters
    silent mode — returns None instantly, probes periodically.
    Heartbeat/perception/events continue; only LLM calls pause.
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
        self._usage_tracker = None

        # Circuit breaker state
        self._consecutive_failures = 0
        self._circuit_open = False
        self._circuit_opened_at = 0.0
        self._last_probe_at = 0.0

    def set_usage_tracker(self, tracker) -> None:
        """Attach a UsageTracker for persistent LLM call logging."""
        self._usage_tracker = tracker

    @property
    def circuit_open(self) -> bool:
        """True when in silent mode (API unavailable)."""
        return self._circuit_open

    def _on_success(self) -> None:
        """Reset circuit breaker on successful call."""
        if self._circuit_open:
            log.info("Circuit breaker CLOSED — API recovered after %.0fs",
                     time.time() - self._circuit_opened_at)
        self._consecutive_failures = 0
        self._circuit_open = False

    def _on_failure(self) -> None:
        """Track failure, open circuit if threshold exceeded."""
        self._consecutive_failures += 1
        if not self._circuit_open and self._consecutive_failures >= _CB_FAILURE_THRESHOLD:
            self._circuit_open = True
            self._circuit_opened_at = time.time()
            self._last_probe_at = time.time()
            log.warning("Circuit breaker OPEN — %d consecutive failures, "
                        "entering silent mode (will probe every %ds)",
                        self._consecutive_failures, _CB_PROBE_INTERVAL_S)

    def _should_probe(self) -> bool:
        """Check if it's time for a health probe."""
        if not self._circuit_open:
            return False
        return (time.time() - self._last_probe_at) >= _CB_PROBE_INTERVAL_S

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

        # Circuit breaker: if open, skip unless it's probe time
        if self._circuit_open and not self._should_probe():
            log.debug("Circuit OPEN — skipping LLM call (next probe in %ds)",
                       int(_CB_PROBE_INTERVAL_S - (time.time() - self._last_probe_at)))
            return None
        if self._circuit_open:
            self._last_probe_at = time.time()
            log.info("Circuit breaker probe — testing API availability")

        import asyncio as _aio
        max_retries = 2 if self._circuit_open else 3
        for attempt in range(max_retries + 1):
            if attempt > 0:
                wait = min(2 ** attempt * 5, 30)
                log.info("API overloaded, backing off %ds (attempt %d/%d)", wait, attempt, max_retries)
                await _aio.sleep(wait)

            if tier <= 2:
                try:
                    resp = await _aio.wait_for(completion(
                        model=self._tier2_model,
                        messages=messages,
                        max_tokens=self._tier2_max_tokens,
                        temperature=temperature,
                    ), timeout=60)
                    self._record_usage(resp, tier=2)
                    self._on_success()
                    return resp["content"]
                except _aio.TimeoutError:
                    log.warning("Tier2 timed out after 60s")
                    self._on_failure()
                except Exception as e:
                    if "529" in str(e) or "overloaded" in str(e).lower():
                        self._on_failure()
                        continue
                    log.warning("Tier2 failed: %s, falling back to Tier1", e)

            try:
                resp = await _aio.wait_for(completion(
                    model=self._tier1_model,
                    messages=messages,
                    max_tokens=self._tier1_max_tokens,
                    temperature=temperature,
                ), timeout=90)
                self._record_usage(resp, tier=1)
                self._on_success()
                return resp["content"]
            except _aio.TimeoutError:
                log.error("Tier1 timed out after 90s")
                self._on_failure()
                return None
            except Exception as e:
                if "529" in str(e) or "overloaded" in str(e).lower():
                    self._on_failure()
                    continue
                log.error("Tier1 also failed: %s", e)
                self._on_failure()
                return None

        log.error("All LLM retries exhausted")
        self._on_failure()
        return None

    async def call_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tier: int = 2,
        temperature: float = 0.7,
    ) -> dict | None:
        """Call LLM with tools. Circuit breaker + retry on 529."""
        if not self.check_budget():
            return None

        # Circuit breaker
        if self._circuit_open and not self._should_probe():
            return None
        if self._circuit_open:
            self._last_probe_at = time.time()
            log.info("Circuit breaker probe (tools) — testing API")

        import asyncio as _aio

        max_retries = 2 if self._circuit_open else 3
        for attempt in range(max_retries + 1):
            if attempt > 0:
                wait = min(2 ** attempt * 5, 30)
                log.info("API overloaded, backing off %ds (attempt %d/%d)", wait, attempt, max_retries)
                await _aio.sleep(wait)

            if tier <= 2:
                try:
                    resp = await _aio.wait_for(completion(
                        model=self._tier2_model,
                        messages=messages,
                        max_tokens=self._tier2_max_tokens,
                        temperature=temperature,
                        tools=tools,
                    ), timeout=60)
                    self._record_usage(resp, tier=2)
                    self._on_success()
                    return resp
                except _aio.TimeoutError:
                    log.warning("Tier2 (tools) timed out after 60s")
                    self._on_failure()
                except Exception as e:
                    err_str = str(e)
                    if "529" in err_str or "overloaded" in err_str.lower():
                        self._on_failure()
                        continue
                    log.warning("Tier2 (tools) failed: %s", e)

            try:
                resp = await _aio.wait_for(completion(
                    model=self._tier1_model,
                    messages=messages,
                    max_tokens=self._tier1_max_tokens,
                    temperature=temperature,
                    tools=tools,
                ), timeout=90)
                self._record_usage(resp, tier=1)
                self._on_success()
                return resp
            except _aio.TimeoutError:
                log.error("Tier1 (tools) timed out after 90s")
                self._on_failure()
                return None
            except Exception as e:
                err_str = str(e)
                if "529" in err_str or "overloaded" in err_str.lower():
                    self._on_failure()
                    continue
                log.error("Tier1 (tools) also failed: %s", e)
                self._on_failure()
                return None

        log.error("All LLM retries exhausted after %d attempts", max_retries + 1)
        self._on_failure()
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

    def get_status(self) -> dict:
        """Get router status including circuit breaker state."""
        return {
            "circuit_open": self._circuit_open,
            "consecutive_failures": self._consecutive_failures,
            "circuit_opened_at": self._circuit_opened_at if self._circuit_open else None,
            "seconds_in_silent_mode": int(time.time() - self._circuit_opened_at) if self._circuit_open else 0,
        }

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
