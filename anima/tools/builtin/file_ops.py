"""File operation tools — read and write files."""

from __future__ import annotations

from pathlib import Path

from anima.models.tool_spec import ToolSpec, RiskLevel


async def _read_file(path: str, max_lines: int = 200) -> str:
    """Read file contents."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not p.is_file():
        raise ValueError(f"Not a file: {path}")
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + f"\n... ({len(lines) - max_lines} more lines)"
    return text


async def _write_file(path: str, content: str) -> str:
    """Write content to a file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Written {len(content)} bytes to {path}"


async def _list_directory(path: str = ".") -> list[str]:
    """List files in a directory."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Directory not found: {path}")
    return sorted(str(item.relative_to(p)) for item in p.iterdir())


def get_file_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="read_file",
            description="Read the contents of a file",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"},
                    "max_lines": {"type": "integer", "default": 200},
                },
                "required": ["path"],
            },
            risk_level=RiskLevel.SAFE,
            handler=_read_file,
        ),
        ToolSpec(
            name="write_file",
            description="Write content to a file",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
            risk_level=RiskLevel.MEDIUM,
            handler=_write_file,
        ),
        ToolSpec(
            name="list_directory",
            description="List files in a directory",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                },
            },
            risk_level=RiskLevel.SAFE,
            handler=_list_directory,
        ),
    ]
