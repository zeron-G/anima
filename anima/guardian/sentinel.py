"""Sentinel — the self-healing supervisor's brain.

P0 scope: PASSIVE. It runs its own supervised loop, polls each probe for current
health, tracks per-component state, audits every transition to an append-only
JSONL, and exposes a snapshot for /v1/status + the dashboard. It takes NO repair
action (dry-run by construction) — the Fixer layer arrives in a later phase.

It is a standalone asyncio task (NOT a heartbeat tick): the heartbeat is one of
the things it watches, so it must not be killable by the very failures it
monitors. The loop is exception-isolated per probe and per iteration; one bad
probe never stops the others, and nothing here may crash the app.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

from anima.guardian.probes import Probe, TaskProbe
from anima.guardian.signal import (
    AuditRecord, Component, Health, HealthReport, Severity, worst_health,
)
from anima.utils.logging import get_logger

log = get_logger("guardian.sentinel")


@dataclass
class _State:
    health: Health = Health.UNKNOWN
    detail: str = ""
    pressure: float = 0.0
    self_healed: bool = False
    ts: float = field(default_factory=time.time)
    last_ok_ts: float | None = None


class Sentinel:
    """Passive health supervisor (P0). Observes, audits, surfaces — no actions."""

    def __init__(self, *, probes: list[Probe], config: dict | None = None,
                 node_id: str = "local") -> None:
        cfg = config or {}
        self._probes = probes
        self._interval = float(cfg.get("interval_s", 5))
        self._probe_timeout = float(cfg.get("probe_timeout_s", 3))
        self._node_id = node_id
        self._states: dict[Component, _State] = {}
        self._tick = 0
        self._stopped = False
        self._task_probe: TaskProbe | None = next(
            (p for p in probes if isinstance(p, TaskProbe)), None)
        self._audit_path = self._resolve_audit_path()

    # ── task supervision passthrough ──
    def watch_task(self, task: "asyncio.Task", *, kind: str) -> None:
        if self._task_probe:
            self._task_probe.watch(task, kind=kind)

    # ── lifecycle ──
    async def run(self) -> None:
        log.info("Sentinel started — passive/observe-only (interval=%ss, probes=%d)",
                 self._interval, len(self._probes))
        while not self._stopped:
            try:
                self._tick += 1
                for probe in self._probes:
                    hr = await self._safe_check(probe)
                    try:
                        self._observe(hr)
                    except Exception as e:  # noqa: BLE001 — observe must never kill the loop
                        log.warning("guardian: observe(%s) failed: %s", probe.component.value, e)
                # Stamp the cross-process liveness token: the external limb reads
                # this to tell "process alive but Sentinel frozen" from "healthy".
                from anima.guardian import handoff
                handoff.write_sentinel_tick(self._tick)
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001 — never hot-spin, never die
                log.error("guardian: loop iteration error: %s", e)
                try:
                    await asyncio.sleep(self._interval)
                except asyncio.CancelledError:
                    break
        log.info("Sentinel stopped")

    def begin_shutdown(self) -> None:
        """Stop observing as teardown begins (so dying tasks aren't false alarms)."""
        self._stopped = True
        if self._task_probe:
            self._task_probe.begin_shutdown()

    async def stop(self) -> None:
        self.begin_shutdown()
        for p in self._probes:
            try:
                await p.aclose()
            except Exception:  # noqa: BLE001
                pass

    # ── internals ──
    async def _safe_check(self, probe: Probe) -> HealthReport:
        try:
            return await asyncio.wait_for(probe.check(), self._probe_timeout)
        except asyncio.TimeoutError:
            return HealthReport(probe.component, Health.UNKNOWN, Severity.WARN,
                                detail="probe timeout")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            return HealthReport(probe.component, Health.UNKNOWN, Severity.WARN,
                                detail=f"probe error: {e}")

    def _observe(self, hr: HealthReport) -> None:
        prev = self._states.get(hr.component)
        prev_health = prev.health if prev else Health.UNKNOWN
        st = _State(
            health=hr.health, detail=hr.detail, pressure=hr.pressure,
            self_healed=hr.self_healed, ts=hr.ts,
            last_ok_ts=hr.ts if hr.health == Health.OK else (prev.last_ok_ts if prev else None),
        )
        self._states[hr.component] = st
        if hr.health != prev_health:
            self._on_transition(hr, prev_health)

    def _on_transition(self, hr: HealthReport, prev: Health) -> None:
        msg = f"{hr.component.value}: {prev.value} → {hr.health.value} ({hr.detail})"
        if hr.faulted() or hr.health == Health.UNKNOWN:
            log.warning("guardian: %s", msg)
        else:
            log.info("guardian: %s", msg)
        self._audit(AuditRecord(
            phase="transition", component=hr.component, severity=hr.severity,
            message=msg, health=hr.health, node_id=self._node_id,
            data={"prev": prev.value, "self_healed": hr.self_healed, **dict(hr.raw)},
        ))

    def _audit(self, rec: AuditRecord) -> None:
        if not self._audit_path:
            return
        try:
            with open(self._audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec.to_json(), ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001 — audit is best-effort, never fatal
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
                "health": s.health.value,
                "detail": s.detail,
                "pressure": round(s.pressure, 3),
                "self_healed": s.self_healed,
                "last_ok_ts": s.last_ok_ts,
                "ts": s.ts,
            }
            for c, s in self._states.items()
        }
        overall = (worst_health(s.health for s in self._states.values())
                   if self._states else Health.UNKNOWN)
        return {
            "overall": overall.value,
            "ts": time.time(),
            "sentinel_tick": self._tick,
            "dry_run": True,                 # P0: observe-only, no repair actions
            "node_id": self._node_id,
            "components": comps,
        }
