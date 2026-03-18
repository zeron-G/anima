"""LLM Router — Tier1 + Tier2 with fallback chain + circuit breaker.

Circuit breaker:
  After N consecutive failures, enters OPEN state (silent mode).
  In OPEN state: returns None immediately, probes every 30s.
  On probe success: circuit CLOSES, normal operation resumes.

  Heartbeat/perception/events continue; only LLM calls pause.
  Cognitive loop notifies user when circuit opens/closes.
"""

from __future__ import annotations

import time

from anima.llm.providers import completion
from anima.utils.logging import get_logger

log = get_logger("llm_router")

# Circuit breaker tuning
_CB_FAILURE_THRESHOLD = 4       # consecutive failures to trip
_CB_PROBE_INTERVAL_S = 30      # probe every 30s (was 60 — too slow)


class LLMRouter:
    """Routes LLM calls with circuit breaker for API resilience."""

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

        # Circuit breaker
        self._consecutive_failures = 0
        self._circuit_open = False
        self._circuit_opened_at = 0.0
        self._last_probe_at = 0.0

    def set_usage_tracker(self, tracker) -> None:
        self._usage_tracker = tracker

    @property
    def circuit_open(self) -> bool:
        return self._circuit_open

    def _on_success(self) -> None:
        if self._circuit_open:
            log.info("Circuit CLOSED — API recovered after %.0fs",
                     time.time() - self._circuit_opened_at)
        self._consecutive_failures = 0
        self._circuit_open = False

    def _on_failure(self) -> None:
        self._consecutive_failures += 1
        if not self._circuit_open and self._consecutive_failures >= _CB_FAILURE_THRESHOLD:
            self._circuit_open = True
            self._circuit_opened_at = time.time()
            self._last_probe_at = time.time()
            log.warning("Circuit OPEN — %d consecutive failures, silent mode "
                        "(probe every %ds)", self._consecutive_failures, _CB_PROBE_INTERVAL_S)

    def _should_probe(self) -> bool:
        if not self._circuit_open:
            return False
        return (time.time() - self._last_probe_at) >= _CB_PROBE_INTERVAL_S

    def _check_circuit(self) -> bool | None:
        """Check circuit breaker. Returns None to proceed, False to skip."""
        if not self._circuit_open:
            return None  # proceed normally
        if self._should_probe():
            self._last_probe_at = time.time()
            log.info("Circuit probe — testing API")
            return None  # proceed with probe
        elapsed = int(time.time() - self._circuit_opened_at)
        log.warning("Circuit OPEN (%ds) — skipping LLM call, next probe in %ds",
                    elapsed, int(_CB_PROBE_INTERVAL_S - (time.time() - self._last_probe_at)))
        return False  # skip

    async def _try_call(self, messages, tier, temperature, tools=None):
        """Core call logic with timeout + retry. Returns response or None."""
        import asyncio as _aio

        max_retries = 1 if self._circuit_open else 2
        for attempt in range(max_retries + 1):
            if attempt > 0:
                wait = min(2 ** attempt * 5, 20)
                log.info("Retrying in %ds (attempt %d/%d)", wait, attempt, max_retries)
                await _aio.sleep(wait)

            # Tier 2
            if tier <= 2:
                try:
                    kwargs = dict(model=self._tier2_model, messages=messages,
                                 max_tokens=self._tier2_max_tokens, temperature=temperature)
                    if tools:
                        kwargs["tools"] = tools
                    resp = await _aio.wait_for(completion(**kwargs), timeout=120)
                    self._record_usage(resp, tier=2)
                    self._on_success()
                    return resp
                except _aio.TimeoutError:
                    log.warning("Tier2 timeout (120s)")
                    self._on_failure()
                except Exception as e:
                    if "529" in str(e) or "overloaded" in str(e).lower():
                        self._on_failure()
                        continue
                    log.warning("Tier2 failed: %s", e)

            # Tier 1 fallback
            try:
                kwargs = dict(model=self._tier1_model, messages=messages,
                             max_tokens=self._tier1_max_tokens, temperature=temperature)
                if tools:
                    kwargs["tools"] = tools
                resp = await _aio.wait_for(completion(**kwargs), timeout=150)
                self._record_usage(resp, tier=1)
                self._on_success()
                return resp
            except _aio.TimeoutError:
                log.error("Tier1 timeout (150s)")
                self._on_failure()
                return None
            except Exception as e:
                if "529" in str(e) or "overloaded" in str(e).lower():
                    self._on_failure()
                    continue
                log.error("Tier1 failed: %s", e)
                self._on_failure()
                return None

        log.error("All retries exhausted")
        self._on_failure()
        return None

    async def call(self, messages, tier=2, temperature=0.7) -> str | None:
        """Call LLM. Returns content string or None."""
        if not self.check_budget():
            log.warning("Budget exceeded")
            return None
        check = self._check_circuit()
        if check is False:
            return None
        resp = await self._try_call(messages, tier, temperature)
        return resp["content"] if resp else None

    async def call_with_tools(self, messages, tools, tier=2, temperature=0.7) -> dict | None:
        """Call LLM with tools. Returns response dict or None."""
        if not self.check_budget():
            return None
        check = self._check_circuit()
        if check is False:
            return None
        return await self._try_call(messages, tier, temperature, tools=tools)

    # ── Budget ──

    _PRICING = {
        "haiku": (0.25, 1.25),
        "sonnet": (3.0, 15.0),
        "opus": (15.0, 75.0),
    }

    def check_budget(self, estimated_cost: float = 0) -> bool:
        today = self._today()
        if today != self._day_start:
            self._usage.clear()
            self._day_start = today
        total_cost = 0.0
        for u in self._usage:
            model = u.get("model", "").lower()
            inp = u.get("prompt_tokens", 0)
            out = u.get("completion_tokens", 0)
            price = self._PRICING.get("sonnet")
            for key, rates in self._PRICING.items():
                if key in model:
                    price = rates
                    break
            total_cost += (inp * price[0] + out * price[1]) / 1_000_000
        return (total_cost + estimated_cost) < self._daily_budget

    def get_status(self) -> dict:
        return {
            "circuit_open": self._circuit_open,
            "consecutive_failures": self._consecutive_failures,
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
        if self._usage_tracker:
            from anima.llm.providers import _get_token, _is_oauth_token
            token = _get_token()
            auth = "oauth" if _is_oauth_token(token) else "apikey"
            model = resp.get("model", self._tier1_model if tier == 1 else self._tier2_model)
            try:
                self._usage_tracker.record(
                    model=model, tier=f"tier{tier}",
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    auth_mode=auth,
                )
            except Exception as e:
                log.debug("Usage tracking: %s", e)

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d")
