"""Startup dependency validation — fast-fail on missing critical dependencies.

Called from main.py before subsystem initialization. Checks:
  1. LLM API credentials available
  2. Semantic search (OpenAI embeddings for pgvector)
  3. Required agent files (identity, rules, config)
  4. Postgres data layer (driver + endpoint config + failover topology)
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
    _check_llm_credentials(issues, config)
    _check_semantic_search(issues)
    _check_required_files(issues)
    _check_database(issues, config)

    return issues


def _check_python_version(issues: list) -> None:
    if sys.version_info < (3, 11):
        issues.append(("critical", f"Python 3.11+ required, got {sys.version}"))


def _check_llm_credentials(issues: list, config: dict | None = None) -> None:
    """Check whether this runtime has enough LLM configuration to start."""
    runtime_cfg = (config or {}).get("runtime", {})
    llm_cfg = (config or {}).get("llm", {})
    require_llm = bool(runtime_cfg.get("require_llm_credentials", True))

    openai_cfg = llm_cfg.get("openai_fallback", {}) if isinstance(llm_cfg, dict) else {}
    tier1_model = str((llm_cfg.get("tier1", {}) or {}).get("model", ""))
    tier2_model = str((llm_cfg.get("tier2", {}) or {}).get("model", ""))

    from anima.secret_store import get_secret
    has_openai_key = bool(get_secret("OPENAI_API_KEY"))
    has_openai_model = bool(str(openai_cfg.get("model", "")).strip())
    has_openai_route = has_openai_key and (
        has_openai_model or tier1_model.startswith("openai/") or tier2_model.startswith("openai/")
    )

    has_key = bool(get_secret("ANTHROPIC_API_KEY"))
    has_oauth = bool(get_secret("ANTHROPIC_OAUTH_TOKEN"))
    has_creds = _has_claude_code_credentials()

    if has_key or has_oauth or has_creds or has_openai_route:
        return

    if require_llm:
        issues.append((
            "critical",
            "No LLM credentials found. Set ANTHROPIC_API_KEY or ANTHROPIC_OAUTH_TOKEN, "
            "or log into Claude Code."
        ))
        return

    issues.append((
        "warning",
        "No LLM credentials found. Continuing in degraded runtime mode; "
        "LLM-backed conversation and planning will stay unavailable until credentials are configured."
    ))


def _has_claude_code_credentials() -> bool:
    """Whether Claude Code OAuth credentials are available locally."""
    creds_path = Path.home() / ".claude" / ".credentials.json"
    if not creds_path.exists():
        return False

    try:
        import json
        data = json.loads(creds_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    return bool(data.get("claudeAiOauth", {}).get("accessToken"))


def _check_semantic_search(issues: list) -> None:
    """Semantic recall uses pgvector with OpenAI embeddings (text-embedding-3-small)."""
    from anima.secret_store import get_secret

    if get_secret("OPENAI_API_KEY"):
        issues.append(("info", "Semantic search: pgvector + OpenAI text-embedding-3-small"))
    else:
        issues.append((
            "warning",
            "OPENAI_API_KEY unset — embeddings disabled; semantic recall falls "
            "back to keyword search (ILIKE)",
        ))


def _check_required_files(issues: list) -> None:
    """Check for required agent configuration files."""
    from anima.config import config_dir, agent_dir

    agent = agent_dir()

    required = [
        (agent / "identity" / "core.md", "Agent identity file"),
        (config_dir() / "default.yaml", "Default configuration"),
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
    """Check the Postgres data layer: driver present + an endpoint configured.

    The actual connection (and Neon→local failover) happens in
    PgMemoryStore.create, which logs which endpoint it reached — we keep this
    check lightweight so startup isn't blocked on a network round-trip.
    """
    from anima.secret_store import get_secret

    try:
        import psycopg  # noqa: F401
        import pgvector  # noqa: F401
    except ImportError:
        issues.append((
            "critical",
            "psycopg/pgvector not installed. Install: pip install 'psycopg[binary]' pgvector",
        ))
        return

    primary = bool(get_secret("DATABASE_URL"))
    local = bool(get_secret("LOCAL_DATABASE_URL"))
    if not (primary or local):
        issues.append((
            "critical",
            "No Postgres configured — set DATABASE_URL (Neon) and/or "
            "LOCAL_DATABASE_URL in .env",
        ))
    elif primary and local:
        issues.append(("info", "Postgres: Neon primary + local failover configured"))
    elif primary:
        issues.append((
            "warning",
            "Postgres: Neon primary only — no LOCAL_DATABASE_URL failover for offline use",
        ))
    else:
        issues.append(("info", "Postgres: local only (no Neon primary)"))


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
