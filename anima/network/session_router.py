"""Distributed session routing with locking and cross-node task delegation."""

import asyncio
import heapq
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from anima.utils.ids import gen_id
from anima.utils.logging import get_logger

log = get_logger("network.session")


class NetworkTimeoutError(TimeoutError):
    """Raised when no task heartbeat is received within the expected interval,
    indicating a network disconnect rather than a slow task."""
    pass


# ── Task Delegation ──────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING   = "pending"
    ACCEPTED  = "accepted"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"
    TIMEOUT   = "timeout"
    CANCELLED = "cancelled"


@dataclass
class DelegatedTask:
    """A task delegated from one node to another."""
    task_id: str = field(default_factory=lambda: gen_id("task"))
    task_type: str = ""          # e.g. "llm_inference", "shell_exec", "tool_call"
    payload: dict = field(default_factory=dict)
    source_node: str = ""        # node that created the task
    target_node: str = ""        # node that should execute the task
    status: str = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    accepted_at: float = 0.0
    completed_at: float = 0.0
    result: dict = field(default_factory=dict)
    error: str = ""
    timeout: float = 60.0        # seconds before the task is considered timed out
    priority: int = 0            # higher = more urgent (0 = normal)
    max_retries: int = 0         # number of automatic retries on failure
    retry_count: int = 0         # current retry attempt

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "payload": self.payload,
            "source_node": self.source_node,
            "target_node": self.target_node,
            "status": self.status,
            "created_at": self.created_at,
            "accepted_at": self.accepted_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
            "timeout": self.timeout,
            "priority": self.priority,
            "max_retries": self.max_retries,
            "retry_count": self.retry_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DelegatedTask":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class TaskDelegate:
    """
    Cross-node task queue manager.

    Outbound tasks: tasks this node delegated to remote nodes.
    Inbound tasks:  tasks remote nodes delegated to this node.

    Usage:
        delegate = TaskDelegate(local_node_id, broadcast_fn)

        # Delegate a task to a remote node
        task_id = await delegate.delegate(task_type="llm_inference",
                                          payload={"prompt": "..."},
                                          target_node="node-b")

        # Wait for result
        result = await delegate.wait_result(task_id, timeout=30)

        # On the receiving node — register a handler
        delegate.register_handler("llm_inference", my_handler_coroutine)
    """

    TASK_TTL = 300.0        # seconds to keep completed tasks in memory
    HEARTBEAT_INTERVAL = 15.0  # executor sends task_heartbeat every N seconds

    def __init__(
        self,
        local_node_id: str,
        broadcast_fn: Optional[Callable] = None,
        max_concurrent: int = 5,
    ):
        self._local_node_id = local_node_id
        self._broadcast_fn = broadcast_fn
        self._max_concurrent = max_concurrent

        # Tasks we sent to remote nodes
        self._outbound: dict[str, DelegatedTask] = {}
        # Tasks remote nodes sent us
        self._inbound: dict[str, DelegatedTask] = {}
        # Futures awaiting results (task_id → asyncio.Future)
        self._result_futures: dict[str, asyncio.Future] = {}
        # Registered handlers for inbound tasks (task_type → coroutine callable)
        self._handlers: dict[str, Callable] = {}
        # Optional explicit event loop for cross-thread future resolution
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Priority queue for inbound tasks: heap of (-priority, seq, task_id)
        # Lower heap value = higher priority (max-heap via negation).
        # Used for introspection and cleanup; the actual async dispatch queue
        # is _dispatch_queue below.
        self._task_queue: list[tuple[int, int, str]] = []
        self._queue_seq: int = 0  # tie-breaker for equal-priority tasks (FIFO)

        # asyncio.PriorityQueue used by worker coroutines to pop tasks in
        # priority order.  Items: (-priority, seq, task_id).
        # Lazily created inside the event loop (same reason as semaphore).
        self._dispatch_queue: Optional[asyncio.PriorityQueue] = None

        # Semaphore to cap concurrent task execution (lazily initialised inside
        # the event loop so we don't require a running loop at construction time).
        self._concurrency_sem: Optional[asyncio.Semaphore] = None

        # Worker tasks that pull from _dispatch_queue (one per max_concurrent slot)
        self._workers: list[asyncio.Task] = []

        # Futures awaiting task_status_reply (correlation_id → Future)
        self._status_query_futures: dict[str, asyncio.Future] = {}

        # Last heartbeat timestamp for outbound tasks (task_id → epoch seconds)
        self._heartbeat_times: dict[str, float] = {}

    def set_broadcast(self, fn: Callable) -> None:
        self._broadcast_fn = fn

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the event loop so handle_task_result can resolve futures
        from a non-async context (e.g. the gossip background thread)."""
        self._loop = loop

    def register_handler(self, task_type: str, handler: Callable) -> None:
        """Register an async handler for a task type.
        Handler signature: async def handler(task: DelegatedTask) -> dict
        The returned dict becomes task.result.
        """
        self._handlers[task_type] = handler
        log.debug("Registered handler for task type: %s", task_type)

    # ── Outbound (delegation) ────────────────────────────────────────────────

    async def delegate(
        self,
        task_type: str,
        payload: dict,
        target_node: str,
        timeout: float = 60.0,
        priority: int = 0,
        max_retries: int = 0,
    ) -> str:
        """Delegate a task to a remote node. Returns task_id.

        Args:
            task_type:   Logical task type string (must match a handler on the
                         target node).
            payload:     Arbitrary dict passed to the handler.
            target_node: Node ID that should execute the task.
            timeout:     Seconds before the task is considered timed-out.
            priority:    Higher value = higher urgency (informational for now;
                         the executing node may use it to order its queue).
            max_retries: How many times the executor should retry on failure
                         before giving up.
        """
        task = DelegatedTask(
            task_type=task_type,
            payload=payload,
            source_node=self._local_node_id,
            target_node=target_node,
            timeout=timeout,
            priority=priority,
            max_retries=max_retries,
        )
        self._outbound[task.task_id] = task

        if self._broadcast_fn:
            await self._broadcast_fn({
                "type": "task_delegate",
                "task": task.to_dict(),
            })

        log.info(
            "Delegated task %s (%s) → %s [priority=%d, max_retries=%d]",
            task.task_id, task_type, target_node, priority, max_retries,
        )
        return task.task_id

    async def cancel(self, task_id: str) -> bool:
        """Cancel an outbound task that has not yet completed.

        Sends a ``task_cancel`` message to the target node and marks the local
        record as CANCELLED.  Returns True if the cancellation was sent, False
        if the task was already terminal or not found.
        """
        task = self._outbound.get(task_id)
        if task is None:
            return False
        if task.status in (TaskStatus.DONE, TaskStatus.FAILED,
                           TaskStatus.TIMEOUT, TaskStatus.CANCELLED):
            return False  # Already terminal

        task.status = TaskStatus.CANCELLED
        task.completed_at = time.time()

        # Resolve any waiting future with a cancellation error
        fut = self._result_futures.pop(task_id, None)
        if fut and not fut.done():
            loop = self._loop or (fut.get_loop() if hasattr(fut, "get_loop") else None)
            exc = RuntimeError(f"Task {task_id} was cancelled")
            if loop:
                loop.call_soon_threadsafe(fut.set_exception, exc)
            else:
                try:
                    fut.set_exception(exc)
                except Exception as e:
                    log.debug("session_router: %s", e)

        if self._broadcast_fn:
            await self._broadcast_fn({
                "type": "task_cancel",
                "task_id": task_id,
                "source_node": self._local_node_id,
                "target_node": task.target_node,
            })

        log.info("Cancelled task %s", task_id)
        return True

    async def wait_result(
        self,
        task_id: str,
        timeout: float = 60.0,
        heartbeat_timeout: float = 45.0,
    ) -> dict:
        """
        Await the result of a delegated task.

        Args:
            timeout:           Overall deadline in seconds.
            heartbeat_timeout: If >0, raise NetworkTimeoutError when no
                               liveness signal is received within this many
                               seconds.  A "liveness signal" is any of:
                               ACCEPTED status update (resets the timer from
                               acceptance time), a task_heartbeat message, or
                               task completion.  Defaults to 45 s.

        Returns result dict on success.
        Raises TimeoutError on overall deadline, NetworkTimeoutError on
        heartbeat silence, or RuntimeError on task failure.
        """
        task = self._outbound.get(task_id)
        if task is None:
            raise KeyError(f"Unknown task_id: {task_id}")

        if task.status in (TaskStatus.DONE, TaskStatus.FAILED):
            if task.status == TaskStatus.FAILED:
                raise RuntimeError(f"Task {task_id} failed: {task.error}")
            return task.result

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._result_futures[task_id] = fut

        # Seed heartbeat reference time with task creation so the window
        # starts from when we first sent the task, not from when a heartbeat
        # actually arrived (which may never happen for short tasks).
        if task_id not in self._heartbeat_times:
            self._heartbeat_times[task_id] = task.created_at

        # How often to wake up and check heartbeat staleness.
        check_interval = (
            min(heartbeat_timeout / 3.0, 10.0) if heartbeat_timeout > 0 else timeout
        )

        try:
            deadline = time.time() + timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    task.status = TaskStatus.TIMEOUT
                    raise TimeoutError(f"Task {task_id} timed out after {timeout}s")

                wait_time = min(remaining, check_interval)
                try:
                    result = await asyncio.wait_for(asyncio.shield(fut), timeout=wait_time)
                    return result
                except asyncio.TimeoutError:
                    pass  # No result yet — check heartbeat below

                if heartbeat_timeout > 0:
                    # While task is still ACCEPTED (queued, not yet running),
                    # heartbeats are not expected.  Keep the reference current
                    # so the timeout window only starts once the task is
                    # executing and expected to send heartbeats.
                    if task.status == TaskStatus.ACCEPTED:
                        self._heartbeat_times[task_id] = time.time()
                    last_hb = self._heartbeat_times.get(task_id, task.created_at)
                    since_hb = time.time() - last_hb
                    if since_hb > heartbeat_timeout:
                        task.status = TaskStatus.TIMEOUT
                        raise NetworkTimeoutError(
                            f"Task {task_id}: no heartbeat for {since_hb:.0f}s "
                            f"(possible network disconnect)"
                        )
        finally:
            self._result_futures.pop(task_id, None)

    # ── Inbound (execution) ──────────────────────────────────────────────────

    def _ensure_workers(self) -> None:
        """Lazily initialise the dispatch queue and worker pool.

        Must be called from within a running event loop.
        Also replaces any dead workers to keep the pool at max_concurrent.
        """
        if self._dispatch_queue is None:
            self._dispatch_queue = asyncio.PriorityQueue()
            # Spawn max_concurrent worker coroutines that pop from the queue
            # in priority order and execute tasks one-at-a-time per worker.
            for _ in range(self._max_concurrent):
                worker = asyncio.ensure_future(self._worker_loop())
                self._workers.append(worker)
            return
        # Health check: replace any dead workers
        alive = [w for w in self._workers if not w.done()]
        deficit = self._max_concurrent - len(alive)
        if deficit > 0:
            log.warning("Replacing %d dead worker(s)", deficit)
            for _ in range(deficit):
                alive.append(asyncio.ensure_future(self._worker_loop()))
            self._workers = alive

    async def _worker_loop(self) -> None:
        """Long-running worker that pops tasks from _dispatch_queue and executes them."""
        while True:  # outer: self-healing — restarts on unexpected crash
            try:
                while True:  # inner: normal operation
                    try:
                        _neg_priority, _seq, task_id = await self._dispatch_queue.get()
                        task = self._inbound.get(task_id)
                        if task is not None:
                            await self._execute_task(task)
                        self._dispatch_queue.task_done()
                    except asyncio.CancelledError:
                        raise  # propagate to outer for clean exit
                    except Exception as exc:
                        log.error("Worker loop error: %s", exc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("Worker crashed, restarting: %s", exc)
                await asyncio.sleep(0)

    async def handle_incoming_task(self, task_dict: dict) -> None:
        """Handle a task_delegate message received from a remote node.

        Tasks are enqueued in a priority-ordered queue (higher ``priority``
        value = executed first) and executed subject to the concurrency limit
        set by ``max_concurrent``.
        """
        task = DelegatedTask.from_dict(task_dict)

        # Only accept tasks targeted at us (or broadcast tasks with no target)
        if task.target_node and task.target_node != self._local_node_id:
            return

        if task.task_id in self._inbound:
            log.debug("Duplicate task ignored: %s", task.task_id)
            return

        task.status = TaskStatus.ACCEPTED
        task.accepted_at = time.time()
        self._inbound[task.task_id] = task

        log.info(
            "Accepted task %s (%s) from %s [priority=%d]",
            task.task_id, task.task_type, task.source_node, task.priority,
        )

        # Ensure the worker pool is running
        self._ensure_workers()

        # Enqueue into both the introspection heap and the async dispatch queue
        entry = (-task.priority, self._queue_seq, task.task_id)
        heapq.heappush(self._task_queue, entry)
        self._queue_seq += 1
        await self._dispatch_queue.put(entry)

        # Send acceptance acknowledgement
        await self._send_result(task, status=TaskStatus.ACCEPTED)

    async def handle_cancel(self, task_id: str) -> None:
        """Handle a task_cancel message from the originating node.

        If the task is still pending/accepted we mark it CANCELLED and stop
        execution.  If it is already running the cancellation will take effect
        after the current attempt finishes (we don't forcibly kill coroutines).
        """
        task = self._inbound.get(task_id)
        if task is None:
            return
        if task.status in (TaskStatus.DONE, TaskStatus.FAILED,
                           TaskStatus.TIMEOUT, TaskStatus.CANCELLED):
            return  # Already terminal — nothing to do

        task.status = TaskStatus.CANCELLED
        task.completed_at = time.time()
        log.info("Task %s cancelled by remote request", task_id)
        await self._send_result(task, status=TaskStatus.CANCELLED)

    async def _heartbeat_sender(self, task: DelegatedTask) -> None:
        """Periodically broadcast task_heartbeat while the task is RUNNING."""
        start = time.time()
        while task.status == TaskStatus.RUNNING:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            if task.status != TaskStatus.RUNNING:
                break
            if self._broadcast_fn:
                await self._broadcast_fn({
                    "type": "task_heartbeat",
                    "task_id": task.task_id,
                    "executor_node": self._local_node_id,
                    "source_node": task.source_node,
                    "elapsed_time": time.time() - start,
                })
            log.debug(
                "Sent heartbeat for task %s (elapsed=%.1fs)",
                task.task_id, time.time() - start,
            )

    async def _execute_task(self, task: DelegatedTask) -> None:
        # Bail out immediately if already cancelled before we even start
        if task.status == TaskStatus.CANCELLED:
            return

        handler = self._handlers.get(task.task_type)
        if handler is None:
            task.status = TaskStatus.FAILED
            task.error = f"No handler registered for task type: {task.task_type}"
            task.completed_at = time.time()
            log.warning("No handler for task type: %s", task.task_type)
            await self._send_result(task, status=TaskStatus.FAILED)
            return

        # Retry loop — attempt up to (1 + max_retries) times
        while True:
            # Abort if a cancellation arrived while we were waiting to retry
            if task.status == TaskStatus.CANCELLED:
                return

            task.status = TaskStatus.RUNNING
            hb_task = asyncio.ensure_future(self._heartbeat_sender(task))
            try:
                result = await asyncio.wait_for(handler(task), timeout=task.timeout)
                task.result = result if isinstance(result, dict) else {"value": result}
                task.status = TaskStatus.DONE
                task.completed_at = time.time()
                log.info("Task %s completed successfully (attempt %d)",
                         task.task_id, task.retry_count + 1)
                break
            except asyncio.TimeoutError:
                task.error = f"Task execution timed out after {task.timeout}s"
                task.completed_at = time.time()
                log.warning("Task %s timed out (attempt %d)", task.task_id, task.retry_count + 1)
                # Timeouts are not retried
                task.status = TaskStatus.FAILED
                break
            except Exception as exc:
                task.error = str(exc)
                task.completed_at = time.time()
                log.error("Task %s failed (attempt %d): %s",
                          task.task_id, task.retry_count + 1, exc)

                if task.retry_count < task.max_retries:
                    task.retry_count += 1
                    task.status = TaskStatus.PENDING  # reset for retry
                    log.info("Retrying task %s (attempt %d/%d)",
                             task.task_id, task.retry_count + 1, task.max_retries + 1)
                    await asyncio.sleep(0)  # yield before retrying
                    continue
                else:
                    task.status = TaskStatus.FAILED
                    break
            finally:
                hb_task.cancel()
                try:
                    await hb_task
                except asyncio.CancelledError:
                    pass

        await self._send_result(task, status=task.status)

    async def _send_result(self, task: DelegatedTask, status: str = "") -> None:
        """Broadcast a task_result message back to the source node."""
        if self._broadcast_fn:
            await self._broadcast_fn({
                "type": "task_result",
                "task_id": task.task_id,
                "source_node": task.source_node,   # original requester
                "executor_node": self._local_node_id,
                "status": status or task.status,
                "result": task.result,
                "error": task.error,
                "completed_at": task.completed_at,
            })

    # ── Cross-node status queries ────────────────────────────────────────────

    async def query_task_status(
        self, task_id: str, target_node: str, timeout: float = 10.0
    ) -> dict:
        """Ask a remote node for the current status of a task.

        Broadcasts a ``task_status_query`` message and waits up to *timeout*
        seconds for a ``task_status_reply``.  Returns the reply payload dict.

        Raises ``TimeoutError`` if no reply arrives in time.
        """
        correlation_id = gen_id("sq")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._status_query_futures[correlation_id] = fut

        if self._broadcast_fn:
            await self._broadcast_fn({
                "type": "task_status_query",
                "task_id": task_id,
                "source_node": self._local_node_id,
                "target_node": target_node,
                "correlation_id": correlation_id,
            })

        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Status query for task {task_id} timed out after {timeout}s"
            )
        finally:
            self._status_query_futures.pop(correlation_id, None)

    async def handle_status_query(self, msg: dict) -> None:
        """Handle an incoming ``task_status_query`` from a remote node.

        Looks up the task in the inbound queue and sends back a
        ``task_status_reply`` with the current status.
        """
        task_id = msg.get("task_id", "")
        correlation_id = msg.get("correlation_id", "")
        requester = msg.get("source_node", "")

        task = self._inbound.get(task_id)
        reply_payload: dict
        if task is None:
            reply_payload = {
                "task_id": task_id,
                "status": "unknown",
                "error": f"Task {task_id} not found on this node",
            }
        else:
            reply_payload = {
                "task_id": task_id,
                "status": task.status,
                "result": task.result,
                "error": task.error,
                "completed_at": task.completed_at,
            }

        if self._broadcast_fn:
            await self._broadcast_fn({
                "type": "task_status_reply",
                "correlation_id": correlation_id,
                "source_node": self._local_node_id,
                "target_node": requester,
                **reply_payload,
            })

        log.debug(
            "Replied to status query %s: task=%s status=%s",
            correlation_id, task_id, reply_payload.get("status"),
        )

    def handle_status_reply(self, msg: dict) -> None:
        """Handle an incoming ``task_status_reply`` (sync, called from gossip thread).

        Resolves the Future created by ``query_task_status``.
        """
        # Only handle replies addressed to this node
        target = msg.get("target_node", "")
        if target and target != self._local_node_id:
            return

        correlation_id = msg.get("correlation_id", "")
        fut = self._status_query_futures.get(correlation_id)
        if fut is None or fut.done():
            return

        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

        reply = {k: v for k, v in msg.items() if k != "correlation_id"}
        if loop is not None:
            loop.call_soon_threadsafe(fut.set_result, reply)
        else:
            try:
                fut.set_result(reply)
            except Exception as e:
                log.debug("session_router: %s", e)

        log.debug(
            "Received status reply for correlation=%s task=%s status=%s",
            correlation_id, msg.get("task_id"), msg.get("status"),
        )

    # ── Result reception ─────────────────────────────────────────────────────

    def handle_task_heartbeat(self, msg: dict) -> None:
        """Handle a task_heartbeat from the executing node (sync, gossip thread).

        Updates the last-heartbeat timestamp so wait_result() can detect
        network silence vs. a normally-running long task.
        """
        task_id = msg.get("task_id", "")
        if task_id not in self._outbound:
            return
        self._heartbeat_times[task_id] = time.time()
        log.debug(
            "Heartbeat received for task %s (executor=%s elapsed=%.1fs)",
            task_id, msg.get("executor_node"), msg.get("elapsed_time", 0),
        )

    def handle_task_result(self, msg: dict) -> None:
        """Handle a task_result message received from a remote node (sync, called from gossip thread)."""
        task_id = msg.get("task_id", "")
        status = msg.get("status", "")
        result = msg.get("result", {})
        error = msg.get("error", "")

        task = self._outbound.get(task_id)
        if task is None:
            return  # Not our task

        task.status = status
        task.result = result
        task.error = error
        if status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.TIMEOUT):
            task.completed_at = msg.get("completed_at", time.time())

        if status == TaskStatus.ACCEPTED:
            # Track acceptance time on the outbound task so diagnostics and
            # wait_result can distinguish queue-wait time from execution time.
            task.accepted_at = time.time()
            # Refresh heartbeat timer from acceptance time so heartbeat_timeout
            # is measured from when the remote node accepted the task, not from
            # task creation.  This prevents false NetworkTimeoutError when the
            # task is queued behind other tasks on the executor (max_concurrent).
            self._heartbeat_times[task_id] = task.accepted_at
            log.debug("Task %s accepted by remote node, refreshing heartbeat timer", task_id)
            return  # ACCEPTED does not resolve the Future — keep waiting

        log.info("Task %s result received: status=%s", task_id, status)

        # Resolve the waiting Future if any
        fut = self._result_futures.get(task_id)
        if fut and not fut.done():
            # Prefer the explicitly stored loop (set by attach_task_delegate /
            # set_loop) so this works when called from a background thread.
            loop = self._loop or fut.get_loop()
            if status == TaskStatus.DONE:
                loop.call_soon_threadsafe(fut.set_result, result)
            elif status in (TaskStatus.FAILED, TaskStatus.TIMEOUT):
                exc = RuntimeError(f"Task {task_id} {status}: {error}")
                loop.call_soon_threadsafe(fut.set_exception, exc)

    # ── Housekeeping ─────────────────────────────────────────────────────────

    def cleanup_expired(self) -> int:
        """Remove completed tasks older than TASK_TTL. Returns count removed."""
        now = time.time()
        removed = 0
        expired_ids: list[str] = []
        for store in (self._outbound, self._inbound):
            for tid in list(store):
                t = store[tid]
                if t.status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED):
                    if now - t.completed_at > self.TASK_TTL:
                        del store[tid]
                        expired_ids.append(tid)
                        removed += 1

        # Clean up heartbeat timestamps for expired tasks (prevents memory leak)
        for tid in expired_ids:
            self._heartbeat_times.pop(tid, None)

        # Rebuild the priority queue to drop entries for tasks that no longer exist
        self._task_queue = [
            entry for entry in self._task_queue
            if entry[2] in self._inbound
        ]
        heapq.heapify(self._task_queue)

        return removed

    def get_task(self, task_id: str) -> Optional[DelegatedTask]:
        return self._outbound.get(task_id) or self._inbound.get(task_id)

    def list_outbound(self) -> list[dict]:
        return [t.to_dict() for t in self._outbound.values()]

    def list_inbound(self) -> list[dict]:
        return [t.to_dict() for t in self._inbound.values()]

    def queue_stats(self) -> dict:
        """Return a snapshot of the current task queue state.

        Useful for monitoring and load-balancing decisions.
        """
        pending = sum(
            1 for t in self._inbound.values()
            if t.status in (TaskStatus.PENDING, TaskStatus.ACCEPTED)
        )
        running = sum(
            1 for t in self._inbound.values()
            if t.status == TaskStatus.RUNNING
        )
        return {
            "node_id": self._local_node_id,
            "queued": pending,
            "running": running,
            "max_concurrent": self._max_concurrent,
            "total_inbound": len(self._inbound),
            "total_outbound": len(self._outbound),
        }

    def get_diagnostics(self) -> dict:
        """Return structured diagnostics for the dashboard.

        Returns:
            {
                queued, running, max_concurrent,
                outbound_pending, outbound_done,
                inbound_active, inbound_done,
                workers_alive,
            }
        """
        outbound_terminal = {TaskStatus.DONE, TaskStatus.FAILED,
                             TaskStatus.TIMEOUT, TaskStatus.CANCELLED}
        outbound_pending = sum(
            1 for t in self._outbound.values() if t.status not in outbound_terminal
        )
        outbound_done = sum(
            1 for t in self._outbound.values() if t.status in outbound_terminal
        )
        inbound_active = sum(
            1 for t in self._inbound.values()
            if t.status in (TaskStatus.PENDING, TaskStatus.ACCEPTED, TaskStatus.RUNNING)
        )
        inbound_done = sum(
            1 for t in self._inbound.values() if t.status in outbound_terminal
        )
        queued = sum(
            1 for t in self._inbound.values()
            if t.status in (TaskStatus.PENDING, TaskStatus.ACCEPTED)
        )
        running = sum(
            1 for t in self._inbound.values() if t.status == TaskStatus.RUNNING
        )
        workers_alive = sum(
            1 for w in self._workers if not w.done()
        )
        return {
            "queued": queued,
            "running": running,
            "max_concurrent": self._max_concurrent,
            "outbound_pending": outbound_pending,
            "outbound_done": outbound_done,
            "inbound_active": inbound_active,
            "inbound_done": inbound_done,
            "workers_alive": workers_alive,
        }


@dataclass
class Session:
    id: str = ""
    channel: str = ""         # "terminal", "dashboard", "discord", "webhook"
    user_id: str = ""         # channel-specific user identifier
    owner_node: str = ""      # node_id that owns this session
    locked_at: float = 0.0
    last_activity: float = 0.0

    def to_dict(self) -> dict:
        return {"id": self.id, "channel": self.channel, "user_id": self.user_id,
                "owner_node": self.owner_node, "locked_at": self.locked_at,
                "last_activity": self.last_activity}


class SessionRouter:
    """Routes sessions to nodes with distributed locking."""

    SESSION_TIMEOUT = 120.0  # seconds idle before auto-release

    def __init__(self, local_node_id: str):
        self._local_node_id = local_node_id
        self._sessions: dict[str, Session] = {}
        self._broadcast_fn: Callable | None = None  # Set to gossip_mesh.broadcast_event

    def set_broadcast(self, fn: Callable) -> None:
        """Set the broadcast function for session lock/release events."""
        self._broadcast_fn = fn

    def try_lock(self, session_id: str, channel: str = "", user_id: str = "") -> bool:
        """Try to lock a session for this node. Returns True if successful."""
        existing = self._sessions.get(session_id)

        if existing and existing.owner_node:
            if existing.owner_node == self._local_node_id:
                # Already own it
                existing.last_activity = time.time()
                return True
            # Another node owns it — check if timed out
            if time.time() - existing.last_activity > self.SESSION_TIMEOUT:
                log.info("Session %s timed out from %s, claiming", session_id, existing.owner_node)
            else:
                return False  # Another node actively owns it

        # Lock it
        session = Session(
            id=session_id, channel=channel, user_id=user_id,
            owner_node=self._local_node_id,
            locked_at=time.time(), last_activity=time.time(),
        )
        self._sessions[session_id] = session

        # Broadcast lock (safe for both async and sync contexts)
        if self._broadcast_fn:
            event_data = {
                "type": "session_lock",
                "session_id": session_id,
                "node_id": self._local_node_id,
                "channel": channel,
                "timestamp": time.time(),
            }
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._broadcast_fn(event_data))
            except RuntimeError:
                # No running loop — schedule via thread-safe call
                import threading
                def _broadcast():
                    _loop = asyncio.new_event_loop()
                    _loop.run_until_complete(self._broadcast_fn(event_data))
                    _loop.close()
                threading.Thread(target=_broadcast, daemon=True).start()

        log.info("Session locked: %s → %s", session_id, self._local_node_id)
        return True

    def release(self, session_id: str) -> None:
        """Release a session lock."""
        session = self._sessions.get(session_id)
        if session and session.owner_node == self._local_node_id:
            session.owner_node = ""
            if self._broadcast_fn:
                event_data = {
                    "type": "session_release",
                    "session_id": session_id,
                    "node_id": self._local_node_id,
                }
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self._broadcast_fn(event_data))
                except RuntimeError:
                    pass  # Release doesn't need broadcast if no loop
            log.info("Session released: %s", session_id)

    def handle_remote_lock(self, session_id: str, remote_node_id: str, channel: str = "", timestamp: float = 0) -> None:
        """Handle a session lock from another node."""
        existing = self._sessions.get(session_id)

        if existing and existing.owner_node == self._local_node_id:
            # Conflict! Deterministic tiebreaker: lower node_id wins
            if remote_node_id < self._local_node_id:
                log.info("Session conflict %s: yielding to %s (lower ID)", session_id, remote_node_id)
                existing.owner_node = remote_node_id
                existing.last_activity = timestamp or time.time()
            else:
                log.info("Session conflict %s: keeping (our ID is lower)", session_id)
                return
        else:
            self._sessions[session_id] = Session(
                id=session_id, channel=channel, owner_node=remote_node_id,
                locked_at=timestamp or time.time(), last_activity=timestamp or time.time(),
            )

    def handle_remote_release(self, session_id: str, remote_node_id: str) -> None:
        """Handle a session release from another node."""
        session = self._sessions.get(session_id)
        if session and session.owner_node == remote_node_id:
            session.owner_node = ""

    def release_all_for_node(self, dead_node_id: str) -> list[str]:
        """Release all sessions owned by a dead node. Returns released session IDs."""
        released = []
        for sid, session in self._sessions.items():
            if session.owner_node == dead_node_id:
                session.owner_node = ""
                released.append(sid)
        if released:
            log.info("Released %d sessions from dead node %s", len(released), dead_node_id)
        return released

    def is_mine(self, session_id: str) -> bool:
        """Check if this node owns the given session."""
        session = self._sessions.get(session_id)
        return session is not None and session.owner_node == self._local_node_id

    def get_owner(self, session_id: str) -> str:
        """Get the owner node_id of a session, or '' if unowned."""
        session = self._sessions.get(session_id)
        return session.owner_node if session else ""

    def get_session_id(self, channel: str, user_id: str = "") -> str:
        """Generate a deterministic session ID from channel and user."""
        return f"{channel}:{user_id}" if user_id else f"{channel}:default"

    def list_sessions(self) -> list[dict]:
        return [s.to_dict() for s in self._sessions.values()]

    def cleanup_expired(self) -> list[str]:
        """Remove expired sessions. Called periodically."""
        now = time.time()
        expired = []
        for sid, session in list(self._sessions.items()):
            if session.owner_node and (now - session.last_activity) > self.SESSION_TIMEOUT:
                session.owner_node = ""
                expired.append(sid)
        return expired
