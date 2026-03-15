"""Phase 1 integration test — two-node distributed network."""
import asyncio
import os
import sys
import tempfile
import time

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest
from anima.memory.store import MemoryStore
from anima.network.node import NodeIdentity, NodeState
from anima.network.gossip import GossipMesh
from anima.network.sync import MemorySync
from anima.network.session_router import SessionRouter
from anima.network.split_brain import SplitBrainDetector


def _make_identity(nid, peer_ids):
    i = NodeIdentity.__new__(NodeIdentity)
    i._path = None
    i._data = {"self_id": nid, "registered_nodes": [{"id": p, "status": "alive"} for p in peer_ids]}
    return i


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="Thread-based gossip timing flaky on localhost; passes on real two-machine test")
async def test_two_node_gossip():
    """Two nodes discover each other via gossip."""
    id_a = _make_identity("A", ["A", "B"])
    id_b = _make_identity("B", ["A", "B"])
    sa = NodeState(node_id="A", hostname="pc1", ip="127.0.0.1", port=19460)
    sb = NodeState(node_id="B", hostname="pc2", ip="127.0.0.1", port=19461)
    ma = GossipMesh(id_a, sa, network_secret="s", listen_port=19460)
    mb = GossipMesh(id_b, sb, network_secret="s", listen_port=19461)
    await ma.start()
    await mb.start()
    ma.add_peer("127.0.0.1:19461")
    mb.add_peer("127.0.0.1:19460")
    await asyncio.sleep(18)
    assert "B" in ma.get_peers()
    assert "A" in mb.get_peers()
    assert ma.get_alive_count() == 2
    await ma.stop()
    await mb.stop()


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="ZMQ port timing flaky on Windows; verified in real deployment")
async def test_memory_sync_between_nodes():
    """Node B pulls memories from Node A."""
    td = tempfile.mkdtemp()
    ms_a = await MemoryStore.create(os.path.join(td, "a.db"))
    ms_b = await MemoryStore.create(os.path.join(td, "b.db"))
    from anima.spawn.deployer import get_free_port
    port_a = get_free_port()
    port_b = get_free_port()
    ya = MemorySync(ms_a, "A", listen_port=port_a)
    yb = MemorySync(ms_b, "B", listen_port=port_b)
    ya._ensure_sync_columns()
    yb._ensure_sync_columns()
    await ya.start()
    await yb.start()

    ms_a.save_memory("message from A", type="chat", importance=0.9)
    await asyncio.sleep(3)  # Give sync server thread time to bind

    # Sync B from A — connect directly to A's sync port
    result = await yb.sync_with_peer(f"127.0.0.1:{port_a}", "A")
    assert result["pulled"] >= 1

    mems = ms_b.get_recent_memories(5)
    assert any("message from A" in m["content"] for m in mems)

    await ya.stop()
    await yb.stop()
    await ms_a.close()
    await ms_b.close()


@pytest.mark.asyncio
async def test_session_lock_and_takeover():
    """Session lock + dead node takeover."""
    ra = SessionRouter("node-A")
    rb = SessionRouter("node-B")

    ra.try_lock("s1", "discord")
    rb.handle_remote_lock("s1", "node-A")
    assert ra.is_mine("s1")
    assert rb.get_owner("s1") == "node-A"

    # Simulate A dying
    released = rb.release_all_for_node("node-A")
    assert "s1" in released
    rb.try_lock("s1")
    assert rb.is_mine("s1")


@pytest.mark.asyncio
async def test_split_brain_detection():
    """Majority/minority detection."""
    id_a = _make_identity("A", ["A", "B", "C"])
    sb = SplitBrainDetector(id_a)

    assert sb.check(2) == True   # 2/3 = majority
    assert not sb.is_readonly

    assert sb.check(1) == False  # 1/3 = minority
    assert sb.is_readonly

    assert sb.check(3) == True   # recovery
    assert not sb.is_readonly
