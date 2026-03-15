"""Edit tool — precise string-based file editing."""

from __future__ import annotations

from pathlib import Path

from anima.models.tool_spec import ToolSpec, RiskLevel


async def _edit_file(file_path: str, old_string: str, new_string: str) -> dict[str, object]:
    """Replace an exact occurrence of *old_string* with *new_string* in a file.

    The old_string must appear exactly once in the file.  If it is missing or
    ambiguous (appears more than once), an error is returned so the caller can
    provide more context.
    """
    p = Path(file_path)
    if not p.exists():
        return {"success": False, "message": f"File not found: {file_path}"}
    if not p.is_file():
        return {"success": False, "message": f"Not a file: {file_path}"}

    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError) as exc:
        return {"success": False, "message": f"Cannot read file: {exc}"}

    count = content.count(old_string)

    if count == 0:
        return {
            "success": False,
            "message": "old_string not found in the file. Check for exact whitespace and indentation.",
        }

    if count > 1:
        return {
            "success": False,
            "message": (
                f"old_string appears {count} times in the file. "
                "Provide more surrounding context to make it unique."
            ),
        }

    if old_string == new_string:
        return {"success": False, "message": "old_string and new_string are identical; nothing to change."}

    new_content = content.replace(old_string, new_string, 1)

    try:
        p.write_text(new_content, encoding="utf-8")
    except (OSError, PermissionError) as exc:
        return {"success": False, "message": f"Cannot write file: {exc}"}

    # Build a brief description of the change
    old_lines = old_string.count("\n") + 1
    new_lines = new_string.count("\n") + 1
    return {
        "success": True,
        "message": f"Replaced {old_lines} line(s) with {new_lines} line(s) in {file_path}",
    }


def get_edit_tool() -> ToolSpec:
    return ToolSpec(
        name="edit_file",
        description=(
            "Edit a file by replacing an exact string with a new string. "
            "The old_string must appear exactly once in the file."
        ),
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file to edit",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to find (must be unique in the file)",
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement text",
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        },
        risk_level=RiskLevel.MEDIUM,
        handler=_edit_file,
    )
