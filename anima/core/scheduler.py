"""Cron scheduler — time-based task scheduling integrated with heartbeat.

Jobs are defined as cron expressions. When a job fires, it pushes a
SCHEDULED_TASK event to the event queue, which the cognitive loop processes
with full LLM + tool access.

Jobs persist to data/scheduler.json so they survive restarts.
"""

import json
import time
from dataclasses import dataclass, field

from anima.config import data_dir
from anima.utils.ids import gen_id
from anima.utils.logging import get_logger

log = get_logger("scheduler")


@dataclass
class CronJob:
    id: str = field(default_factory=lambda: gen_id("cron"))
    name: str = ""
    cron_expr: str = ""        # "*/5 * * * *" or "0 9 * * *"
    prompt: str = ""           # What to tell the LLM when this fires
    enabled: bool = True
    recurring: bool = True     # False = one-shot (fires once then disables)
    timeout_s: int = 120
    last_run: float = 0
    next_run: float = 0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "cron_expr": self.cron_expr,
            "prompt": self.prompt[:200], "enabled": self.enabled,
            "recurring": self.recurring, "timeout_s": self.timeout_s,
            "last_run": self.last_run, "next_run": self.next_run,
        }


class Scheduler:
    """Cron-compatible job scheduler.

    Checks every heartbeat tick (15s) if any jobs need to fire.
    When a job fires, pushes SCHEDULED_TASK event to the event queue.
    Jobs persist to data/scheduler.json.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, CronJob] = {}
        self._persist_path = data_dir() / "scheduler.json"
        self._load()

    def add_job(self, name: str, cron_expr: str, prompt: str,
                recurring: bool = True, timeout_s: int = 120) -> CronJob:
        """Add a new scheduled job. Deduplicates by name — skips if exists."""
        # Dedup: if a job with the same name already exists, skip
        for existing in self._jobs.values():
            if existing.name == name:
                return existing
        job = CronJob(name=name, cron_expr=cron_expr, prompt=prompt,
                      recurring=recurring, timeout_s=timeout_s)
        job.next_run = self._calc_next_run(cron_expr)
        self._jobs[job.id] = job
        self._save()
        log.info("Job added: %s (%s) -> %s", name, cron_expr, job.id)
        return job

    def remove_job(self, job_id: str) -> bool:
        if job_id in self._jobs:
            del self._jobs[job_id]
            self._save()
            return True
        return False

    def enable_job(self, job_id: str, enabled: bool = True) -> bool:
        job = self._jobs.get(job_id)
        if job:
            job.enabled = enabled
            if enabled:
                job.next_run = self._calc_next_run(job.cron_expr)
            self._save()
            return True
        return False

    def get_due_jobs(self) -> list[CronJob]:
        """Check which jobs are due to run NOW. Called by heartbeat every 15s."""
        now = time.time()
        due = []
        for job in self._jobs.values():
            if not job.enabled:
                continue
            if now >= job.next_run:
                due.append(job)
                job.last_run = now
                if job.recurring:
                    job.next_run = self._calc_next_run(job.cron_expr)
                else:
                    job.enabled = False
        if due:
            self._save()
        return due

    def list_jobs(self) -> list[dict]:
        return [j.to_dict() for j in self._jobs.values()]

    def _calc_next_run(self, cron_expr: str) -> float:
        """Calculate the next run time from a cron expression.

        Supports: */N * * * * (every N minutes), 0 H * * * (daily at hour H),
        and basic 5-field cron.

        For a full implementation, use croniter. For now, implement the
        most common patterns directly.
        """
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            log.warning("Invalid cron expression: %s, defaulting to 1h", cron_expr)
            return time.time() + 3600

        import datetime
        now = datetime.datetime.now()
        minute, hour, dom, month, dow = parts

        # Pattern: */N * * * * (every N minutes)
        if minute.startswith("*/") and hour == "*":
            interval = int(minute[2:])
            # Next minute that's a multiple of interval
            next_min = ((now.minute // interval) + 1) * interval
            if next_min >= 60:
                target = now.replace(minute=next_min % 60, second=0, microsecond=0) + datetime.timedelta(hours=1)
            else:
                target = now.replace(minute=next_min, second=0, microsecond=0)
            if target <= now:
                target += datetime.timedelta(minutes=interval)
            return target.timestamp()

        # Pattern: M H * * * (daily at specific time)
        if minute.isdigit() and hour.isdigit() and dom == "*":
            target = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
            if target <= now:
                target += datetime.timedelta(days=1)
            return target.timestamp()

        # Fallback: 1 hour from now
        return time.time() + 3600

    def _save(self) -> None:
        data = {"jobs": []}
        for job in self._jobs.values():
            data["jobs"].append({
                "id": job.id, "name": job.name, "cron_expr": job.cron_expr,
                "prompt": job.prompt, "enabled": job.enabled,
                "recurring": job.recurring, "timeout_s": job.timeout_s,
                "last_run": job.last_run, "next_run": job.next_run,
                "created_at": job.created_at,
            })
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        self._persist_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load(self) -> None:
        if not self._persist_path.exists():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            for jd in data.get("jobs", []):
                job = CronJob(
                    id=jd["id"], name=jd.get("name", ""), cron_expr=jd.get("cron_expr", ""),
                    prompt=jd.get("prompt", ""), enabled=jd.get("enabled", True),
                    recurring=jd.get("recurring", True), timeout_s=jd.get("timeout_s", 120),
                    last_run=jd.get("last_run", 0), next_run=jd.get("next_run", 0),
                    created_at=jd.get("created_at", time.time()),
                )
                self._jobs[job.id] = job
            log.info("Loaded %d scheduled jobs", len(self._jobs))
        except Exception as e:
            log.error("Failed to load scheduler state: %s", e)
