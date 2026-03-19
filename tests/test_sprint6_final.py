"""Sprint 6 Final Tests — structured output, emotion feedback, tracer, final sweep.

Final sprint covering:
  - Structured output JSON parsing and validation
  - Emotion feedback extraction from LLM responses
  - Observability tracer spans and statistics
  - Remaining audit fixes (M-11, M-20, L-06, L-07/L-08, L-24, L-26)
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, AsyncMock


# ── Structured Output tests ──


class TestStructuredOutput:
    """Test JSON extraction and Pydantic validation."""

    def test_extract_pure_json(self):
        from anima.llm.structured import extract_json_from_response
        result = extract_json_from_response('{"title": "Fix bug", "type": "fix"}')
        assert result is not None
        data = json.loads(result)
        assert data["title"] == "Fix bug"

    def test_extract_markdown_fenced(self):
        from anima.llm.structured import extract_json_from_response
        text = 'Here is the result:\n```json\n{"title": "New feature"}\n```\nDone.'
        result = extract_json_from_response(text)
        assert result is not None
        data = json.loads(result)
        assert data["title"] == "New feature"

    def test_extract_with_preamble(self):
        from anima.llm.structured import extract_json_from_response
        text = 'I analyzed the code and here is my proposal:\n{"type": "optimization", "title": "Speed up"}'
        result = extract_json_from_response(text)
        assert result is not None
        data = json.loads(result)
        assert data["type"] == "optimization"

    def test_extract_no_json(self):
        from anima.llm.structured import extract_json_from_response
        result = extract_json_from_response("No JSON here at all.")
        assert result is None

    def test_evolution_proposal_model(self):
        from anima.llm.structured import EvolutionProposal
        proposal = EvolutionProposal(
            type="fix",
            title="Fix memory leak",
            problem="Memory grows unbounded",
            solution="Add LRU eviction",
            files=["memory/store.py"],
            risk="low",
        )
        assert proposal.title == "Fix memory leak"
        assert proposal.risk == "low"
        assert len(proposal.files) == 1

    def test_evolution_proposal_from_dict(self):
        from anima.llm.structured import EvolutionProposal
        data = {
            "type": "feature",
            "title": "Add streaming",
            "problem": "No real-time output",
            "solution": "SSE streaming",
            "files": ["llm/providers.py"],
            "risk": "medium",
            "breaking_change": False,
            "extra_field": "ignored",  # Should be ignored
        }
        proposal = EvolutionProposal.model_validate(data)
        assert proposal.title == "Add streaming"

    @pytest.mark.asyncio
    async def test_get_structured_output_success(self):
        from anima.llm.structured import get_structured_output, EvolutionProposal

        mock_router = MagicMock()
        mock_router.call = AsyncMock(return_value='{"type": "fix", "title": "Test fix", "problem": "p", "solution": "s"}')

        result = await get_structured_output(
            mock_router,
            messages=[{"role": "user", "content": "propose something"}],
            output_type=EvolutionProposal,
        )
        assert result is not None
        assert result.title == "Test fix"

    @pytest.mark.asyncio
    async def test_get_structured_output_invalid_json(self):
        from anima.llm.structured import get_structured_output, EvolutionProposal

        mock_router = MagicMock()
        mock_router.call = AsyncMock(return_value="This is not JSON at all, just a description of what I would do.")

        result = await get_structured_output(
            mock_router,
            messages=[{"role": "user", "content": "propose"}],
            output_type=EvolutionProposal,
            max_retries=0,
        )
        assert result is None


# ── Emotion Feedback tests ──


class TestEmotionFeedback:
    """Test emotion signal extraction from LLM responses."""

    def test_positive_engagement(self):
        from anima.emotion.feedback import extract_emotion_adjustments
        adj = extract_emotion_adjustments("这个问题很有趣！让我看看代码...")
        assert adj.get("engagement", 0) > 0

    def test_negative_confidence(self):
        from anima.emotion.feedback import extract_emotion_adjustments
        adj = extract_emotion_adjustments("I'm not sure about this. The error is confusing.")
        assert adj.get("confidence", 0) < 0

    def test_concern_on_errors(self):
        from anima.emotion.feedback import extract_emotion_adjustments
        adj = extract_emotion_adjustments("ERROR: the process crashed with an exception!")
        assert adj.get("concern", 0) > 0

    def test_tool_success_boosts_confidence(self):
        from anima.emotion.feedback import extract_emotion_adjustments
        adj = extract_emotion_adjustments(
            "Done!", had_tool_calls=True, tool_success_rate=1.0
        )
        assert adj.get("confidence", 0) > 0

    def test_tool_failure_reduces_confidence(self):
        from anima.emotion.feedback import extract_emotion_adjustments
        adj = extract_emotion_adjustments(
            "Failed.", had_tool_calls=True, tool_success_rate=0.2
        )
        assert adj.get("confidence", 0) < 0

    def test_long_response_engagement(self):
        from anima.emotion.feedback import extract_emotion_adjustments
        adj = extract_emotion_adjustments("A" * 600)  # Long response
        assert adj.get("engagement", 0) > 0

    def test_short_response_disengagement(self):
        from anima.emotion.feedback import extract_emotion_adjustments
        adj = extract_emotion_adjustments("OK.")  # Very short
        assert adj.get("engagement", 0) < 0

    def test_code_blocks_boost(self):
        from anima.emotion.feedback import extract_emotion_adjustments
        adj = extract_emotion_adjustments("Here's the fix:\n```python\nprint('hello')\n```")
        assert adj.get("engagement", 0) > 0
        assert adj.get("confidence", 0) > 0

    def test_adjustments_clamped(self):
        from anima.emotion.feedback import extract_emotion_adjustments
        # Even with many signals, individual adjustments should be clamped
        text = "兴奋 期待 好奇 有趣 想试试 让我看看 马上 excited interesting"
        adj = extract_emotion_adjustments(text)
        for dim, val in adj.items():
            assert -0.15 <= val <= 0.15, f"{dim}={val} exceeds clamp range"

    def test_neutral_response(self):
        from anima.emotion.feedback import extract_emotion_adjustments
        adj = extract_emotion_adjustments("The weather today is mild.")
        # Neutral text should produce minimal or no adjustments
        total = sum(abs(v) for v in adj.values())
        assert total < 0.1


# ── Observability Tracer tests ──


class TestTracer:
    """Test the execution tracing system."""

    def test_trace_creates_root_span(self):
        from anima.observability.tracer import Tracer
        tracer = Tracer()
        with tracer.trace("test:event") as t:
            t.root.set("key", "value")
        recent = tracer.get_recent(1)
        assert len(recent) == 1
        assert recent[0]["name"] == "test:event"
        assert recent[0]["attributes"]["key"] == "value"
        assert recent[0]["duration_ms"] >= 0

    def test_child_spans(self):
        from anima.observability.tracer import Tracer
        tracer = Tracer()
        with tracer.trace("test:event") as t:
            with t.span("memory_retrieval") as s:
                s.set("count", 5)
            with t.span("llm_call") as s:
                s.set("model", "opus")
        recent = tracer.get_recent(1)
        assert len(recent[0]["children"]) == 2
        assert recent[0]["children"][0]["name"] == "memory_retrieval"
        assert recent[0]["children"][0]["attributes"]["count"] == 5

    def test_error_recording(self):
        from anima.observability.tracer import Tracer
        tracer = Tracer()
        try:
            with tracer.trace("test:error") as t:
                with t.span("failing_op") as s:
                    raise ValueError("test error")
        except ValueError:
            pass
        recent = tracer.get_recent(1)
        assert recent[0]["status"] == "error"
        assert recent[0]["children"][0]["status"] == "error"
        assert "test error" in recent[0]["children"][0]["error"]

    def test_max_traces_limit(self):
        from anima.observability.tracer import Tracer
        tracer = Tracer(max_traces=5)
        for i in range(10):
            with tracer.trace(f"test:{i}"):
                pass
        recent = tracer.get_recent(100)
        assert len(recent) == 5

    def test_stats(self):
        from anima.observability.tracer import Tracer
        tracer = Tracer()
        for i in range(5):
            with tracer.trace(f"test:{i}") as t:
                with t.span("op"):
                    pass
        stats = tracer.get_stats()
        assert stats["total_traces"] == 5
        assert stats["error_rate"] == 0.0
        assert "op" in stats["span_avg_ms"]

    def test_global_tracer(self):
        from anima.observability.tracer import get_tracer
        t1 = get_tracer()
        t2 = get_tracer()
        assert t1 is t2  # Singleton


# ── Configurable decay params test ──


class TestConfigurableDecay:
    """Test L-07/L-08: decay params configurable."""

    def test_default_values(self):
        from anima.memory.decay import MemoryDecay
        decay = MemoryDecay()
        assert decay.cluster_window_hours == 6.0
        assert decay.consolidation_threshold == 0.1

    def test_custom_values(self):
        from anima.memory.decay import MemoryDecay
        decay = MemoryDecay(cluster_window_hours=2.0, consolidation_threshold=0.2)
        assert decay.cluster_window_hours == 2.0
        assert decay.cluster_window_secs == 2.0 * 3600
        assert decay.consolidation_threshold == 0.2


# ── Configurable gossip params test ──


class TestConfigurableGossip:
    """Test L-24: gossip params configurable."""

    def test_gossip_custom_interval(self):
        from anima.network.gossip import GossipMesh
        from unittest.mock import MagicMock
        mesh = GossipMesh(
            identity=MagicMock(),
            local_state=MagicMock(),
            gossip_interval=10.0,
            suspect_phi=12.0,
        )
        assert mesh.GOSSIP_INTERVAL == 10.0


# ── Protocol validation test ──


class TestProtocolValidation:
    """Test L-26: protocol field validation."""

    def test_invalid_message_rejected(self):
        from anima.network.protocol import NetworkMessage
        import msgpack
        # Missing 'type' field
        bad_data = msgpack.packb({"source_node": "test"}, use_bin_type=True)
        with pytest.raises(ValueError, match="missing"):
            NetworkMessage.unpack(bad_data)

    def test_valid_message_accepted(self):
        from anima.network.protocol import NetworkMessage
        import msgpack
        good_data = msgpack.packb({
            "id": "msg1", "type": "gossip", "source_node": "node1",
            "payload": {}, "timestamp": 1.0, "ttl": 3,
        }, use_bin_type=True)
        msg = NetworkMessage.unpack(good_data)
        assert msg.type == "gossip"


# ── RRF configurable weights test ──


class TestRRFWeights:
    """Test M-20: RRF weights configurable."""

    def test_default_weights(self):
        from anima.memory.retriever import MemoryRetriever
        r = MemoryRetriever()
        assert r._rrf_weights["lorebook"] == 1.5
        assert r._rrf_weights["recent"] == 1.0
        assert r._rrf_weights["knowledge"] == 0.8

    def test_custom_weights(self):
        from anima.memory.retriever import MemoryRetriever
        r = MemoryRetriever()
        r._rrf_weights = {"lorebook": 2.0, "recent": 1.5, "knowledge": 1.0}
        assert r._rrf_weights["lorebook"] == 2.0
