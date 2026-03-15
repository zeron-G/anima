"""Note-taking tool — saves observations to data directory."""

from __future__ import annotations

import time
from pathlib import Path

from anima.config import data_dir
from anima.models.tool_spec import ToolSpec, RiskLevel


async def _save_note(title: str, content: str) -> str:
    """Save a note/observation."""
    notes_dir = data_dir() / "notes"
    notes_dir.mkdir(exist_ok=True)
    ts = int(time.time())
    safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)
    path = notes_dir / f"{ts}_{safe_title}.md"
    path.write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
    return f"Note saved: {path.name}"


def get_note_tool() -> ToolSpec:
    return ToolSpec(
        name="save_note",
        description="Save a note or observation for future reference",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Note title"},
                "content": {"type": "string", "description": "Note content"},
            },
            "required": ["title", "content"],
        },
        risk_level=RiskLevel.LOW,
        handler=_save_note,
    )
