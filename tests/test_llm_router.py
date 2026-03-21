"""Tests for LLM router — budget and fallback logic."""

import pytest

from anima.llm.router import LLMRouter


def test_budget_starts_ok():
    router = LLMRouter("t1/model", "t2/model", daily_budget=1.0)
    assert router.check_budget()


def test_usage_stats_initial():
    router = LLMRouter("t1/model", "t2/model")
    stats = router.get_usage_stats()
    assert stats["calls"] == 0
    assert stats["total_tokens"] == 0


def test_budget_day_reset():
    router = LLMRouter("t1/model", "t2/model", daily_budget=1.0)
    # Simulate yesterday's usage
    router._usage = [{"prompt_tokens": 999999, "completion_tokens": 999999}]
    router._day_start = "2020-01-01"  # old day
    assert router.check_budget()  # New day should reset


@pytest.mark.asyncio
async def test_call_returns_none_when_budget_exceeded():
    router = LLMRouter("t1/model", "t2/model", daily_budget=0.001)
    # Exhaust budget by faking a huge usage entry
    router._usage.append({"model": "opus", "prompt_tokens": 1_000_000, "completion_tokens": 100_000})
    result = await router.call([{"role": "user", "content": "hi"}])
    assert result is None
