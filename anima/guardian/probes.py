"""Watchdog-layer probes — read-only sensors. Each reports one component's
current health via ``check()``. P0 is passive: probes only observe; they never
act, and a probe that raises is isolated (Sentinel maps it to UNKNOWN).
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from functools import partial

from anima.guardian.signal import Component, Health, HealthReport, Severity
from anima.utils.logging import get_logger

log = get_logger("guardian.probes")


class Probe(ABC):
    """A read-only health sensor for one component."""

    component: Component

    @abstractmethod
    async def check(self) -> HealthReport:
        """Return the component's CURRENT health. Cheap, side-effect-free.
        Should not raise — return a DOWN/UNKNOWN report on internal error."""

    async def aclose(self) -> None:  # optional cleanup
        return None


# ── TASK: the highest-value probe — surfaces silent background-task death ──
class TaskProbe(Probe):
    """Push-based: ``add_done_callback`` captures a task dying with an exception
    the instant it happens (the codebase had ZERO such callbacks — a dead
    cognitive/heartbeat loop was completely unobserved). check() reports DOWN
    while any watched task is dead, with the offending task + exception."""

    component = Component.TASK

    def __init__(self) -> None:
        self._kinds: dict[str, str] = {}          # name -> "alive" | repr(exc)
        self._shutting_down = False

    def watch(self, task: "asyncio.Task", *, kind: str) -> None:
        self._kinds[kind] = "alive"
        task.add_done_callback(partial(self._on_done, kind=kind))

    def begin_shutdown(self) -> None:
        # Teardown cancels tasks; their exits must NOT register as failures.
        self._shutting_down = True

    def _on_done(self, task: "asyncio.Task", *, kind: str) -> None:
        if self._shutting_down or task.cancelled():
            return
        try:
            exc = task.exception()
        except Exception:  # noqa: BLE001
            exc = None
        if exc is None:
            # A clean return is NOT a failure: some "tasks" (e.g. heartbeat.start)
            # spawn their own sub-loops and return by design. Only an unhandled
            # EXCEPTION means a loop actually crashed — that's the blind spot we
            # exist to surface. (Distinguishing "should-run-forever returned" is a
            # later refinement requiring a per-task long-lived flag.)
            self._kinds[kind] = "exited"
            log.debug("guardian: task %s returned cleanly", kind)
            return
        self._kinds[kind] = repr(exc)
        log.error("guardian: task %s DIED: %r", kind, exc)

    async def check(self) -> HealthReport:
        # Only exception-deaths count as DOWN; "alive" and clean "exited" are fine.
        dead = {k: v for k, v in self._kinds.items() if v not in ("alive", "exited")}
        if not dead:
            return HealthReport(self.component, Health.OK,
                                detail=f"{len(self._kinds)} task(s) watched")
        return HealthReport(self.component, Health.DOWN, Severity.CRITICAL,
                            detail="crashed tasks: " + ", ".join(dead),
                            raw={"dead": dead})


# ── LLM: read the router's own circuit-breaker / degradation state (no calls) ──
class LlmProbe(Probe):
    component = Component.LLM

    def __init__(self, llm_router) -> None:
        self._router = llm_router

    async def check(self) -> HealthReport:
        try:
            st = self._router.get_status() if self._router else {}
        except Exception as e:  # noqa: BLE001
            return HealthReport(self.component, Health.UNKNOWN, Severity.WARN,
                                detail=f"get_status failed: {e}")
        if st.get("circuit_open"):
            return HealthReport(self.component, Health.DOWN, Severity.ERROR,
                                detail="circuit open (all providers failing)", raw=st)
        if st.get("degraded"):
            return HealthReport(self.component, Health.DEGRADED, Severity.WARN, self_healed=True,
                                detail=f"on fallback {st.get('active_model', '?')}", raw=st)
        return HealthReport(self.component, Health.OK, detail=st.get("active_model", ""), raw=st)


# ── DB: connected? failed over to local? (folds in the existing failover) ──
class DbProbe(Probe):
    component = Component.DB

    def __init__(self, db) -> None:
        self._db = db  # PgDatabaseManager

    async def check(self) -> HealthReport:
        try:
            st = self._db.status() if self._db else {}
        except Exception as e:  # noqa: BLE001
            return HealthReport(self.component, Health.UNKNOWN, Severity.WARN,
                                detail=f"status failed: {e}")
        if not st.get("is_open"):
            return HealthReport(self.component, Health.DOWN, Severity.CRITICAL,
                                detail="no Postgres connection", raw=st)
        if st.get("using_local"):
            return HealthReport(self.component, Health.DEGRADED, Severity.WARN, self_healed=True,
                                detail="FAILED OVER TO LOCAL (primary unreachable)", raw=st)
        return HealthReport(self.component, Health.OK, detail="on primary", raw=st)


# ── MESH: peer connectivity + split-brain (read-only) ──
class MeshProbe(Probe):
    component = Component.MESH

    def __init__(self, gossip_mesh=None, node_identity=None, split_brain=None) -> None:
        self._gossip = gossip_mesh
        self._node = node_identity
        self._split = split_brain

    async def check(self) -> HealthReport:
        if not self._gossip:
            return HealthReport(self.component, Health.OK, detail="network disabled")
        try:
            alive = self._gossip.get_alive_count()
            registered = (self._node.get_active_count()
                          if self._node and hasattr(self._node, "get_active_count")
                          else alive)
            readonly = bool(getattr(self._split, "is_readonly", False)) if self._split else False
        except Exception as e:  # noqa: BLE001
            return HealthReport(self.component, Health.UNKNOWN, Severity.WARN,
                                detail=f"mesh read failed: {e}")
        raw = {"alive": alive, "registered": registered, "split_brain": readonly}
        if readonly:
            return HealthReport(self.component, Health.DEGRADED, Severity.WARN,
                                detail="split-brain: minority partition (readonly)", raw=raw)
        if registered and alive < registered:
            return HealthReport(self.component, Health.DEGRADED, Severity.WARN,
                                detail=f"{alive}/{registered} peers alive", raw=raw)
        return HealthReport(self.component, Health.OK, detail=f"{alive}/{registered} peers", raw=raw)


# ── RESOURCE: host CPU / memory pressure (advisory) ──
class ResourceProbe(Probe):
    component = Component.RESOURCE

    async def check(self) -> HealthReport:
        try:
            import psutil
            mem = psutil.virtual_memory().percent
            cpu = psutil.cpu_percent(interval=None)
        except Exception as e:  # noqa: BLE001
            return HealthReport(self.component, Health.UNKNOWN, Severity.INFO,
                                detail=f"psutil unavailable: {e}")
        raw = {"mem_percent": mem, "cpu_percent": cpu}
        if mem >= 97:
            return HealthReport(self.component, Health.DOWN, Severity.ERROR,
                                detail=f"memory critical {mem:.0f}%", pressure=mem / 100, raw=raw)
        if mem >= 90:
            return HealthReport(self.component, Health.DEGRADED, Severity.WARN,
                                detail=f"memory high {mem:.0f}%", pressure=mem / 100, raw=raw)
        return HealthReport(self.component, Health.OK, detail=f"mem {mem:.0f}% cpu {cpu:.0f}%",
                            pressure=mem / 100, raw=raw)
