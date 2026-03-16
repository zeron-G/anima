"""Tests for cross-node task delegation protocol."""

import asyncio
import time
import pytest

from anima.network.session_router import (
    TaskDelegate, DelegatedTask, TaskStatus,
)


# ── DelegatedTask dataclass ──────────────────────────────────────────────────

def test_task_to_from_dict():
    task = DelegatedTask(
        task_type="llm_inference",
        payload={"prompt": "hello"},
        source_node="node-a",
        target_node="node-b",
    )
    d = task.to_dict()
    task2 = DelegatedTask.from_dict(d)
    assert task2.task_id == task.task_id
    assert task2.task_type == "llm_inference"
    assert task2.payload == {"prompt": "hello"}
    assert task2.source_node == "node-a"
    assert task2.target_node == "node-b"
    assert task2.status == TaskStatus.PENDING


# ── TaskDelegate: delegation ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delegate_creates_outbound_task():
    broadcasts = []

    async def fake_broadcast(msg):
        broadcasts.append(msg)

    td = TaskDelegate("node-a", fake_broadcast)
    task_id = await td.delegate("shell_exec", {"cmd": "ls"}, target_node="node-b")

    assert task_id in td._outbound
    task = td._outbound[task_id]
    assert task.task_type == "shell_exec"
    assert task.target_node == "node-b"
    assert task.status == TaskStatus.PENDING

    # Should have broadcast a task_delegate message
    assert len(broadcasts) == 1
    assert broadcasts[0]["type"] == "task_delegate"
    assert broadcasts[0]["task"]["task_id"] == task_id


@pytest.mark.asyncio
async def test_delegate_without_broadcast_fn():
    """Delegation works even without a broadcast function."""
    td = TaskDelegate("node-a")
    task_id = await td.delegate("tool_call", {"tool": "search"}, target_node="node-b")
    assert task_id in td._outbound


# ── TaskDelegate: handler registration ──────────────────────────────────────

def test_register_handler():
    td = TaskDelegate("node-b")

    async def my_handler(task):
        return {"answer": 42}

    td.register_handler("my_task", my_handler)
    assert "my_task" in td._handlers


# ── TaskDelegate: inbound task execution ────────────────────────────────────

@pytest.mark.asyncio
async def test_incoming_task_executed_and_result_sent():
    results_sent = []

    async def fake_broadcast(msg):
        results_sent.append(msg)

    td = TaskDelegate("node-b", fake_broadcast)

    async def echo_handler(task: DelegatedTask) -> dict:
        return {"echo": task.payload.get("value")}

    td.register_handler("echo", echo_handler)

    task = DelegatedTask(
        task_type="echo",
        payload={"value": "pong"},
        source_node="node-a",
        target_node="node-b",
    )
    await td.handle_incoming_task(task.to_dict())
    await asyncio.sleep(0.05)  # let the execution coroutine finish

    assert task.task_id in td._inbound
    inbound = td._inbound[task.task_id]
    assert inbound.status == TaskStatus.DONE
    assert inbound.result == {"echo": "pong"}

    # Should have sent at least: accepted + done
    types_sent = [m.get("type") for m in results_sent]
    assert "task_result" in types_sent

    # Final result message should carry DONE status
    done_msgs = [m for m in results_sent if m.get("status") == TaskStatus.DONE]
    assert len(done_msgs) == 1
    assert done_msgs[0]["result"] == {"echo": "pong"}


@pytest.mark.asyncio
async def test_incoming_task_ignored_if_wrong_target():
    td = TaskDelegate("node-b")
    task = DelegatedTask(
        task_type="echo",
        payload={},
        source_node="node-a",
        target_node="node-c",   # NOT node-b
    )
    await td.handle_incoming_task(task.to_dict())
    assert task.task_id not in td._inbound


@pytest.mark.asyncio
async def test_duplicate_task_ignored():
    td = TaskDelegate("node-b")

    async def noop(task):
        return {}

    td.register_handler("noop", noop)
    task = DelegatedTask(task_type="noop", source_node="node-a", target_node="node-b")
    await td.handle_incoming_task(task.to_dict())
    await td.handle_incoming_task(task.to_dict())  # duplicate
    await asyncio.sleep(0.05)
    # Still only one entry
    assert len(td._inbound) == 1


@pytest.mark.asyncio
async def test_unknown_task_type_fails():
    results_sent = []

    async def fake_broadcast(msg):
        results_sent.append(msg)

    td = TaskDelegate("node-b", fake_broadcast)
    task = DelegatedTask(task_type="unknown_type", source_node="node-a", target_node="node-b")
    await td.handle_incoming_task(task.to_dict())
    await asyncio.sleep(0.05)

    inbound = td._inbound[task.task_id]
    assert inbound.status == TaskStatus.FAILED
    assert "No handler" in inbound.error

    failed_msgs = [m for m in results_sent if m.get("status") == TaskStatus.FAILED]
    assert len(failed_msgs) == 1


# ── TaskDelegate: result reception ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_task_result_updates_outbound():
    td = TaskDelegate("node-a")
    task_id = await td.delegate("tool_call", {}, target_node="node-b")

    td.handle_task_result({
        "task_id": task_id,
        "status": TaskStatus.DONE,
        "result": {"output": "success"},
        "error": "",
        "completed_at": time.time(),
    })

    task = td._outbound[task_id]
    assert task.status == TaskStatus.DONE
    assert task.result == {"output": "success"}


@pytest.mark.asyncio
async def test_handle_task_result_unknown_task_ignored():
    td = TaskDelegate("node-a")
    # Should not raise
    td.handle_task_result({
        "task_id": "nonexistent-task",
        "status": TaskStatus.DONE,
        "result": {},
        "error": "",
    })


@pytest.mark.asyncio
async def test_wait_result_resolved_by_handle_task_result():
    broadcasts = []

    async def fake_broadcast(msg):
        broadcasts.append(msg)

    td = TaskDelegate("node-a", fake_broadcast)
    task_id = await td.delegate("tool_call", {}, target_node="node-b")

    async def deliver_result():
        await asyncio.sleep(0.05)
        td.handle_task_result({
            "task_id": task_id,
            "status": TaskStatus.DONE,
            "result": {"answer": 99},
            "error": "",
            "completed_at": time.time(),
        })

    asyncio.ensure_future(deliver_result())
    result = await td.wait_result(task_id, timeout=2.0)
    assert result == {"answer": 99}


@pytest.mark.asyncio
async def test_wait_result_timeout():
    td = TaskDelegate("node-a")
    task_id = await td.delegate("slow_task", {}, target_node="node-b", timeout=5.0)

    with pytest.raises(TimeoutError):
        await td.wait_result(task_id, timeout=0.1)


# ── TaskDelegate: housekeeping ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cleanup_expired_removes_old_done_tasks():
    td = TaskDelegate("node-a")
    task_id = await td.delegate("tool_call", {}, target_node="node-b")

    # Mark as done with an old timestamp
    task = td._outbound[task_id]
    task.status = TaskStatus.DONE
    task.completed_at = time.time() - TaskDelegate.TASK_TTL - 1

    removed = td.cleanup_expired()
    assert removed == 1
    assert task_id not in td._outbound


@pytest.mark.asyncio
async def test_cleanup_does_not_remove_pending_tasks():
    td = TaskDelegate("node-a")
    task_id = await td.delegate("tool_call", {}, target_node="node-b")

    removed = td.cleanup_expired()
    assert removed == 0
    assert task_id in td._outbound


# ── TaskDelegate: list helpers ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_outbound():
    td = TaskDelegate("node-a")
    await td.delegate("task-1", {}, target_node="node-b")
    await td.delegate("task-2", {}, target_node="node-c")
    lst = td.list_outbound()
    assert len(lst) == 2
    types = {t["task_type"] for t in lst}
    assert types == {"task-1", "task-2"}


@pytest.mark.asyncio
async def test_get_task():
    td = TaskDelegate("node-a")
    task_id = await td.delegate("tool_call", {}, target_node="node-b")
    task = td.get_task(task_id)
    assert task is not None
    assert task.task_id == task_id
    assert td.get_task("nonexistent") is None


# ── TaskDelegate: set_loop / cross-thread future resolution ─────────────────

@pytest.mark.asyncio
async def test_set_loop_resolves_future_from_thread():
    """handle_task_result called from a background thread resolves wait_result."""
    import threading

    td = TaskDelegate("node-a")
    loop = asyncio.get_event_loop()
    td.set_loop(loop)

    task_id = await td.delegate("tool_call", {}, target_node="node-b")

    def _deliver():
        time.sleep(0.05)
        td.handle_task_result({
            "task_id": task_id,
            "status": TaskStatus.DONE,
            "result": {"value": "from-thread"},
            "error": "",
            "completed_at": time.time(),
        })

    t = threading.Thread(target=_deliver, daemon=True)
    t.start()

    result = await td.wait_result(task_id, timeout=2.0)
    assert result == {"value": "from-thread"}
    t.join()


@pytest.mark.asyncio
async def test_set_loop_raises_on_failure_from_thread():
    """handle_task_result with FAILED status raises RuntimeError in wait_result."""
    import threading

    td = TaskDelegate("node-a")
    td.set_loop(asyncio.get_event_loop())
    task_id = await td.delegate("tool_call", {}, target_node="node-b")

    def _deliver():
        time.sleep(0.05)
        td.handle_task_result({
            "task_id": task_id,
            "status": TaskStatus.FAILED,
            "result": {},
            "error": "something went wrong",
            "completed_at": time.time(),
        })

    threading.Thread(target=_deliver, daemon=True).start()

    with pytest.raises(RuntimeError, match="something went wrong"):
        await td.wait_result(task_id, timeout=2.0)


# ── TaskDelegate: broadcast message format ───────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_contains_type_key():
    """Delegated task broadcast payload must carry 'type': 'task_delegate'."""
    broadcasts = []

    async def fake_broadcast(msg):
        broadcasts.append(msg)

    td = TaskDelegate("node-a", fake_broadcast)
    await td.delegate("ping", {}, target_node="node-b")

    assert broadcasts[0]["type"] == "task_delegate"
    assert "task" in broadcasts[0]


@pytest.mark.asyncio
async def test_result_broadcast_contains_type_key():
    """_send_result must emit 'type': 'task_result' in the broadcast payload."""
    broadcasts = []

    async def fake_broadcast(msg):
        broadcasts.append(msg)

    td = TaskDelegate("node-b", fake_broadcast)

    async def noop_handler(task):
        return {"ok": True}

    td.register_handler("ping", noop_handler)
    task = DelegatedTask(task_type="ping", source_node="node-a", target_node="node-b")
    await td.handle_incoming_task(task.to_dict())
    await asyncio.sleep(0.05)

    types = [m.get("type") for m in broadcasts]
    assert "task_result" in types


# ── TaskDelegate: wildcard target ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_incoming_task_accepted_with_empty_target():
    """A task with no target_node (broadcast) should be accepted by any node."""
    td = TaskDelegate("node-b")

    async def noop(task):
        return {}

    td.register_handler("noop", noop)
    task = DelegatedTask(task_type="noop", source_node="node-a", target_node="")
    await td.handle_incoming_task(task.to_dict())
    await asyncio.sleep(0.05)
    assert task.task_id in td._inbound


# ── TaskDelegate: list_inbound ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_inbound():
    """list_inbound returns serialised inbound tasks."""
    td = TaskDelegate("node-b")

    async def noop(task):
        return {}

    td.register_handler("noop", noop)

    for _ in range(3):
        t = DelegatedTask(task_type="noop", source_node="node-a", target_node="node-b")
        await td.handle_incoming_task(t.to_dict())

    await asyncio.sleep(0.05)
    lst = td.list_inbound()
    assert len(lst) == 3
    assert all(isinstance(item, dict) for item in lst)


# ── GossipMesh: attach_task_delegate wiring ──────────────────────────────────

def test_attach_task_delegate_sets_callbacks():
    """attach_task_delegate wires gossip callbacks and delegate broadcast."""
    from unittest.mock import MagicMock
    from anima.network.gossip import GossipMesh
    from anima.network.node import NodeIdentity, NodeState

    identity = MagicMock(spec=NodeIdentity)
    identity.node_id = "node-a"
    state = MagicMock(spec=NodeState)

    mesh = GossipMesh(identity, state, listen_port=19420)
    td = TaskDelegate("node-a")

    mesh.attach_task_delegate(td)

    # Gossip callbacks should now be set
    assert mesh._on_task_delegate is not None
    assert mesh._on_task_result is not None
    # Delegate broadcast should be set
    assert td._broadcast_fn is not None


@pytest.mark.asyncio
async def test_attach_task_delegate_broadcast_routes_type():
    """The broadcast shim created by attach_task_delegate routes the type correctly."""
    from unittest.mock import MagicMock
    from anima.network.gossip import GossipMesh
    from anima.network.node import NodeIdentity, NodeState

    identity = MagicMock(spec=NodeIdentity)
    identity.node_id = "node-a"
    state = MagicMock(spec=NodeState)

    mesh = GossipMesh(identity, state, listen_port=19421)
    td = TaskDelegate("node-a")
    mesh.attach_task_delegate(td)

    # Manually queue a task_delegate via the broadcast shim
    await td._broadcast_fn({"type": "task_delegate", "task": {"task_id": "t1"}})

    with mesh._lock:
        queued = list(mesh._outbound_task_msgs)

    assert len(queued) == 1
    assert queued[0]["_msg_type"] == "task_delegate"
    assert queued[0]["task"]["task_id"] == "t1"


# ── TaskDelegate: priority & max_retries fields ──────────────────────────────

@pytest.mark.asyncio
async def test_delegate_with_priority_and_retries():
    """priority and max_retries are stored on the DelegatedTask and broadcast."""
    broadcasts = []

    async def fake_broadcast(msg):
        broadcasts.append(msg)

    td = TaskDelegate("node-a", fake_broadcast)
    task_id = await td.delegate(
        "compute", {"x": 1}, target_node="node-b",
        priority=5, max_retries=3,
    )
    task = td._outbound[task_id]
    assert task.priority == 5
    assert task.max_retries == 3

    sent_task = broadcasts[0]["task"]
    assert sent_task["priority"] == 5
    assert sent_task["max_retries"] == 3


def test_task_to_from_dict_includes_priority_and_retries():
    task = DelegatedTask(
        task_type="compute",
        payload={},
        source_node="node-a",
        target_node="node-b",
        priority=3,
        max_retries=2,
        retry_count=1,
    )
    d = task.to_dict()
    assert d["priority"] == 3
    assert d["max_retries"] == 2
    assert d["retry_count"] == 1

    task2 = DelegatedTask.from_dict(d)
    assert task2.priority == 3
    assert task2.max_retries == 2
    assert task2.retry_count == 1


# ── TaskDelegate: retry on failure ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_retried_on_failure():
    """A task with max_retries=2 should be attempted 3 times total."""
    broadcasts = []
    call_count = 0

    async def fake_broadcast(msg):
        broadcasts.append(msg)

    td = TaskDelegate("node-b", fake_broadcast)

    async def flaky_handler(task: DelegatedTask) -> dict:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("transient error")
        return {"ok": True}

    td.register_handler("flaky", flaky_handler)

    task = DelegatedTask(
        task_type="flaky",
        payload={},
        source_node="node-a",
        target_node="node-b",
        max_retries=2,
    )
    await td.handle_incoming_task(task.to_dict())
    await asyncio.sleep(0.1)

    inbound = td._inbound[task.task_id]
    assert inbound.status == TaskStatus.DONE
    assert inbound.retry_count == 2
    assert call_count == 3


@pytest.mark.asyncio
async def test_task_fails_after_max_retries_exhausted():
    """If all retries fail the task ends with FAILED status."""
    broadcasts = []

    async def fake_broadcast(msg):
        broadcasts.append(msg)

    td = TaskDelegate("node-b", fake_broadcast)

    async def always_fails(task: DelegatedTask) -> dict:
        raise RuntimeError("always bad")

    td.register_handler("bad", always_fails)

    task = DelegatedTask(
        task_type="bad",
        payload={},
        source_node="node-a",
        target_node="node-b",
        max_retries=1,
    )
    await td.handle_incoming_task(task.to_dict())
    await asyncio.sleep(0.1)

    inbound = td._inbound[task.task_id]
    assert inbound.status == TaskStatus.FAILED
    assert inbound.retry_count == 1   # used all retries

    failed_msgs = [m for m in broadcasts if m.get("status") == TaskStatus.FAILED]
    assert len(failed_msgs) == 1


# ── TaskDelegate: cancellation ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_outbound_task():
    """cancel() marks the task CANCELLED and broadcasts task_cancel."""
    broadcasts = []

    async def fake_broadcast(msg):
        broadcasts.append(msg)

    td = TaskDelegate("node-a", fake_broadcast)
    task_id = await td.delegate("slow", {}, target_node="node-b")

    result = await td.cancel(task_id)
    assert result is True
    assert td._outbound[task_id].status == TaskStatus.CANCELLED

    cancel_msgs = [m for m in broadcasts if m.get("type") == "task_cancel"]
    assert len(cancel_msgs) == 1
    assert cancel_msgs[0]["task_id"] == task_id


@pytest.mark.asyncio
async def test_cancel_already_done_task_returns_false():
    td = TaskDelegate("node-a")
    task_id = await td.delegate("done_task", {}, target_node="node-b")
    td._outbound[task_id].status = TaskStatus.DONE

    result = await td.cancel(task_id)
    assert result is False


@pytest.mark.asyncio
async def test_cancel_nonexistent_task_returns_false():
    td = TaskDelegate("node-a")
    result = await td.cancel("no-such-task")
    assert result is False


@pytest.mark.asyncio
async def test_wait_result_cancelled_raises():
    """wait_result raises RuntimeError when cancel() is called concurrently."""
    td = TaskDelegate("node-a")
    td.set_loop(asyncio.get_event_loop())
    task_id = await td.delegate("slow", {}, target_node="node-b")

    async def do_cancel():
        await asyncio.sleep(0.05)
        await td.cancel(task_id)

    asyncio.ensure_future(do_cancel())
    with pytest.raises(RuntimeError, match="cancelled"):
        await td.wait_result(task_id, timeout=2.0)


@pytest.mark.asyncio
async def test_handle_cancel_inbound_marks_cancelled():
    """handle_cancel marks an inbound task as CANCELLED."""
    broadcasts = []

    async def fake_broadcast(msg):
        broadcasts.append(msg)

    td = TaskDelegate("node-b", fake_broadcast)

    async def slow_handler(task: DelegatedTask) -> dict:
        await asyncio.sleep(10)  # will not finish in test
        return {}

    td.register_handler("slow", slow_handler)

    task = DelegatedTask(
        task_type="slow",
        payload={},
        source_node="node-a",
        target_node="node-b",
    )
    await td.handle_incoming_task(task.to_dict())
    # Cancel before execution completes
    await td.handle_cancel(task.task_id)

    inbound = td._inbound[task.task_id]
    assert inbound.status == TaskStatus.CANCELLED

    cancel_result_msgs = [m for m in broadcasts if m.get("status") == TaskStatus.CANCELLED]
    assert len(cancel_result_msgs) == 1


@pytest.mark.asyncio
async def test_handle_cancel_unknown_task_is_noop():
    """handle_cancel for an unknown task_id should not raise."""
    td = TaskDelegate("node-b")
    await td.handle_cancel("nonexistent-task-id")  # must not raise


# ── GossipMesh: task_cancel wiring ──────────────────────────────────────────

def test_attach_task_delegate_sets_cancel_callback():
    """attach_task_delegate also wires _on_task_cancel."""
    from unittest.mock import MagicMock
    from anima.network.gossip import GossipMesh
    from anima.network.node import NodeIdentity, NodeState

    identity = MagicMock(spec=NodeIdentity)
    identity.node_id = "node-a"
    state = MagicMock(spec=NodeState)

    mesh = GossipMesh(identity, state, listen_port=19422)
    td = TaskDelegate("node-a")
    mesh.attach_task_delegate(td)

    assert mesh._on_task_cancel is not None


@pytest.mark.asyncio
async def test_cancel_broadcast_routed_via_gossip():
    """cancel() sends a task_cancel message that ends up in gossip outbound queue."""
    from unittest.mock import MagicMock
    from anima.network.gossip import GossipMesh
    from anima.network.node import NodeIdentity, NodeState

    identity = MagicMock(spec=NodeIdentity)
    identity.node_id = "node-a"
    state = MagicMock(spec=NodeState)

    mesh = GossipMesh(identity, state, listen_port=19423)
    td = TaskDelegate("node-a")
    mesh.attach_task_delegate(td)

    task_id = await td.delegate("slow", {}, target_node="node-b")
    await td.cancel(task_id)

    with mesh._lock:
        queued = list(mesh._outbound_task_msgs)

    # Should have: task_delegate + task_cancel
    msg_types = [m.get("_msg_type") for m in queued]
    assert "task_delegate" in msg_types
    assert "task_cancel" in msg_types
