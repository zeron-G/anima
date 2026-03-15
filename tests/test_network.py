"""Tests for the network module."""

import asyncio
import time
import pytest

from anima.network.protocol import NetworkMessage
from anima.network.node import NodeState, NodeIdentity, NodeStatus
from anima.network.gossip import PhiAccrualDetector, GossipMesh
from anima.network.discovery import get_local_ip


# ── Protocol tests ──

def test_message_pack_unpack():
    msg = NetworkMessage(type="gossip", source_node="node-a", payload={"key": "value"})
    data = msg.pack()
    msg2 = NetworkMessage.unpack(data)
    assert msg2.type == "gossip"
    assert msg2.source_node == "node-a"
    assert msg2.payload["key"] == "value"


def test_message_sign_verify():
    msg = NetworkMessage(type="event", source_node="node-a", payload={"text": "hello"})
    msg.sign("my-secret")
    assert msg.verify("my-secret")
    assert not msg.verify("wrong-secret")


def test_message_sign_tamper():
    msg = NetworkMessage(type="event", source_node="node-a", payload={"text": "hello"})
    msg.sign("secret")
    msg.payload["text"] = "tampered"
    assert not msg.verify("secret")


# ── Node tests ──

def test_node_state_bump():
    state = NodeState(node_id="test-1")
    old_version = state.version
    state.bump_version()
    assert state.version == old_version + 1
    assert state.heartbeat_ts > 0


def test_node_state_dict_roundtrip():
    state = NodeState(node_id="test-1", hostname="mypc", capabilities=["shell", "llm"])
    d = state.to_dict()
    state2 = NodeState.from_dict(d)
    assert state2.node_id == "test-1"
    assert state2.capabilities == ["shell", "llm"]


def test_node_identity_creates_on_first_run(tmp_path):
    import json
    # Override data_dir to use tmp
    node_file = tmp_path / "node.json"
    identity = NodeIdentity.__new__(NodeIdentity)
    identity._path = node_file
    identity._data = identity._load()

    assert identity.node_id.startswith("anima-")
    assert len(identity.registered_nodes) == 1
    assert node_file.exists()


def test_node_identity_majority():
    identity = NodeIdentity.__new__(NodeIdentity)
    identity._path = None
    identity._data = {
        "self_id": "node-a",
        "registered_nodes": [
            {"id": "node-a", "status": "alive"},
            {"id": "node-b", "status": "alive"},
            {"id": "node-c", "status": "alive"},
        ]
    }
    # 2 visible out of 3 = 66% > 50% = majority
    assert identity.is_majority(2) == True
    # 1 visible out of 3 = 33% < 50% = minority
    assert identity.is_majority(1) == False


# ── Phi Accrual tests ──

def test_phi_detector_healthy():
    d = PhiAccrualDetector()
    # Simulate regular heartbeats at 5s intervals
    base = time.time()
    for i in range(10):
        d._last_seen["node-a"] = base + i * 5
        d._intervals["node-a"].append(5.0)
    d._last_seen["node-a"] = time.time()  # just received

    phi = d.phi("node-a")
    assert phi < 2.0  # Should be low — node is healthy


def test_phi_detector_dead():
    d = PhiAccrualDetector()
    d._last_seen["node-a"] = time.time() - 120  # 2 minutes ago
    d._intervals["node-a"] = [5.0] * 20  # History says 5s intervals

    phi = d.phi("node-a")
    assert phi > 16.0  # Should be very high — node is dead


# ── Discovery tests ──

def test_get_local_ip():
    ip = get_local_ip()
    assert ip != ""
    parts = ip.split(".")
    assert len(parts) == 4


# ── Gossip integration test ──

@pytest.mark.asyncio
async def test_gossip_mesh_start_stop():
    """Verify gossip mesh can start and stop cleanly."""
    identity = NodeIdentity.__new__(NodeIdentity)
    identity._path = None
    identity._data = {"self_id": "test-node", "registered_nodes": [{"id": "test-node", "status": "alive"}]}

    state = NodeState(node_id="test-node", hostname="test", port=19420)
    mesh = GossipMesh(identity, state, listen_port=19420)

    await mesh.start()
    await asyncio.sleep(0.5)
    assert mesh._running
    await mesh.stop()
    assert not mesh._running
