"""Command risk assessment — evaluates shell commands before execution."""

from __future__ import annotations

import re
import shlex

from anima.models.tool_spec import RiskLevel

from anima.utils.logging import get_logger
log = get_logger("safety")

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
        "npm", "git", "rustc", "cargo",
        # Windows
        "ver", "systeminfo", "tasklist", "ipconfig", "tree",
    }
    try:
        parts = shlex.split(segment)
        if not parts:
            return RiskLevel.SAFE
        executable = _extract_executable(parts[0])
        if executable in read_only:
            if executable == "git":
                return _check_git_safety(parts)
            return RiskLevel.SAFE
        if executable in _INTERPRETER_EXECUTABLES:
            return _check_interpreter_safety(parts)
    except ValueError as e:
        log.debug("safety: shlex parse failed (treating as HIGH): %s", e)
        return RiskLevel.HIGH

    return RiskLevel.LOW


# ── Enhanced structural analysis (v2) ──

_GIT_SAFE_SUBCMDS: frozenset[str] = frozenset({
    "status", "log", "diff", "show", "branch", "tag", "remote",
    "stash", "ls-files", "blame", "shortlog", "describe", "rev-parse",
    "config",  # config is safe for READ, but dangerous flags checked below
})

_GIT_DANGEROUS_FLAGS: frozenset[str] = frozenset({
    "--exec", "--upload-pack", "--receive-pack",
    "core.pager", "core.editor", "core.fsmonitor",
    "alias.", "credential.", "http.proxy",
})

_INTERPRETER_EXECUTABLES: frozenset[str] = frozenset({
    "python", "python3", "node", "ruby", "perl", "java", "go",
})

_INTERPRETER_INLINE_FLAGS: frozenset[str] = frozenset({
    "-c", "-e",
})


def _extract_executable(token: str) -> str:
    """Extract bare executable name from a possibly absolute path.

    '/bin/rm' -> 'rm', 'C:\\Windows\\system32\\cmd.exe' -> 'cmd.exe'
    """
    # Handle both Unix and Windows path separators
    name = token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    # Remove .exe extension on Windows
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.lower()


def _check_interpreter_safety(tokens: list[str]) -> RiskLevel:
    """Assess risk of interpreter invocations (python, node, ruby, etc.)."""
    if len(tokens) < 2:
        return RiskLevel.SAFE  # bare 'python' / 'node' is safe

    for token in tokens[1:]:
        if token in _INTERPRETER_INLINE_FLAGS:
            return RiskLevel.HIGH
        if token == "-m":
            return RiskLevel.MEDIUM

    # Running a script file or other flags
    return RiskLevel.MEDIUM


def _check_git_safety(tokens: list[str]) -> RiskLevel:
    """Fine-grained git safety check: subcommand + flag analysis."""
    if len(tokens) < 2:
        return RiskLevel.SAFE  # bare 'git' is safe

    subcmd = tokens[1]

    # Check for dangerous flags across ALL arguments
    for token in tokens[2:]:
        for flag in _GIT_DANGEROUS_FLAGS:
            if flag in token.lower():
                log.debug("Git dangerous flag detected: %s in %s", flag, token)
                return RiskLevel.HIGH

    if subcmd in _GIT_SAFE_SUBCMDS:
        return RiskLevel.SAFE

    if subcmd in ("push", "reset", "checkout", "clean", "rebase"):
        return RiskLevel.MEDIUM

    return RiskLevel.LOW
