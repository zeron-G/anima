"""Tests for gossip message dedup cache: TTL cleanup and capacity eviction."""

import time
from unittest.mock import MagicMock

from anima.network.gossip import GossipMesh


def _make_mesh(**kwargs):
    """Create a GossipMesh instance without starting it."""
    return GossipMesh(
        identity=MagicMock(),
        local_state=MagicMock(),
        **kwargs,
    )


class TestDedupTTL:
    """TTL expiry allows re-receipt of the same message."""

    def test_expired_msg_allowed_again(self):
        mesh = _make_mesh()
        mesh._seen_ttl = 0.1  # 100ms TTL for fast test
        mesh._seen_cleanup_interval_s = 0.0  # always allow cleanup

        # First time: not a duplicate
        assert mesh._dedup_check("msg-1") is False
        # Immediately: is a duplicate
        assert mesh._dedup_check("msg-1") is True

        # Wait for TTL to expire, then cleanup
        time.sleep(0.15)
        mesh._dedup_cleanup()

        # After cleanup: msg-1 was removed, so it should be allowed again
        assert mesh._dedup_check("msg-1") is False

    def test_non_expired_msg_still_blocked(self):
        mesh = _make_mesh()
        mesh._seen_ttl = 10.0  # long TTL
        mesh._seen_cleanup_interval_s = 0.0

        assert mesh._dedup_check("msg-1") is False
        mesh._dedup_cleanup()
        # TTL not reached — still blocked
        assert mesh._dedup_check("msg-1") is True


class TestDedupCapacity:
    """Over-capacity triggers eviction of oldest entries."""

    def test_evicts_oldest_when_over_cap(self):
        mesh = _make_mesh()
        mesh._seen_max_entries = 3

        for i in range(3):
            assert mesh._dedup_check(f"msg-{i}") is False

        # Insert a 4th — should evict msg-0 (oldest)
        assert mesh._dedup_check("msg-3") is False
        assert len(mesh._seen_msgs) == 3

        # msg-0 was evicted, so it can be received again
        assert mesh._dedup_check("msg-0") is False

    def test_within_cap_no_eviction(self):
        mesh = _make_mesh()
        mesh._seen_max_entries = 10

        for i in range(5):
            assert mesh._dedup_check(f"msg-{i}") is False

        # All 5 should still be cached
        assert len(mesh._seen_msgs) == 5
        for i in range(5):
            assert mesh._dedup_check(f"msg-{i}") is True
