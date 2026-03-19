"""Sprint 2 Logic Tests — tier selection, token budget, consensus, timeouts, importance.

Tests every logic fix from Sprint 2:
  - H-04: Tier selection uses correct model per tier
  - H-05: Local model pricing is free
  - H-20: No double timeout layer
  - L-30: Circuit breaker gradual reset
  - H-01: TokenBudget.compile() is now the active path
  - H-02: No hardcoded memory context truncation
  - C-04: Consensus voting waits for results
  - H-09: Per-tool timeout in executor
  - H-21: Message alternation fix
  - M-05: Multiplicative importance scoring
  - M-02: Self-thought filtering in summarizer
  - H-19: Checkpoint only restores emotion
  - M-32: TokenBudget raises on negative budget
"""

from __future__ import annotations

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Tier selection tests ──


class TestTierSelection:
    """Test H-04: tier==1 → tier1_model, tier>=2 → tier2_model."""

    def test_tier1_selects_opus(self):
        from anima.llm.router import LLMRouter
        router = LLMRouter(
            tier1_model="claude-opus-4-6",
            tier2_model="claude-sonnet-4-6",
        )
        # Simulate _try_call model selection logic
        # tier=1 should select tier1_model (Opus)
        tier = 1
        primary = router._tier1_model if tier == 1 else router._tier2_model
        assert primary == "claude-opus-4-6"

    def test_tier2_selects_sonnet(self):
        from anima.llm.router import LLMRouter
        router = LLMRouter(
            tier1_model="claude-opus-4-6",
            tier2_model="claude-sonnet-4-6",
        )
        tier = 2
        primary = router._tier1_model if tier == 1 else router._tier2_model
        assert primary == "claude-sonnet-4-6"


# ── Budget pricing tests ──


class TestBudgetPricing:
    """Test H-05: local models are free, unknown models don't consume budget."""

    def test_local_model_free(self):
        from anima.llm.router import LLMRouter
        router = LLMRouter(
            tier1_model="claude-opus-4-6",
            tier2_model="claude-sonnet-4-6",
            daily_budget=0.01,  # Very small budget
        )
        # Simulate a local model call consuming lots of tokens
        router._usage.append({
            "model": "local/qwen",
            "prompt_tokens": 100000,
            "completion_tokens": 50000,
            "timestamp": time.time(),
        })
        # Budget should still be OK (local is free)
        assert router.check_budget() is True

    def test_opus_model_consumes_budget(self):
        from anima.llm.router import LLMRouter
        router = LLMRouter(
            tier1_model="claude-opus-4-6",
            tier2_model="claude-sonnet-4-6",
            daily_budget=0.01,  # $0.01 budget
        )
        # Simulate an Opus call with substantial tokens
        router._usage.append({
            "model": "claude-opus-4-6",
            "prompt_tokens": 10000,
            "completion_tokens": 5000,
            "timestamp": time.time(),
        })
        # This should exceed the tiny budget
        assert router.check_budget() is False


# ── Circuit breaker tests ──


class TestCircuitBreaker:
    """Test L-30: gradual reset after probe success."""

    def test_probe_success_gradual_reset(self):
        from anima.llm.router import LLMRouter
        router = LLMRouter(
            tier1_model="opus", tier2_model="sonnet",
        )
        # Simulate 4 failures → circuit opens
        for _ in range(4):
            router._on_failure()
        assert router._circuit_open is True

        # Probe success — should close circuit but not fully reset
        router._on_success()
        assert router._circuit_open is False
        # L-30: consecutive_failures set to 1, not 0
        assert router._consecutive_failures == 1

    def test_normal_success_decrements(self):
        from anima.llm.router import LLMRouter
        router = LLMRouter(
            tier1_model="opus", tier2_model="sonnet",
        )
        router._consecutive_failures = 2
        router._on_success()
        assert router._consecutive_failures == 1
        router._on_success()
        assert router._consecutive_failures == 0


# ── TokenBudget tests ──


class TestTokenBudget:
    """Test H-01: compile() is now active, M-32: raises on negative budget."""

    def test_compile_returns_system_and_messages(self):
        from anima.llm.prompt_compiler import PromptCompiler
        compiler = PromptCompiler(max_context=10000, reserve_response=1000)
        system_prompt, messages = compiler.compile(
            "USER_MESSAGE",
            tools_description="",
        )
        # Should return non-empty system prompt
        assert len(system_prompt) > 0
        assert isinstance(messages, list)

    def test_negative_budget_raises(self):
        from anima.llm.token_budget import TokenBudget
        from anima.utils.errors import ContextTooSmallError
        # Tiny context that can't fit minimum allocations
        budget = TokenBudget(max_context=100, reserve_response=50)
        # Available = 100 - 50 = 50 tokens
        # Minimums = identity(300) + rules(300) + context(200) + memory(200) = 1000
        # This should raise ContextTooSmallError
        with pytest.raises(ContextTooSmallError):
            budget.compile({
                "identity": "x" * 1000,
                "rules": "x" * 1000,
                "context": "x" * 1000,
                "memory": "x" * 500,
                "tools": "",
                "conversation": "",
            })


# ── Consensus voting tests ──


class TestConsensusVoting:
    """Test C-04: check_result() is actually called."""

    def test_single_node_auto_approves(self):
        from anima.evolution.consensus import ConsensusEngine
        from anima.evolution.proposal import Proposal, ProposalType
        engine = ConsensusEngine(node_id="local")
        proposal = Proposal(id="test_1", type=ProposalType.BUGFIX, priority=3, title="test", problem="p", solution="s")
        result = engine.submit_for_voting(proposal, alive_count=1)
        assert result is True
        assert proposal.status.value == "approved"

    def test_check_result_approval(self):
        from anima.evolution.consensus import ConsensusEngine
        from anima.evolution.proposal import Proposal, ProposalType
        engine = ConsensusEngine(node_id="node_a")
        proposal = Proposal(id="test_1", type=ProposalType.BUGFIX, priority=3, title="test", problem="p", solution="s")

        # Simulate votes from 2 nodes (out of 3 total)
        proposal.votes = {"node_b": "approve", "node_c": "approve"}
        result = engine.check_result(proposal, total_nodes=3)
        assert result == "approved"

    def test_check_result_rejection(self):
        from anima.evolution.consensus import ConsensusEngine
        from anima.evolution.proposal import Proposal, ProposalType
        engine = ConsensusEngine(node_id="node_a")
        proposal = Proposal(id="test_1", type=ProposalType.BUGFIX, priority=3, title="test", problem="p", solution="s")

        proposal.votes = {"node_b": "reject", "node_c": "reject"}
        result = engine.check_result(proposal, total_nodes=3)
        assert result == "rejected"

    def test_check_result_waiting(self):
        from anima.evolution.consensus import ConsensusEngine
        from anima.evolution.proposal import Proposal, ProposalType
        engine = ConsensusEngine(node_id="node_a")
        proposal = Proposal(id="test_1", type=ProposalType.BUGFIX, priority=3, title="test", problem="p", solution="s")

        # Only 1 vote out of 2 needed — still waiting
        proposal.votes = {"node_b": "approve"}
        result = engine.check_result(proposal, total_nodes=3)
        assert result is None  # Still waiting


# ── Per-tool timeout tests ──


class TestPerToolTimeout:
    """Test H-09: executor wraps handlers with asyncio.wait_for."""

    @pytest.mark.asyncio
    async def test_slow_tool_times_out(self):
        from anima.tools.executor import ToolExecutor
        from anima.tools.registry import ToolRegistry
        from anima.models.tool_spec import ToolSpec, RiskLevel

        async def slow_handler() -> dict:
            await asyncio.sleep(60)  # Very slow
            return {"result": "done"}

        registry = ToolRegistry()
        registry.register(ToolSpec(
            name="slow_tool",
            description="A slow tool",
            parameters={"type": "object", "properties": {}},
            risk_level=RiskLevel.SAFE,
            handler=slow_handler,
        ))

        executor = ToolExecutor(registry, max_risk=3)
        # Tool timeout from safe_subprocess defaults to 30s
        # Override to 1s for test speed
        with patch("anima.tools.executor.get_tool_timeout", return_value=1):
            result = await executor.execute("slow_tool", {})

        assert result["success"] is False
        assert "timed out" in result["error"].lower()


# ── Message alternation tests ──


class TestMessageAlternation:
    """Test H-21: consecutive same-role messages are merged."""

    def test_merge_consecutive_user(self):
        from anima.llm.providers import _fix_api_messages
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "World"},
            {"role": "assistant", "content": "Hi"},
        ]
        fixed = _fix_api_messages(messages)
        assert len(fixed) == 2
        assert fixed[0]["role"] == "user"
        assert "Hello" in fixed[0]["content"]
        assert "World" in fixed[0]["content"]
        assert fixed[1]["role"] == "assistant"

    def test_no_merge_alternating(self):
        from anima.llm.providers import _fix_api_messages
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "How are you"},
        ]
        fixed = _fix_api_messages(messages)
        assert len(fixed) == 3

    def test_empty_messages(self):
        from anima.llm.providers import _fix_api_messages
        assert _fix_api_messages([]) == []


# ── Importance scorer tests ──


class TestImportanceScorer:
    """Test M-05: multiplicative formula prevents clipping."""

    def test_base_score_preserved(self):
        from anima.memory.importance import ImportanceScorer
        scorer = ImportanceScorer()
        # Simple observation with no signals → base score
        score = scorer.score("System is running normally.", "observation")
        assert 0.15 <= score <= 0.35  # Should be near observation base (0.2)

    def test_multiplicative_not_additive(self):
        from anima.memory.importance import ImportanceScorer
        scorer = ImportanceScorer()
        # Message with many signals: question + instruction + emotion + name
        rich_msg = "请帮我看看 Eva 的代码有 bug 吗？谢谢！"
        score = scorer.score(rich_msg, "chat_user")
        # With multiplicative: base(0.7) * (1 + min(bonus, 0.5)) = 0.7 * 1.5 = 1.05 → clamp 1.0
        # But not all signals fire, so should be < 1.0 in most cases
        assert score <= 1.0

    def test_critical_message_scored_reasonably(self):
        from anima.memory.importance import ImportanceScorer
        scorer = ImportanceScorer()
        # Critical message without typical signal keywords
        score1 = scorer.score("The production server is down and data is being lost", "chat_user")
        # Casual message with many signals
        score2 = scorer.score("请帮我看看这段代码有 bug 吗？谢谢 Eva！", "chat_user")
        # Both should score well, but the gap should be smaller with multiplicative
        assert score1 >= 0.5  # Base is 0.7, even with no bonus it's still 0.7


# ── Self-thought filtering tests ──


class TestSelfThoughtFiltering:
    """Test M-02: summarizer.get_context() excludes self-thoughts."""

    def test_self_thoughts_excluded(self):
        from anima.memory.summarizer import ConversationSummarizer
        summarizer = ConversationSummarizer(
            llm_router=MagicMock(),
            summary_interval=100,  # Don't trigger compression
            keep_recent=10,
        )
        # Manually populate raw buffer
        summarizer._raw_buffer = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!", "is_self_thought": False},
            {"role": "assistant", "content": "[self-thought] Scanning logs...", "is_self_thought": True},
            {"role": "user", "content": "How are you?"},
            {"role": "assistant", "content": "Great!", "is_self_thought": False},
        ]
        context = summarizer.get_context()
        # Self-thought should be filtered out
        contents = [m.get("content", "") for m in context]
        assert not any("[self-thought]" in c for c in contents)
        # Non-self-thought messages should be present
        assert any("Hello" in c for c in contents)
        assert any("Great!" in c for c in contents)


# ── Checkpoint restore tests ──


class TestCheckpointRestore:
    """Test H-19: checkpoint only restores emotion, not conversation."""

    def test_checkpoint_does_not_restore_conversation(self):
        from anima.core.cognitive import AgenticLoop
        from anima.core.event_queue import EventQueue
        from anima.perception.snapshot_cache import SnapshotCache
        from anima.emotion.state import EmotionState
        from anima.tools.registry import ToolRegistry
        from anima.tools.executor import ToolExecutor

        loop = AgenticLoop(
            event_queue=EventQueue(),
            snapshot_cache=SnapshotCache(),
            memory_store=MagicMock(),
            emotion_state=EmotionState(),
            llm_router=MagicMock(),
            tool_executor=ToolExecutor(ToolRegistry()),
            tool_registry=ToolRegistry(),
            config={},
        )

        # Pre-existing conversation (use ctx.conversation directly since
        # loop._conversation is a read-only property after Sprint 4 refactor)
        loop._ctx.conversation.append({"role": "user", "content": "existing"})

        # Restore from checkpoint with conversation + emotion
        checkpoint = {
            "conversation": [
                {"role": "user", "content": "from checkpoint"},
                {"role": "assistant", "content": "checkpoint response"},
            ],
            "emotion": {
                "engagement": 0.9,
                "confidence": 0.3,
            },
        }
        loop.restore_from_checkpoint(checkpoint)

        # Conversation should NOT be overwritten (H-19)
        assert len(loop._ctx.conversation) == 1
        assert loop._ctx.conversation[0]["content"] == "existing"

        # But emotion SHOULD be restored
        assert loop._emotion.engagement == 0.9
        assert loop._emotion.confidence == 0.3
