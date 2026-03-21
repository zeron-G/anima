"""Startup dependency validation — fast-fail on missing critical dependencies.

Called from main.py before subsystem initialization. Checks:
  1. LLM API credentials available
  2. Semantic search backend (chromadb or sentence-transformers)
  3. Required agent files (identity, rules, config)
  4. Database file accessibility
  5. Python version compatibility

Returns a list of (severity, message) tuples:
  - "critical": process should not start
  - "warning": degraded but functional
  - "info": advisory
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from anima.utils.logging import get_logger

log = get_logger("startup_check")


def verify_dependencies(config: dict | None = None) -> list[tuple[str, str]]:
    """Run all startup checks. Returns list of (severity, message).

    Severity levels:
      - "critical": Must fix before running (process should exit)
      - "warning": Degraded functionality (process can run)
      - "info": Informational (no action needed)
    """
    issues: list[tuple[str, str]] = []

    _check_python_version(issues)
    _check_llm_credentials(issues)
    _check_semantic_search(issues)
    _check_required_files(issues)
    _check_database(issues, config)

    return issues


def _check_python_version(issues: list) -> None:
    if sys.version_info < (3, 11):
        issues.append(("critical", f"Python 3.11+ required, got {sys.version}"))


def _check_llm_credentials(issues: list) -> None:
    """Check for Anthropic API credentials."""
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    has_oauth = bool(os.environ.get("ANTHROPIC_OAUTH_TOKEN", "").strip())

    # Check Claude Code credentials file
    has_creds = False
    creds_path = Path.home() / ".claude" / ".credentials.json"
    if creds_path.exists():
        try:
            import json
            data = json.loads(creds_path.read_text(encoding="utf-8"))
            if data.get("claudeAiOauth", {}).get("accessToken"):
                has_creds = True
        except Exception:
            pass

    if not has_key and not has_oauth and not has_creds:
        issues.append((
            "critical",
            "No LLM credentials found. Set ANTHROPIC_API_KEY or ANTHROPIC_OAUTH_TOKEN, "
            "or log into Claude Code."
        ))


def _check_semantic_search(issues: list) -> None:
    """Check for semantic search backend.

    ChromaDB is required (critical). sentence-transformers is optional
    and used as a local embedding fallback.
    """
    try:
        import chromadb  # noqa: F401
    except ImportError:
        issues.append((
            "critical",
            "ChromaDB is required but not installed. "
            "Install: pip install chromadb"
        ))
        return

    has_st = False
    try:
        import sentence_transformers  # noqa: F401
        has_st = True
    except ImportError:
        pass

    if has_st:
        issues.append(("info", "Semantic search: ChromaDB + local sentence-transformers"))
    else:
        issues.append(("info", "Semantic search: ChromaDB (sentence-transformers not installed — optional)"))


def _check_required_files(issues: list) -> None:
    """Check for required agent configuration files."""
    from anima.config import project_root, agent_dir

    root = project_root()
    agent = agent_dir()

    required = [
        (agent / "identity" / "core.md", "Agent identity file"),
        (root / "config" / "default.yaml", "Default configuration"),
    ]

    for path, desc in required:
        if not path.exists():
            issues.append(("critical", f"Required file missing: {path} ({desc})"))

    # Check rules directory has at least one rule
    rules_dir = agent / "rules"
    if rules_dir.is_dir():
        rules = list(rules_dir.glob("*.md"))
        if not rules:
            issues.append(("warning", "No rule files in agents/eva/rules/ — behavioral constraints disabled"))
    else:
        issues.append(("warning", f"Rules directory missing: {rules_dir}"))


def _check_database(issues: list, config: dict | None) -> None:
    """Check database accessibility."""
    from anima.config import project_root

    db_path = "data/anima.db"
    if config:
        db_path = config.get("memory", {}).get("db_path", db_path)

    resolved = project_root() / db_path
    if resolved.exists():
        # Quick integrity check
        try:
            import sqlite3
            conn = sqlite3.connect(str(resolved))
            result = conn.execute("PRAGMA integrity_check").fetchone()
            conn.close()
            if result[0] != "ok":
                issues.append(("critical", f"Database integrity check failed: {result[0]}"))
        except Exception as e:
            issues.append(("warning", f"Database check failed: {e}"))
    else:
        issues.append(("info", f"Database will be created: {resolved}"))


def run_and_report(config: dict | None = None) -> bool:
    """Run checks and log results. Returns False if critical issues found."""
    issues = verify_dependencies(config)

    has_critical = False
    for severity, message in issues:
        if severity == "critical":
            log.error("STARTUP CHECK FAILED: %s", message)
            has_critical = True
        elif severity == "warning":
            log.warning("Startup warning: %s", message)
        else:
            log.info("Startup check: %s", message)

    if not issues or all(s != "critical" for s, _ in issues):
        log.info("All startup checks passed")

    return not has_critical
