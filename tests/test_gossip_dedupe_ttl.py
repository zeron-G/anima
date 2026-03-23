"""Tests for gossip dedup cache TTL expiry and size cap."""

import time

import pytest

from anima.network.gossip import GossipMesh
from anima.network.node import NodeIdentity, NodeState


def _make_mesh(**overrides) -> GossipMesh:
    """Create a GossipMesh without starting it (no ZMQ, no threads)."""
    identity = NodeIdentity.__new__(NodeIdentity)
    identity._path = None
    identity._data = {"self_id": "test-node", "registered_nodes": []}
    state = NodeState(node_id="test-node")
    defaults = dict(
        identity=identity,
        local_state=state,
        network_secret="",
        listen_port=19420,
    )
    defaults.update(overrides)
    return GossipMesh(**defaults)


class TestDedupeTTL:
    """Expired entries should be pruned so the same message can be forwarded again."""

    def test_expired_entry_pruned(self):
        mesh = _make_mesh()
        now = time.time()
        # Insert an already-expired entry
        mesh._seen_msgs["msg-old"] = now - 1.0
        mesh._prune_dedupe_cache(now)
        assert "msg-old" not in mesh._seen_msgs

    def test_unexpired_entry_blocks_forward(self):
        mesh = _make_mesh()
        now = time.time()
        # Entry that expires in the future
        mesh._seen_msgs["msg-dup"] = now + 300.0
        mesh._prune_dedupe_cache(now)
        assert "msg-dup" in mesh._seen_msgs

    def test_expiry_allows_reforward(self):
        mesh = _make_mesh()
        now = time.time()
        # Simulate message seen, then TTL passes
        mesh._seen_msgs["msg-A"] = now + 10.0
        mesh._prune_dedupe_cache(now)
        assert "msg-A" in mesh._seen_msgs  # still valid

        # Advance past expiry
        mesh._prune_dedupe_cache(now + 11.0)
        assert "msg-A" not in mesh._seen_msgs  # expired → can re-forward


class TestDedupeCapEviction:
    """When cache exceeds MAX_DEDUPE_SIZE, oldest 10% are evicted."""

    def test_over_limit_evicts_ten_percent(self):
        mesh = _make_mesh()
        mesh.MAX_DEDUPE_SIZE = 100  # small limit for testing
        now = time.time()

        # Fill to 110 entries (over limit)
        for i in range(110):
            mesh._seen_msgs[f"msg-{i}"] = now + 300.0 + i

        mesh._prune_dedupe_cache(now)
        # 10% of 110 = 11 evicted → 99 remaining
        assert len(mesh._seen_msgs) <= 100

    def test_evicts_oldest_first(self):
        mesh = _make_mesh()
        mesh.MAX_DEDUPE_SIZE = 10
        now = time.time()

        # 12 entries: first two have earliest expiry
        for i in range(12):
            mesh._seen_msgs[f"msg-{i}"] = now + 100.0 + i

        mesh._prune_dedupe_cache(now)
        # msg-0 has earliest expiry and should be evicted
        assert "msg-0" not in mesh._seen_msgs
        # msg-11 has latest expiry and should survive
        assert "msg-11" in mesh._seen_msgs

    def test_eviction_does_not_raise(self):
        mesh = _make_mesh()
        mesh.MAX_DEDUPE_SIZE = 5
        now = time.time()
        for i in range(20):
            mesh._seen_msgs[f"msg-{i}"] = now + 300.0
        # Should not raise
        mesh._prune_dedupe_cache(now)
        assert len(mesh._seen_msgs) <= 5
