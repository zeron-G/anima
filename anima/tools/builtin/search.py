"""Search tools — glob file search and grep content search."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from anima.models.tool_spec import ToolSpec, RiskLevel

_SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv", ".egg-info"}
_MAX_GLOB_RESULTS = 500


def _should_skip(path: Path) -> bool:
    """Return True if any path component is in the skip set."""
    return any(part in _SKIP_DIRS for part in path.parts)


def _is_binary(filepath: Path, check_bytes: int = 1024) -> bool:
    """Heuristic: file is binary if its first chunk contains null bytes."""
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(check_bytes)
        return b"\x00" in chunk
    except (OSError, PermissionError):
        return True


# ---------------------------------------------------------------------------
# glob_search
# ---------------------------------------------------------------------------

async def _glob_search(pattern: str, path: str = ".") -> list[str]:
    """Search for files matching a glob pattern.

    Returns matching file paths sorted by modification time (newest first).
    """
    base = Path(path).resolve()
    if not base.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    # Use rglob for patterns starting with **/, otherwise glob
    matches: list[Path] = []
    for p in base.glob(pattern):
        if p.is_file() and not _should_skip(p):
            matches.append(p)
            if len(matches) >= _MAX_GLOB_RESULTS:
                break

    # Sort by modification time, newest first
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [str(p) for p in matches]


# ---------------------------------------------------------------------------
# grep_search
# ---------------------------------------------------------------------------

async def _grep_search(
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    context: int = 0,
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """Search file contents using a regex pattern.

    Returns a list of matches with file, line number, content, and optional
    context lines.
    """
    base = Path(path).resolve()
    if not base.exists():
        raise FileNotFoundError(f"Path not found: {path}")

    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid regex pattern: {exc}") from exc

    results: list[dict[str, Any]] = []

    # Collect files to search
    if base.is_file():
        files = [base]
    elif glob:
        files = [p for p in base.rglob(glob) if p.is_file() and not _should_skip(p)]
    else:
        files = [p for p in base.rglob("*") if p.is_file() and not _should_skip(p)]

    for filepath in files:
        if _is_binary(filepath):
            continue
        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, PermissionError):
            continue

        lines = text.splitlines()
        for i, line in enumerate(lines):
            if regex.search(line):
                match: dict[str, Any] = {
                    "file": str(filepath),
                    "line": i + 1,
                    "content": line,
                }
                if context > 0:
                    start = max(0, i - context)
                    end = min(len(lines), i + context + 1)
                    match["context_before"] = lines[start:i]
                    match["context_after"] = lines[i + 1 : end]
                results.append(match)
                if len(results) >= max_results:
                    return results

    return results


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def get_search_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="glob_search",
            description=(
                "Search for files matching a glob pattern (e.g. '**/*.py'). "
                "Returns paths sorted by modification time, newest first."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern, e.g. '**/*.py' or 'src/**/*.ts'",
                    },
                    "path": {
                        "type": "string",
                        "description": "Base directory to search in",
                        "default": ".",
                    },
                },
                "required": ["pattern"],
            },
            risk_level=RiskLevel.SAFE,
            handler=_glob_search,
        ),
        ToolSpec(
            name="grep_search",
            description=(
                "Search file contents using a regex pattern. "
                "Returns matching lines with file path, line number, and optional context."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search in",
                        "default": ".",
                    },
                    "glob": {
                        "type": "string",
                        "description": "File filter pattern, e.g. '*.py'",
                    },
                    "context": {
                        "type": "integer",
                        "description": "Number of context lines before and after each match",
                        "default": 0,
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matches to return",
                        "default": 50,
                    },
                },
                "required": ["pattern"],
            },
            risk_level=RiskLevel.SAFE,
            handler=_grep_search,
        ),
    ]
