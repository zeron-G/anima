"""Tests for gossip protocol versioning, message dedup, and backward compat."""

import collections
import logging
import time

import msgpack
import pytest

from anima.network.protocol import NetworkMessage, PROTOCOL_VERSION
from anima.network.gossip import GossipMesh
from anima.network.node import NodeState, NodeIdentity


# ── Protocol version tests ──

def test_protocol_version_present_in_new_messages():
    """New messages carry the current protocol_version."""
    msg = NetworkMessage(type="event", source_node="n1", payload={"x": 1})
    assert msg.protocol_version == PROTOCOL_VERSION
    data = msg.pack()
    restored = NetworkMessage.unpack(data)
    assert restored.protocol_version == PROTOCOL_VERSION


def test_old_message_without_version_defaults_to_v1(caplog):
    """Messages without protocol_version (old nodes) are parsed as v1 with a warning."""
    raw = {
        "id": "msg_old123",
        "type": "event",
        "source_node": "old-node",
        "target_node": "*",
        "timestamp": time.time(),
        "ttl": 10,
        "payload": {"data": "hello"},
        "signature": "",
        # NOTE: no protocol_version key
    }
    data = msgpack.packb(raw, use_bin_type=True)
    with caplog.at_level(logging.WARNING, logger="network.protocol"):
        msg = NetworkMessage.unpack(data)
    assert msg.protocol_version == 1
    assert any("assuming v1" in r.message for r in caplog.records)


def test_protocol_version_roundtrip_sign_verify():
    """Signing and verification work with protocol_version field present."""
    msg = NetworkMessage(type="event", source_node="n1", payload={"k": "v"})
    msg.sign("secret")
    assert msg.verify("secret")
    # Tamper version → verify fails
    msg.protocol_version = 999
    assert not msg.verify("secret")


# ── Dedup tests ──

class _FakeMesh:
    """Minimal stand-in to test dedup cache logic without ZMQ."""

    def __init__(self, ttl: float = 120.0, max_size: int = 2000):
        self._seen_msgs: collections.OrderedDict = collections.OrderedDict()
        self._SEEN_TTL = ttl
        self._SEEN_MAX = max_size

    def dedup_check(self, msg_id: str, now: float | None = None) -> bool:
        """Return True if the message is a duplicate. Mirrors gossip.py logic."""
        if msg_id in self._seen_msgs:
            return True
        now_recv = now or time.time()
        self._seen_msgs[msg_id] = now_recv
        # Purge expired
        while self._seen_msgs:
            oldest_id, oldest_ts = next(iter(self._seen_msgs.items()))
            if now_recv - oldest_ts > self._SEEN_TTL:
                self._seen_msgs.popitem(last=False)
            else:
                break
        while len(self._seen_msgs) > self._SEEN_MAX:
            self._seen_msgs.popitem(last=False)
        return False


def test_duplicate_message_rejected():
    """Same message_id seen twice → second is flagged as duplicate."""
    mesh = _FakeMesh()
    assert not mesh.dedup_check("msg_aaa", now=100.0)
    assert mesh.dedup_check("msg_aaa", now=100.5)  # duplicate


def test_forwarding_loop_dedup():
    """A message forwarded through A→B→C→A is deduped at A on the return."""
    mesh = _FakeMesh()
    msg_id = "msg_loop1"
    # First arrival at node A
    assert not mesh.dedup_check(msg_id, now=100.0)
    # Same message loops back to A via C
    assert mesh.dedup_check(msg_id, now=101.0)


def test_dedup_ttl_expiry():
    """After TTL expires, the same message_id is accepted again."""
    mesh = _FakeMesh(ttl=10.0)
    assert not mesh.dedup_check("msg_x", now=100.0)
    assert mesh.dedup_check("msg_x", now=105.0)  # within TTL → dup
    # Advance past TTL; insert a new message to trigger purge
    assert not mesh.dedup_check("msg_trigger", now=111.0)
    # Now msg_x should have been purged
    assert not mesh.dedup_check("msg_x", now=111.5)


def test_dedup_max_size_cap():
    """Cache doesn't grow unbounded — oldest entries evicted at cap."""
    mesh = _FakeMesh(ttl=9999.0, max_size=5)
    base = 100.0
    for i in range(6):
        mesh.dedup_check(f"msg_{i}", now=base + i)
    # First entry should be evicted
    assert "msg_0" not in mesh._seen_msgs
    assert "msg_5" in mesh._seen_msgs
    assert len(mesh._seen_msgs) == 5
