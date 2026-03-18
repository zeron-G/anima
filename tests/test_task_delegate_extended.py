"""Extended tests for task delegation protocol enhancements:
- Priority queue ordering
- Concurrency limiting (max_concurrent)
- task_status_query / task_status_reply protocol
- queue_stats helper
- GossipMesh wiring for new message types
"""

import asyncio
import time
import pytest

from anima.network.session_router import (
    TaskDelegate, DelegatedTask, TaskStatus,
)


@pytest.fixture(autouse=True)
async def _cleanup_delegates():
    """Cancel all TaskDelegate workers after each test to prevent memory leaks."""
    delegates: list[TaskDelegate] = []
    _orig_init = TaskDelegate._ensure_workers

    def _tracking_init(self):
        delegates.append(self)
        return _orig_init(self)

    TaskDelegate._ensure_workers = _tracking_init
    yield
    TaskDelegate._ensure_workers = _orig_init
    for td in delegates:
        for w in td._workers:
            w.cancel()
        td._workers.clear()
    await asyncio.sleep(0.01)


# ── Priority queue ordering ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_priority_queue_enqueues_tasks():
    """Tasks are added to the internal priority queue on acceptance."""
    td = TaskDelegate("node-b", max_concurrent=10)

    async def noop(task):
        return {}

    td.register_handler("noop", noop)

    for priority in (0, 5, 1):
        t = DelegatedTask(
            task_type="noop",
            source_node="node-a",
            target_node="node-b",
            priority=priority,
        )
        await td.handle_incoming_task(t.to_dict())

    # Three tasks should be enqueued
    assert len(td._task_queue) == 3


@pytest.mark.asyncio
async def test_priority_queue_higher_priority_runs_first():
    """Higher-priority tasks should be dispatched before lower-priority ones
    when they arrive at the same time (ordering by heap value)."""
    execution_order = []

    async def recording_handler(task: DelegatedTask) -> dict:
        execution_order.append(task.priority)
        return {}

    # Use max_concurrent=1 so tasks execute strictly one at a time
    td = TaskDelegate("node-b", max_concurrent=1)
    td.register_handler("ordered", recording_handler)

    # Submit tasks with different priorities
    for priority in (1, 10, 3):
        t = DelegatedTask(
            task_type="ordered",
            source_node="node-a",
            target_node="node-b",
            priority=priority,
        )
        await td.handle_incoming_task(t.to_dict())

    await asyncio.sleep(0.15)

    # All tasks should have completed
    assert len(execution_order) == 3
    # Highest priority (10) should have run first
    assert execution_order[0] == 10


# ── Concurrency limiting ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_max_concurrent_limits_parallel_execution():
    """No more than max_concurrent tasks run simultaneously."""
    max_concurrent = 2
    running_at_same_time = []
    current_running = [0]

    async def slow_handler(task: DelegatedTask) -> dict:
        current_running[0] += 1
        running_at_same_time.append(current_running[0])
        await asyncio.sleep(0.05)
        current_running[0] -= 1
        return {}

    td = TaskDelegate("node-b", max_concurrent=max_concurrent)
    td.register_handler("slow", slow_handler)

    # Submit 4 tasks — only 2 should run at a time
    for _ in range(4):
        t = DelegatedTask(task_type="slow", source_node="node-a", target_node="node-b")
        await td.handle_incoming_task(t.to_dict())

    await asyncio.sleep(0.4)

    # Peak concurrency should never exceed max_concurrent
    assert max(running_at_same_time) <= max_concurrent


@pytest.mark.asyncio
async def test_dispatch_queue_lazily_created():
    """The dispatch queue and workers are None/empty before the first task."""
    td = TaskDelegate("node-b", max_concurrent=3)
    assert td._dispatch_queue is None
    assert td._workers == []

    async def noop(task):
        return {}

    td.register_handler("noop", noop)
    t = DelegatedTask(task_type="noop", source_node="node-a", target_node="node-b")
    await td.handle_incoming_task(t.to_dict())
    await asyncio.sleep(0.05)

    # Dispatch queue and workers should now exist
    assert td._dispatch_queue is not None
    assert len(td._workers) == 3


# ── queue_stats ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_queue_stats_initial():
    """queue_stats returns correct zero counts on an empty delegate."""
    td = TaskDelegate("node-x", max_concurrent=4)
    stats = td.queue_stats()
    assert stats["node_id"] == "node-x"
    assert stats["queued"] == 0
    assert stats["running"] == 0
    assert stats["max_concurrent"] == 4
    assert stats["total_inbound"] == 0
    assert stats["total_outbound"] == 0


@pytest.mark.asyncio
async def test_queue_stats_after_delegation():
    """queue_stats reflects outbound tasks."""
    td = TaskDelegate("node-a")
    await td.delegate("tool_call", {}, target_node="node-b")
    await td.delegate("tool_call", {}, target_node="node-b")
    stats = td.queue_stats()
    assert stats["total_outbound"] == 2


@pytest.mark.asyncio
async def test_queue_stats_running():
    """queue_stats shows running count while a task is executing."""
    running_event = asyncio.Event()
    finish_event = asyncio.Event()

    async def blocking_handler(task: DelegatedTask) -> dict:
        running_event.set()
        await finish_event.wait()
        return {}

    td = TaskDelegate("node-b", max_concurrent=5)
    td.register_handler("blocking", blocking_handler)

    t = DelegatedTask(task_type="blocking", source_node="node-a", target_node="node-b")
    await td.handle_incoming_task(t.to_dict())

    # Wait until the handler is actually running
    await asyncio.wait_for(running_event.wait(), timeout=1.0)

    stats = td.queue_stats()
    assert stats["running"] == 1

    # Release the handler
    finish_event.set()
    await asyncio.sleep(0.05)

    stats = td.queue_stats()
    assert stats["running"] == 0


# ── task_status_query / task_status_reply ────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_status_query_known_task():
    """handle_status_query sends back a task_status_reply with correct status."""
    replies = []

    async def fake_broadcast(msg):
        replies.append(msg)

    td = TaskDelegate("node-b", fake_broadcast)

    # Manually insert a completed inbound task
    task = DelegatedTask(
        task_type="echo",
        payload={},
        source_node="node-a",
        target_node="node-b",
        status=TaskStatus.DONE,
    )
    task.result = {"value": 42}
    task.completed_at = time.time()
    td._inbound[task.task_id] = task

    await td.handle_status_query({
        "task_id": task.task_id,
        "source_node": "node-a",
        "target_node": "node-b",
        "correlation_id": "corr-1",
    })

    assert len(replies) == 1
    reply = replies[0]
    assert reply["type"] == "task_status_reply"
    assert reply["task_id"] == task.task_id
    assert reply["status"] == TaskStatus.DONE
    assert reply["result"] == {"value": 42}
    assert reply["correlation_id"] == "corr-1"


@pytest.mark.asyncio
async def test_handle_status_query_unknown_task():
    """handle_status_query replies with status='unknown' for missing tasks."""
    replies = []

    async def fake_broadcast(msg):
        replies.append(msg)

    td = TaskDelegate("node-b", fake_broadcast)

    await td.handle_status_query({
        "task_id": "no-such-task",
        "source_node": "node-a",
        "target_node": "node-b",
        "correlation_id": "corr-2",
    })

    assert len(replies) == 1
    assert replies[0]["status"] == "unknown"
    assert replies[0]["correlation_id"] == "corr-2"


@pytest.mark.asyncio
async def test_handle_status_reply_resolves_future():
    """handle_status_reply resolves the Future created by query_task_status."""
    replies_sent = []

    async def fake_broadcast(msg):
        replies_sent.append(msg)
        # Simulate the remote node immediately replying
        if msg.get("type") == "task_status_query":
            td.handle_status_reply({
                "type": "task_status_reply",
                "task_id": msg["task_id"],
                "status": TaskStatus.RUNNING,
                "result": {},
                "error": "",
                "completed_at": 0.0,
                "correlation_id": msg["correlation_id"],
                "target_node": "node-a",
                "source_node": "node-b",
            })

    td = TaskDelegate("node-a", fake_broadcast)
    td.set_loop(asyncio.get_event_loop())

    result = await td.query_task_status("some-task-id", "node-b", timeout=2.0)
    assert result["status"] == TaskStatus.RUNNING
    assert result["task_id"] == "some-task-id"


@pytest.mark.asyncio
async def test_query_task_status_timeout():
    """query_task_status raises TimeoutError if no reply arrives."""
    async def fake_broadcast(msg):
        pass  # Never reply

    td = TaskDelegate("node-a", fake_broadcast)
    td.set_loop(asyncio.get_event_loop())

    with pytest.raises(TimeoutError):
        await td.query_task_status("ghost-task", "node-b", timeout=0.1)


@pytest.mark.asyncio
async def test_handle_status_reply_ignores_wrong_target():
    """handle_status_reply ignores replies not addressed to this node."""
    td = TaskDelegate("node-a")
    td.set_loop(asyncio.get_event_loop())

    # Manufacture a fake pending query future
    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    td._status_query_futures["corr-x"] = fut

    # Reply addressed to a different node — should be ignored
    td.handle_status_reply({
        "correlation_id": "corr-x",
        "target_node": "node-c",  # not node-a
        "task_id": "t1",
        "status": TaskStatus.DONE,
    })

    assert not fut.done()
    fut.cancel()  # clean up


# ── GossipMesh: new callback wiring ─────────────────────────────────────────

def test_attach_task_delegate_sets_status_callbacks():
    """attach_task_delegate wires _on_task_status_query and _on_task_status_reply."""
    from unittest.mock import MagicMock
    from anima.network.gossip import GossipMesh
    from anima.network.node import NodeIdentity, NodeState

    identity = MagicMock(spec=NodeIdentity)
    identity.node_id = "node-a"
    state = MagicMock(spec=NodeState)

    mesh = GossipMesh(identity, state, listen_port=19430)
    td = TaskDelegate("node-a")
    mesh.attach_task_delegate(td)

    assert mesh._on_task_status_query is not None
    assert mesh._on_task_status_reply is not None


@pytest.mark.asyncio
async def test_status_query_broadcast_routed_via_gossip():
    """query_task_status enqueues a task_status_query in the gossip outbound queue."""
    from unittest.mock import MagicMock
    from anima.network.gossip import GossipMesh
    from anima.network.node import NodeIdentity, NodeState

    identity = MagicMock(spec=NodeIdentity)
    identity.node_id = "node-a"
    state = MagicMock(spec=NodeState)

    mesh = GossipMesh(identity, state, listen_port=19431)
    td = TaskDelegate("node-a")
    mesh.attach_task_delegate(td)
    td.set_loop(asyncio.get_event_loop())

    # Start query but don't await the result (it will timeout — we just want
    # to check the outbound queue was populated).
    query_coro = td.query_task_status("task-xyz", "node-b", timeout=0.05)
    query_task = asyncio.ensure_future(query_coro)

    await asyncio.sleep(0.02)  # let the broadcast fire

    with mesh._lock:
        queued = list(mesh._outbound_task_msgs)

    msg_types = [m.get("_msg_type") for m in queued]
    assert "task_status_query" in msg_types

    # Clean up the timing-out task
    try:
        await query_task
    except TimeoutError:
        pass


# ── cleanup_expired prunes priority queue ────────────────────────────────────

@pytest.mark.asyncio
async def test_cleanup_expired_prunes_priority_queue():
    """cleanup_expired removes stale entries from the priority queue."""
    td = TaskDelegate("node-b")

    async def noop(task):
        return {}

    td.register_handler("noop", noop)

    task = DelegatedTask(task_type="noop", source_node="node-a", target_node="node-b")
    await td.handle_incoming_task(task.to_dict())
    await asyncio.sleep(0.05)

    # Force-expire the task
    inbound = td._inbound[task.task_id]
    inbound.status = TaskStatus.DONE
    inbound.completed_at = time.time() - TaskDelegate.TASK_TTL - 1

    assert len(td._task_queue) >= 1
    td.cleanup_expired()
    # Queue entry for the removed task should be gone
    remaining_ids = {entry[2] for entry in td._task_queue}
    assert task.task_id not in remaining_ids
