"""Issue Tracker — JSON file-based issue tracking for ANIMA.

Provides lightweight issue tracking without external dependencies.
Issues are stored as individual JSON files in data/issues/.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path

from anima.config import data_dir
from anima.utils.ids import gen_id
from anima.utils.logging import get_logger

log = get_logger("issue_tracker")


class IssueStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    CLOSED = "closed"


class IssuePriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Issue:
    id: str
    title: str
    description: str = ""
    status: IssueStatus = IssueStatus.OPEN
    priority: IssuePriority = IssuePriority.MEDIUM
    labels: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    reporter: str = "eva"
    assignee: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    resolution: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["priority"] = self.priority.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Issue:
        d = d.copy()
        d["status"] = IssueStatus(d.get("status", "open"))
        d["priority"] = IssuePriority(d.get("priority", "medium"))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class IssueTracker:
    """File-based issue tracker using JSON files in data/issues/."""

    def __init__(self, issues_dir: Path | str | None = None):
        self._dir = Path(issues_dir) if issues_dir else data_dir() / "issues"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Issue] = {}
        self._load_all()

    def _issue_path(self, issue_id: str) -> Path:
        return self._dir / f"{issue_id}.json"

    def _load_all(self) -> None:
        for path in self._dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                issue = Issue.from_dict(data)
                self._cache[issue.id] = issue
            except Exception as e:
                log.warning("Failed to load issue %s: %s", path.name, e)

    def _save(self, issue: Issue) -> None:
        with open(self._issue_path(issue.id), "w", encoding="utf-8") as f:
            json.dump(issue.to_dict(), f, indent=2, ensure_ascii=False)

    def create(
        self,
        title: str,
        description: str = "",
        priority: str = "medium",
        labels: list[str] | None = None,
        files: list[str] | None = None,
        reporter: str = "eva",
    ) -> Issue:
        # Dedup: return existing open issue with same title+files instead of creating a duplicate
        files_list = files or []
        for existing in self._cache.values():
            if (
                existing.status == IssueStatus.OPEN
                and existing.title == title
                and existing.files == files_list
            ):
                existing.updated_at = time.time()
                self._save(existing)
                log.debug("Issue dedup: skipping duplicate of %s", existing.id)
                return existing

        issue = Issue(
            id=gen_id("iss"),
            title=title,
            description=description,
            priority=IssuePriority(priority),
            labels=labels or [],
            files=files_list,
            reporter=reporter,
        )
        self._cache[issue.id] = issue
        self._save(issue)
        log.info("Issue created: %s — %s", issue.id, title)
        return issue

    def get(self, issue_id: str) -> Issue | None:
        return self._cache.get(issue_id)

    def list_issues(
        self,
        status: str | None = None,
        priority: str | None = None,
        label: str | None = None,
        limit: int = 50,
    ) -> list[Issue]:
        issues = list(self._cache.values())
        if status:
            issues = [i for i in issues if i.status.value == status]
        if priority:
            issues = [i for i in issues if i.priority.value == priority]
        if label:
            issues = [i for i in issues if label in i.labels]
        issues.sort(key=lambda i: i.created_at, reverse=True)
        return issues[:limit]

    def update(self, issue_id: str, **kwargs) -> Issue | None:
        issue = self._cache.get(issue_id)
        if not issue:
            return None
        for key, value in kwargs.items():
            if key == "status":
                issue.status = IssueStatus(value)
            elif key == "priority":
                issue.priority = IssuePriority(value)
            elif hasattr(issue, key):
                setattr(issue, key, value)
        issue.updated_at = time.time()
        self._save(issue)
        return issue

    def close(self, issue_id: str, resolution: str = "") -> Issue | None:
        return self.update(issue_id, status="closed", resolution=resolution)

    def get_stats(self) -> dict:
        all_issues = list(self._cache.values())
        return {
            "total": len(all_issues),
            "open": sum(1 for i in all_issues if i.status == IssueStatus.OPEN),
            "in_progress": sum(1 for i in all_issues if i.status == IssueStatus.IN_PROGRESS),
            "closed": sum(1 for i in all_issues if i.status == IssueStatus.CLOSED),
        }
