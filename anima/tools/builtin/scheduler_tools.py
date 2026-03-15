"""Scheduler tools — let the LLM manage scheduled/cron tasks."""

from __future__ import annotations

from anima.models.tool_spec import ToolSpec, RiskLevel

_scheduler = None


def set_scheduler(scheduler):
    global _scheduler
    _scheduler = scheduler


async def _schedule_job(name: str, cron_expr: str, prompt: str, recurring: bool = True) -> dict:
    """Schedule a recurring or one-shot task."""
    if _scheduler is None:
        return {"success": False, "error": "Scheduler not initialized"}
    job = _scheduler.add_job(name=name, cron_expr=cron_expr, prompt=prompt, recurring=recurring)
    return {"success": True, "result": job.to_dict()}


async def _list_jobs() -> dict:
    """List all scheduled jobs."""
    if _scheduler is None:
        return {"success": False, "error": "Scheduler not initialized"}
    return {"success": True, "result": _scheduler.list_jobs()}


async def _cancel_job(job_id: str) -> dict:
    """Cancel a scheduled job."""
    if _scheduler is None:
        return {"success": False, "error": "Scheduler not initialized"}
    removed = _scheduler.remove_job(job_id)
    if removed:
        return {"success": True, "result": f"Job {job_id} cancelled"}
    return {"success": False, "error": f"Job {job_id} not found"}


async def _enable_job(job_id: str, enabled: bool = True) -> dict:
    """Enable or disable a scheduled job."""
    if _scheduler is None:
        return {"success": False, "error": "Scheduler not initialized"}
    ok = _scheduler.enable_job(job_id, enabled)
    if ok:
        action = "enabled" if enabled else "disabled"
        return {"success": True, "result": f"Job {job_id} {action}"}
    return {"success": False, "error": f"Job {job_id} not found"}


def get_scheduler_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="schedule_job",
            description="Schedule a recurring or one-shot cron task. The prompt will be sent to the LLM when it fires.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Human-readable job name"},
                    "cron_expr": {"type": "string", "description": "Cron expression (e.g. '*/5 * * * *' for every 5 min, '0 9 * * *' for daily at 9am)"},
                    "prompt": {"type": "string", "description": "What to tell the LLM when this job fires"},
                    "recurring": {"type": "boolean", "description": "True for recurring, False for one-shot", "default": True},
                },
                "required": ["name", "cron_expr", "prompt"],
            },
            risk_level=RiskLevel.LOW,
            handler=_schedule_job,
        ),
        ToolSpec(
            name="list_jobs",
            description="List all scheduled cron jobs",
            parameters={"type": "object", "properties": {}},
            risk_level=RiskLevel.SAFE,
            handler=_list_jobs,
        ),
        ToolSpec(
            name="cancel_job",
            description="Cancel/remove a scheduled job by ID",
            parameters={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The job ID to cancel"},
                },
                "required": ["job_id"],
            },
            risk_level=RiskLevel.LOW,
            handler=_cancel_job,
        ),
        ToolSpec(
            name="enable_job",
            description="Enable or disable a scheduled job",
            parameters={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The job ID"},
                    "enabled": {"type": "boolean", "description": "True to enable, False to disable", "default": True},
                },
                "required": ["job_id"],
            },
            risk_level=RiskLevel.LOW,
            handler=_enable_job,
        ),
    ]
