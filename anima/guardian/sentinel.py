"""Sentinel — the self-healing supervisor's brain.

Standalone supervised asyncio task (NOT a heartbeat tick — it watches the
heartbeat). Each loop it polls every probe, runs a per-component escalation FSM,
and — when a fault is CONFIRMED (sustained past the warn threshold) — engages
the lightest applicable Fixer. Repairs run as background tasks so the loop keeps
ticking. Safety rails baked in: warn-first, per-component cooldown + attempt
budget, a recovery-confirm window (a fix never self-declares OK), and a
``mode: auto|manual`` switch (manual → propose + await human, never execute).

P2 wires the safe/reversible rungs (LLM backstop, DB recover); restart/code
rungs slot in by harshness later. Every observe / transition / repair is audited
to data/logs/guardian_actions.jsonl. Nothing here may crash the app: every probe,
decision, and repair is exception-isolated.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from anima.guardian.fixer import (
    ComponentPolicy, FsmState, Registry, RepairAction, RepairOutcome, RepairResult,
)
from anima.guardian.probes import Probe, TaskProbe
from anima.guardian.signal import (
    AuditRecord, Component, Fault, Health, HealthReport, Severity, worst_health,
)
from anima.utils.logging import get_logger

log = get_logger("guardian.sentinel")


@dataclass
class _Domain:
    state: FsmState = FsmState.OK
    health: Health = Health.UNKNOWN
    detail: str = ""
    self_healed: bool = False
    warn_count: int = 0           # consecutive faulted observations
    attempts: int = 0             # repair attempts at the safe rung
    ok_streak: int = 0            # consecutive healthy reads while RECOVERING
    last_action_mono: float = -1e9
    last_ok_ts: float | None = None
    ts: float = field(default_factory=time.time)


class Sentinel:
    def __init__(self, *, probes: list[Probe], registry: Registry | None = None,
                 policies: dict[Component, ComponentPolicy] | None = None,
                 config: dict | None = None, node_id: str = "local") -> None:
        cfg = config or {}
        self._probes = probes
        self._registry = registry or Registry([])
        self._policies = policies or {}
        self._default_policy = ComponentPolicy()
        self._interval = float(cfg.get("interval_s", 5))
        self._probe_timeout = float(cfg.get("probe_timeout_s", 3))
        self._repair_timeout = float(cfg.get("repair_timeout_s", 45))
        self._node_id = node_id
        self._domains: dict[Component, _Domain] = {}
        self._tick = 0
        self._stopped = False
        self._task_probe: TaskProbe | None = next(
            (p for p in probes if isinstance(p, TaskProbe)), None)
        self._repair_tasks: set[asyncio.Task] = set()
        self._audit_path = self._resolve_audit_path()

    def _policy(self, c: Component) -> ComponentPolicy:
        return self._policies.get(c, self._default_policy)

    # ── task supervision passthrough ──
    def watch_task(self, task: "asyncio.Task", *, kind: str) -> None:
        if self._task_probe:
            self._task_probe.watch(task, kind=kind)

    # ── lifecycle ──
    async def run(self) -> None:
        log.info("Sentinel started (interval=%ss, probes=%d, fixers=%d)",
                 self._interval, len(self._probes), len(self._registry._strategies))
        while not self._stopped:
            try:
                self._tick += 1
                for probe in self._probes:
                    hr = await self._safe_check(probe)
                    try:
                        self._observe(hr)
                    except Exception as e:  # noqa: BLE001 — never kill the loop
                        log.warning("guardian: decide(%s) failed: %s", probe.component.value, e)
                from anima.guardian import handoff
                handoff.write_sentinel_tick(self._tick)
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                log.error("guardian: loop iteration error: %s", e)
                try:
                    await asyncio.sleep(self._interval)
                except asyncio.CancelledError:
                    break
        log.info("Sentinel stopped")

    def begin_shutdown(self) -> None:
        self._stopped = True
        if self._task_probe:
            self._task_probe.begin_shutdown()
        for t in list(self._repair_tasks):
            t.cancel()

    async def stop(self) -> None:
        self.begin_shutdown()
        for p in self._probes:
            try:
                await p.aclose()
            except Exception:  # noqa: BLE001
                pass

    # ── probe ──
    async def _safe_check(self, probe: Probe) -> HealthReport:
        try:
            return await asyncio.wait_for(probe.check(), self._probe_timeout)
        except asyncio.TimeoutError:
            return HealthReport(probe.component, Health.UNKNOWN, Severity.WARN, detail="probe timeout")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            return HealthReport(probe.component, Health.UNKNOWN, Severity.WARN, detail=f"probe error: {e}")

    # ── FSM ──
    def _observe(self, hr: HealthReport) -> None:
        d = self._domains.setdefault(hr.component, _Domain())
        prev = d.state
        self._fsm(d, hr)
        d.health = hr.health
        d.detail = hr.detail
        d.self_healed = hr.self_healed
        d.ts = hr.ts
        if hr.health == Health.OK:
            d.last_ok_ts = hr.ts
        if d.state != prev:
            self._audit_transition(hr, prev, d.state)

    def _fsm(self, d: _Domain, hr: HealthReport) -> None:
        now = time.monotonic()
        pol = self._policy(hr.component)

        # A subsystem that auto-switched to its own backup is REPORTING, not a
        # fault to repair → stable DEGRADED observation.
        if hr.self_healed:
            if d.state not in (FsmState.REPAIRING, FsmState.RECOVERING):
                d.state = FsmState.DEGRADED
            d.warn_count = 0
            return

        # Healthy read.
        if hr.health == Health.OK:
            if d.state == FsmState.REPAIRING:
                return  # let the repair result drive the transition
            if d.state == FsmState.RECOVERING:
                d.ok_streak += 1
                if d.ok_streak >= pol.recover_confirm:
                    d.state = FsmState.OK
                    d.warn_count = d.attempts = d.ok_streak = 0
                return
            d.state = FsmState.OK
            d.warn_count = d.attempts = 0
            return

        # UNKNOWN (probe blip / timeout): warn, never repair on it.
        if hr.health == Health.UNKNOWN:
            if d.state == FsmState.OK:
                d.state = FsmState.WARNED
            return

        # Faulted (DEGRADED/DOWN, not self-healed).
        if d.state in (FsmState.REPAIRING, FsmState.HELD):
            return  # in-flight, or exhausted and holding
        d.warn_count += 1
        if d.warn_count < pol.warn_threshold:
            d.state = FsmState.WARNED
            return
        # Confirmed fault → engage (cooldown-gated).
        if now - d.last_action_mono < pol.cooldown_s:
            return
        self._engage(d, hr, now, pol)

    def _engage(self, d: _Domain, hr: HealthReport, now: float, pol: ComponentPolicy) -> None:
        if not pol.enabled:
            d.state = FsmState.DEGRADED   # observe-only for this component
            return
        fault = Fault(component=hr.component, health=hr.health, severity=hr.severity,
                      summary=hr.detail, signature=f"{hr.component.value}:{hr.health.value}")
        d.last_action_mono = now
        if pol.mode == "manual":
            # Propose, don't execute — await human authorization.
            d.state = FsmState.DEGRADED
            log.warning("guardian: %s would repair (manual mode) — awaiting approval: %s",
                        hr.component.value, hr.detail)
            self._audit(AuditRecord(phase="proposed", component=hr.component, severity=Severity.WARN,
                                    message=f"manual mode — WOULD repair: {hr.detail}",
                                    health=hr.health, node_id=self._node_id))
            return
        if not self._registry.claim(hr.component):
            return  # a repair is already in flight for this component
        d.state = FsmState.REPAIRING
        t = asyncio.create_task(self._run_repair(d, fault))
        self._repair_tasks.add(t)
        t.add_done_callback(self._repair_tasks.discard)

    async def _run_repair(self, d: _Domain, fault: Fault) -> None:
        c = fault.component
        try:
            strategy = await self._registry.strategy_for(fault)
            if strategy is None:
                d.state = FsmState.HELD
                self._audit(AuditRecord(phase="repair", component=c, severity=Severity.WARN,
                                        message="no safe strategy applies — holding degraded",
                                        health=d.health, node_id=self._node_id))
                return
            action = RepairAction(fault_id=fault.id, component=c, kind=strategy.kind,
                                  reason=fault.summary, attempt=d.attempts + 1)
            self._audit(AuditRecord(phase="repair_start", component=c, severity=Severity.WARN,
                                    message=f"{strategy.kind.value} attempt {action.attempt}: {fault.summary}",
                                    health=d.health, node_id=self._node_id, data={"action_id": action.id}))
            try:
                result = await asyncio.wait_for(strategy.repair(action), self._repair_timeout)
            except asyncio.TimeoutError:
                result = RepairResult(action.id, strategy.kind, RepairOutcome.INEFFECTIVE, "repair timed out")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — repair must not crash the supervisor
                result = RepairResult(action.id, strategy.kind, RepairOutcome.FAILED, repr(e))
            self._on_repair_result(d, c, result)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            log.warning("guardian: repair driver for %s errored: %s", c.value, e)
        finally:
            self._registry.release(c)

    def _on_repair_result(self, d: _Domain, c: Component, result: RepairResult) -> None:
        pol = self._policy(c)
        if result.ok:
            d.state = FsmState.RECOVERING
            d.ok_streak = 0
            sev = Severity.INFO
            msg = f"{result.kind.value} → {result.outcome.value}: {result.detail}"
        else:
            d.attempts += 1
            if d.attempts >= pol.max_attempts or result.outcome in (
                    RepairOutcome.REFUSED, RepairOutcome.ALERT_ONLY):
                d.state = FsmState.HELD
                sev = Severity.ERROR
                msg = (f"{result.kind.value} → {result.outcome.value} "
                       f"(attempt {d.attempts}/{pol.max_attempts}) — HOLDING degraded, alert")
            else:
                d.state = FsmState.DEGRADED   # re-engage after cooldown on next faulted tick
                sev = Severity.WARN
                msg = f"{result.kind.value} → {result.outcome.value}, will retry (attempt {d.attempts})"
        log.log(40 if sev >= Severity.ERROR else 20, "guardian: %s", msg)
        self._audit(AuditRecord(phase="repair", component=c, severity=sev, message=msg,
                                health=result.new_health, node_id=self._node_id,
                                data={"outcome": result.outcome.value, "kind": result.kind.value}))

    # ── audit ──
    def _audit_transition(self, hr: HealthReport, prev: FsmState, new: FsmState) -> None:
        msg = f"{hr.component.value}: {prev.value} → {new.value} ({hr.detail})"
        faulted = new in (FsmState.WARNED, FsmState.DEGRADED, FsmState.REPAIRING, FsmState.HELD)
        log.log(30 if faulted else 20, "guardian: %s", msg)
        self._audit(AuditRecord(phase="transition", component=hr.component, severity=hr.severity,
                                message=msg, health=hr.health, node_id=self._node_id,
                                data={"prev_state": prev.value, "state": new.value,
                                      "self_healed": hr.self_healed}))

    def _audit(self, rec: AuditRecord) -> None:
        if not self._audit_path:
            return
        try:
            with open(self._audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec.to_json(), ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001
            log.debug("guardian: audit write failed: %s", e)

    @staticmethod
    def _resolve_audit_path():
        try:
            from anima.config import data_dir
            d = data_dir() / "logs"
            d.mkdir(parents=True, exist_ok=True)
            return d / "guardian_actions.jsonl"
        except Exception:  # noqa: BLE001
            return None

    # ── surfacing ──
    @property
    def sentinel_tick(self) -> int:
        return self._tick

    def snapshot(self) -> dict:
        comps = {
            c.value: {
                "health": d.health.value,
                "state": d.state.value,
                "detail": d.detail,
                "self_healed": d.self_healed,
                "attempts": d.attempts,
                "last_ok_ts": d.last_ok_ts,
                "ts": d.ts,
            }
            for c, d in self._domains.items()
        }
        overall = (worst_health(d.health for d in self._domains.values())
                   if self._domains else Health.UNKNOWN)
        return {
            "overall": overall.value,
            "ts": time.time(),
            "sentinel_tick": self._tick,
            "active_repairs": len(self._repair_tasks),
            "node_id": self._node_id,
            "components": comps,
        }
