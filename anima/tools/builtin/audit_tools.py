"""Audit tools — bridge between cognitive loop and self-audit + issue tracker.

Three tools:
  - audit_run: Run a specific audit tier
  - audit_status: Get audit system status
  - issue_manage: Create/update/list/close issues
"""

from __future__ import annotations

from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger

log = get_logger("tools.audit")

# Set by main.py during wiring
_self_audit = None
_issue_tracker = None


def set_audit_deps(self_audit, issue_tracker) -> None:
    global _self_audit, _issue_tracker
    _self_audit = self_audit
    _issue_tracker = issue_tracker


async def _audit_run(tier: int = 1) -> dict:
    """Run a specific self-audit tier (1-4)."""
    if not _self_audit:
        return {"error": "Self-audit system not initialized"}
    if tier not in (1, 2, 3, 4):
        return {"error": f"Invalid tier: {tier}. Must be 1-4."}
    result = await _self_audit.run_tier(tier)
    return result.to_dict()


async def _audit_status() -> dict:
    """Get current audit system status."""
    status = {}
    if _self_audit:
        status["audit"] = _self_audit.get_status()
    if _issue_tracker:
        status["issues"] = _issue_tracker.get_stats()
    return status or {"error": "Audit system not initialized"}


async def _issue_manage(
    action: str = "list",
    issue_id: str = "",
    title: str = "",
    description: str = "",
    priority: str = "medium",
    status: str = "",
    labels: str = "",
    resolution: str = "",
) -> dict:
    """Manage issues: create, list, update, close, get."""
    if not _issue_tracker:
        return {"error": "Issue tracker not initialized"}

    if action == "list":
        issues = _issue_tracker.list_issues(
            status=status or None,
            priority=priority if priority != "medium" else None,
            limit=20,
        )
        return {
            "issues": [i.to_dict() for i in issues],
            "stats": _issue_tracker.get_stats(),
        }

    if action == "create":
        if not title:
            return {"error": "title is required for create"}
        label_list = [la.strip() for la in labels.split(",") if la.strip()] if labels else []
        issue = _issue_tracker.create(
            title=title,
            description=description,
            priority=priority,
            labels=label_list,
        )
        return {"created": issue.to_dict()}

    if action == "get":
        if not issue_id:
            return {"error": "issue_id is required for get"}
        issue = _issue_tracker.get(issue_id)
        return {"issue": issue.to_dict()} if issue else {"error": f"Issue {issue_id} not found"}

    if action == "update":
        if not issue_id:
            return {"error": "issue_id is required for update"}
        kwargs = {}
        if status:
            kwargs["status"] = status
        if priority:
            kwargs["priority"] = priority
        if title:
            kwargs["title"] = title
        if description:
            kwargs["description"] = description
        issue = _issue_tracker.update(issue_id, **kwargs)
        return {"updated": issue.to_dict()} if issue else {"error": f"Issue {issue_id} not found"}

    if action == "close":
        if not issue_id:
            return {"error": "issue_id is required for close"}
        issue = _issue_tracker.close(issue_id, resolution=resolution)
        return {"closed": issue.to_dict()} if issue else {"error": f"Issue {issue_id} not found"}

    return {"error": f"Unknown action: {action}. Use: list, create, get, update, close"}


def get_audit_tools() -> list[ToolSpec]:
    """Return all audit-related tools."""
    return [
        ToolSpec(
            name="audit_run",
            description=(
                "Run a self-audit tier. "
                "Tier 1: Static analysis (ruff + bandit). "
                "Tier 2: Test suite check. "
                "Tier 3: Dependency security audit. "
                "Tier 4: Open issue review (dispatches to cognitive loop)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tier": {"type": "integer", "description": "Audit tier (1-4)",
                             "enum": [1, 2, 3, 4]},
                },
                "required": ["tier"],
            },
            risk_level=RiskLevel.LOW,
            handler=_audit_run,
        ),
        ToolSpec(
            name="audit_status",
            description="Get current self-audit status and issue tracker statistics.",
            parameters={"type": "object", "properties": {}},
            risk_level=RiskLevel.SAFE,
            handler=_audit_status,
        ),
        ToolSpec(
            name="issue_manage",
            description=(
                "Manage issues: create, list, get, update, or close issues. "
                "Issues track bugs, improvements, and audit findings."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["list", "create", "get", "update", "close"],
                               "description": "Action to perform"},
                    "issue_id": {"type": "string",
                                 "description": "Issue ID (for get/update/close)"},
                    "title": {"type": "string",
                              "description": "Issue title (for create/update)"},
                    "description": {"type": "string",
                                    "description": "Issue description"},
                    "priority": {"type": "string",
                                 "enum": ["low", "medium", "high", "critical"],
                                 "description": "Priority level"},
                    "status": {"type": "string",
                               "enum": ["open", "in_progress", "closed"],
                               "description": "Filter by status (list) or set status (update)"},
                    "labels": {"type": "string",
                               "description": "Comma-separated labels (for create)"},
                    "resolution": {"type": "string",
                                   "description": "Resolution note (for close)"},
                },
                "required": ["action"],
            },
            risk_level=RiskLevel.LOW,
            handler=_issue_manage,
        ),
    ]
