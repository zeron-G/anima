"""Command risk assessment — evaluates shell commands before execution."""

from __future__ import annotations

import re
import shlex

from anima.models.tool_spec import RiskLevel

# Patterns that are always blocked
_BLOCKED_PATTERNS = [
    r"\brm\s+(-rf?|--recursive)\s+[/~]",  # rm -rf / or ~
    r"\bmkfs\b",
    r"\bdd\s+.*of=/dev/",
    r":\(\)\{.*\}",         # fork bomb
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bformat\b.*[A-Z]:",  # Windows format
    r"\bdel\s+/[sfq]",      # Windows del /s
]

# High risk patterns
_HIGH_RISK_PATTERNS = [
    r"\brm\s+-[a-zA-Z]*[rf]",     # rm with -r or -f flags
    r"\brmdir\b",
    r"\bchmod\s+777\b",
    r"\bchown\b",
    r"\bkill\s+-9\b",
    r"\bsudo\b",
    r">>\s*/etc/",
    r"\bgit\s+push\s+.*--force\b",
]

# Patterns checked against the FULL command (before pipe-split)
_HIGH_RISK_FULL_PATTERNS = [
    r"\bcurl\b.*\|\s*(sh|bash)",   # curl | sh
    r"\bwget\b.*\|\s*(sh|bash)",
]

# Medium risk patterns
_MEDIUM_RISK_PATTERNS = [
    r"\brm\b",
    r"\bmv\b",
    r"\bcp\b.*-r",
    r"\bgit\s+(push|reset|checkout)\b",
    r"\bpip\s+install\b",
    r"\bnpm\s+install\b",
    r">\s+\S+",  # output redirection
]


def assess_command_risk(command: str) -> RiskLevel:
    """Assess the risk level of a shell command.

    Uses pipe-split: evaluates each segment and returns the highest risk.
    """
    # Check full command patterns first (before pipe-split)
    for pattern in _HIGH_RISK_FULL_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return RiskLevel.HIGH

    # Split on pipes and evaluate each segment
    segments = command.split("|")
    max_risk = RiskLevel.SAFE

    for segment in segments:
        segment = segment.strip()
        risk = _assess_segment(segment)
        if risk > max_risk:
            max_risk = risk
        if max_risk == RiskLevel.BLOCKED:
            return RiskLevel.BLOCKED

    return max_risk


def _assess_segment(segment: str) -> RiskLevel:
    """Assess risk of a single command segment."""
    for pattern in _BLOCKED_PATTERNS:
        if re.search(pattern, segment, re.IGNORECASE):
            return RiskLevel.BLOCKED

    for pattern in _HIGH_RISK_PATTERNS:
        if re.search(pattern, segment, re.IGNORECASE):
            return RiskLevel.HIGH

    for pattern in _MEDIUM_RISK_PATTERNS:
        if re.search(pattern, segment, re.IGNORECASE):
            return RiskLevel.MEDIUM

    # Read-only / informational commands are safe
    read_only = {
        # Unix basics
        "ls", "dir", "cat", "head", "tail", "grep", "find", "echo",
        "pwd", "whoami", "date", "uname", "hostname", "id",
        # Process / system info
        "ps", "df", "du", "free", "uptime", "top", "htop", "lsof", "netstat",
        "ss", "ip", "ifconfig", "nslookup", "dig", "ping", "traceroute",
        # Text processing (read-only)
        "wc", "sort", "uniq", "cut", "tr", "awk", "sed", "diff", "comm",
        "less", "more", "file", "stat", "md5sum", "sha256sum",
        # Lookup
        "which", "where", "type", "whereis", "whatis", "man", "help",
        "env", "printenv", "set", "locale",
        # Version / info
        "python", "python3", "node", "npm", "git", "java", "go",
        "rustc", "cargo", "ruby", "perl",
        # Windows
        "ver", "systeminfo", "tasklist", "ipconfig", "tree",
    }
    try:
        parts = shlex.split(segment)
        if parts and parts[0] in read_only:
            # Some commands are safe only without certain flags
            # git is safe for read ops like status, log, diff, show
            if parts[0] == "git" and len(parts) > 1:
                git_safe = {"status", "log", "diff", "show", "branch",
                            "tag", "remote", "stash", "ls-files", "blame",
                            "shortlog", "describe", "rev-parse", "config"}
                if parts[1] in git_safe:
                    return RiskLevel.SAFE
                # git push/reset etc. are handled by MEDIUM patterns above
                return RiskLevel.LOW
            return RiskLevel.SAFE
    except ValueError:
        pass

    return RiskLevel.LOW
