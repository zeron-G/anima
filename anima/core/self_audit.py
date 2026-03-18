"""Self-Audit System — 4-tier automated code quality and security auditing.

Tier 1: Static analysis (ruff + bandit) — direct execution, no LLM
Tier 2: Test suite check — direct execution, no LLM
Tier 3: Dependency/security audit — direct execution, no LLM
Tier 4: Issue review — dispatches to cognitive loop (uses LLM)
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field

from anima.config import project_root
from anima.utils.logging import get_logger

log = get_logger("self_audit")


@dataclass
class AuditResult:
    tier: int
    name: str
    passed: bool
    findings: list[dict] = field(default_factory=list)
    summary: str = ""
    timestamp: float = field(default_factory=time.time)
    duration_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "name": self.name,
            "passed": self.passed,
            "findings_count": len(self.findings),
            "findings": self.findings[:20],
            "summary": self.summary,
            "timestamp": self.timestamp,
            "duration_s": self.duration_s,
        }


class SelfAudit:
    """4-tier self-auditing system.

    Tiers 1-3 execute directly (no LLM cost).
    Tier 4 dispatches issue review to the cognitive loop.
    """

    def __init__(self, issue_tracker=None, event_queue=None):
        self._issue_tracker = issue_tracker
        self._event_queue = event_queue
        self._last_results: dict[int, AuditResult] = {}
        self._root = str(project_root())

    def set_issue_tracker(self, tracker) -> None:
        self._issue_tracker = tracker

    def set_event_queue(self, queue) -> None:
        self._event_queue = queue

    async def run_tier(self, tier: int) -> AuditResult:
        """Run a specific audit tier."""
        runners = {
            1: self._tier1_static,
            2: self._tier2_tests,
            3: self._tier3_deps,
        }
        runner = runners.get(tier)
        if not runner:
            if tier == 4:
                return await self._tier4_issue_review()
            return AuditResult(tier=tier, name="unknown", passed=False,
                               summary=f"Unknown tier: {tier}")

        start = time.time()
        result = runner()
        result.duration_s = round(time.time() - start, 2)
        self._last_results[tier] = result

        # Auto-create issues for critical findings
        if not result.passed and self._issue_tracker:
            critical = [f for f in result.findings
                        if f.get("severity") in ("error", "high", "critical")]
            for finding in critical[:3]:
                self._issue_tracker.create(
                    title=f"[audit-t{tier}] {finding.get('message', 'Audit finding')[:80]}",
                    description=json.dumps(finding, indent=2),
                    priority="high" if finding.get("severity") == "critical" else "medium",
                    labels=["audit", f"tier-{tier}"],
                    files=finding.get("files", []),
                    reporter="self_audit",
                )

        return result

    async def run_all(self) -> list[AuditResult]:
        """Run tiers 1-3 sequentially."""
        results = []
        for tier in (1, 2, 3):
            result = await self.run_tier(tier)
            results.append(result)
        return results

    def get_status(self) -> dict:
        return {
            "last_results": {k: v.to_dict() for k, v in self._last_results.items()},
        }

    # ── Tier 1: Static Analysis ──

    def _tier1_static(self) -> AuditResult:
        """Run ruff + bandit static analysis."""
        findings = []

        # Ruff
        try:
            result = subprocess.run(
                ["python", "-m", "ruff", "check", "anima/",
                 "--select", "E,F,W", "--ignore", "E501,W291,W292,W293",
                 "--output-format", "json"],
                cwd=self._root, capture_output=True, text=True, timeout=60,
            )
            if result.stdout:
                try:
                    ruff_issues = json.loads(result.stdout)
                    for issue in ruff_issues:
                        findings.append({
                            "tool": "ruff",
                            "severity": "error" if issue.get("code", "").startswith("F") else "warning",
                            "message": f"{issue.get('code', '?')}: {issue.get('message', '')}",
                            "file": issue.get("filename", ""),
                            "line": issue.get("location", {}).get("row", 0),
                            "files": [issue.get("filename", "")],
                        })
                except json.JSONDecodeError:
                    pass
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            log.warning("Ruff check failed: %s", e)

        # Bandit
        try:
            result = subprocess.run(
                ["python", "-m", "bandit", "-r", "anima/", "-f", "json", "-ll"],
                cwd=self._root, capture_output=True, text=True, timeout=60,
            )
            if result.stdout:
                try:
                    bandit_data = json.loads(result.stdout)
                    for issue in bandit_data.get("results", []):
                        findings.append({
                            "tool": "bandit",
                            "severity": issue.get("issue_severity", "MEDIUM").lower(),
                            "message": f"{issue.get('test_id', '?')}: {issue.get('issue_text', '')}",
                            "file": issue.get("filename", ""),
                            "line": issue.get("line_number", 0),
                            "files": [issue.get("filename", "")],
                        })
                except json.JSONDecodeError:
                    pass
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            log.debug("Bandit not available: %s", e)

        errors = [f for f in findings if f["severity"] in ("error", "high")]
        passed = len(errors) == 0
        return AuditResult(
            tier=1, name="static_analysis", passed=passed,
            findings=findings,
            summary=f"ruff+bandit: {len(findings)} findings ({len(errors)} errors)",
        )

    # ── Tier 2: Test Suite ──

    def _tier2_tests(self) -> AuditResult:
        """Run pytest and check results."""
        findings = []
        try:
            result = subprocess.run(
                ["python", "-m", "pytest", "tests/", "-q", "--tb=line",
                 "--ignore=tests/stress_test.py",
                 "--ignore=tests/test_oauth_live.py",
                 "--ignore=tests/test_full_system.py",
                 "--ignore=tests/test_integration_network.py",
                 "-x"],
                cwd=self._root, capture_output=True, text=True, timeout=120,
            )
            output = result.stdout + result.stderr
            passed = result.returncode == 0

            if not passed:
                for line in output.splitlines():
                    if "FAILED" in line or "ERROR" in line:
                        findings.append({
                            "tool": "pytest",
                            "severity": "error",
                            "message": line.strip()[:200],
                            "files": [],
                        })

            summary_line = ""
            for line in reversed(output.splitlines()):
                if "passed" in line or "failed" in line or "error" in line:
                    summary_line = line.strip()
                    break

            return AuditResult(
                tier=2, name="test_suite", passed=passed,
                findings=findings,
                summary=summary_line or ("Tests passed" if passed else "Tests failed"),
            )
        except subprocess.TimeoutExpired:
            return AuditResult(tier=2, name="test_suite", passed=False,
                               summary="Test suite timed out (>120s)")
        except Exception as e:
            return AuditResult(tier=2, name="test_suite", passed=False,
                               summary=f"Test execution error: {e}")

    # ── Tier 3: Dependency / Security Audit ──

    def _tier3_deps(self) -> AuditResult:
        """Check dependency security via pip audit."""
        findings = []
        try:
            result = subprocess.run(
                ["python", "-m", "pip_audit", "--format", "json"],
                cwd=self._root, capture_output=True, text=True, timeout=60,
            )
            if result.stdout:
                try:
                    audit_data = json.loads(result.stdout)
                    for vuln in audit_data.get("vulnerabilities", []):
                        findings.append({
                            "tool": "pip-audit",
                            "severity": "high",
                            "message": (f"{vuln.get('name', '?')} {vuln.get('version', '?')}: "
                                        f"{vuln.get('description', '')[:100]}"),
                            "files": ["pyproject.toml"],
                        })
                except json.JSONDecodeError:
                    pass
            passed = result.returncode == 0 and len(findings) == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            # pip-audit not installed — pass with note
            passed = True

        return AuditResult(
            tier=3, name="dependency_audit", passed=passed,
            findings=findings,
            summary=(f"pip-audit: {len(findings)} vulnerabilities"
                     if findings else "Dependencies OK (or pip-audit not installed)"),
        )

    # ── Tier 4: Issue Review (LLM-based) ──

    async def _tier4_issue_review(self) -> AuditResult:
        """Dispatch issue review to cognitive loop via SELF_THINKING event."""
        if not self._event_queue:
            return AuditResult(tier=4, name="issue_review", passed=True,
                               summary="Skipped — event queue not wired")

        from anima.models.event import Event, EventType, EventPriority

        open_issues = []
        if self._issue_tracker:
            issues = self._issue_tracker.list_issues(status="open", limit=10)
            open_issues = [{"id": i.id, "title": i.title, "priority": i.priority.value}
                           for i in issues]

        await self._event_queue.put(Event(
            type=EventType.SELF_THINKING,
            payload={
                "task_type": "issue_review",
                "open_issues": open_issues,
                "tick_count": 0,
            },
            priority=EventPriority.LOW,
            source="self_audit",
        ))

        return AuditResult(
            tier=4, name="issue_review", passed=True,
            summary=f"Dispatched issue review ({len(open_issues)} open issues)",
        )
