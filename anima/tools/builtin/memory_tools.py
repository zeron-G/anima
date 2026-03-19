"""Memory self-edit tools — let Eva update her own feelings and user profile.

Inspired by Letta's core_memory_append/core_memory_replace pattern.
Includes version backup (.bak files) and audit logging.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from anima.config import agent_dir, data_dir
from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger
from anima.utils.path_safety import validate_path_within

log = get_logger("memory_tools")


# ------------------------------------------------------------------ #
#  Module-level dependency injection                                   #
# ------------------------------------------------------------------ #

_memory_store: Any = None
_prompt_compiler: Any = None


def set_memory_deps(memory_store: Any, prompt_compiler: Any) -> None:
    """Inject runtime dependencies (same pattern as other builtin tools).

    Parameters
    ----------
    memory_store:
        ``MemoryStore`` instance with an ``audit()`` method.
    prompt_compiler:
        ``PromptCompiler`` instance with ``refresh_feelings_cache()``.
    """
    global _memory_store, _prompt_compiler
    _memory_store = memory_store
    _prompt_compiler = prompt_compiler
    log.info("Memory tool dependencies injected")


# ------------------------------------------------------------------ #
#  Backup helpers                                                      #
# ------------------------------------------------------------------ #

_MAX_BACKUPS = 10


def _create_backup(file_path: Path) -> str:
    """Copy *file_path* to ``{name}.{YYYYMMDD_HHMMSS}.bak``.

    Keeps at most ``_MAX_BACKUPS`` backups — deletes the oldest when
    the limit is exceeded.

    Returns
    -------
    The backup filename (relative name, not full path).
    """
    if not file_path.exists():
        return ""

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{file_path.name}.{ts}.bak"
    backup_path = file_path.parent / backup_name

    shutil.copy2(file_path, backup_path)
    log.debug("Backup created: %s", backup_path)

    # Prune old backups
    _prune_backups(file_path)

    return backup_name


def _prune_backups(original: Path) -> None:
    """Keep only the most recent ``_MAX_BACKUPS`` backups for *original*."""
    pattern = f"{original.name}.*.bak"
    backups = sorted(
        original.parent.glob(pattern),
        key=lambda p: p.stat().st_mtime,
    )
    while len(backups) > _MAX_BACKUPS:
        oldest = backups.pop(0)
        try:
            oldest.unlink()
            log.debug("Pruned old backup: %s", oldest.name)
        except OSError as exc:
            log.warning("Failed to prune backup %s: %s", oldest.name, exc)


def _audit(action: str, details: str) -> None:
    """Write to the audit log if memory_store is available."""
    if _memory_store is not None and hasattr(_memory_store, "audit"):
        try:
            _memory_store.audit(action=action, details=details)
        except Exception as exc:
            log.warning("Audit log write failed: %s", exc)


# ------------------------------------------------------------------ #
#  Tool: update_feelings                                               #
# ------------------------------------------------------------------ #

async def _update_feelings(content: str, action: str = "append") -> dict:
    """Update Eva's feelings file.

    Parameters
    ----------
    content:
        Text to write.  For ``action="append"`` this is added as a dated
        entry at the end.  For ``action="replace"`` this replaces the
        entire file.
    action:
        ``"append"`` (default) or ``"replace"``.

    Returns
    -------
    ``{"success": bool, "message": str, "backup": str}``
    """
    # Resolve feelings path (try memory/feelings.md, then root feelings.md)
    agent = agent_dir()
    feelings_path = agent / "memory" / "feelings.md"
    if not feelings_path.exists():
        feelings_path = agent / "feelings.md"

    try:
        validate_path_within(feelings_path, agent)
    except Exception:
        return {"success": False, "error": f"Path validation failed for {feelings_path}"}

    try:
        # Ensure parent directory exists
        feelings_path.parent.mkdir(parents=True, exist_ok=True)

        # Create backup before edit
        backup_name = _create_backup(feelings_path)

        if action == "replace":
            feelings_path.write_text(content, encoding="utf-8")
            _audit(
                "memory_self_edit:feelings:replace",
                f"Replaced feelings.md ({len(content)} chars)",
            )
        else:
            # Append with date header
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            entry = f"\n\n---\n*{date_str}*\n{content}"

            existing = ""
            if feelings_path.exists():
                existing = feelings_path.read_text(encoding="utf-8")

            feelings_path.write_text(
                existing + entry, encoding="utf-8",
            )
            _audit(
                "memory_self_edit:feelings:append",
                f"Appended to feelings.md ({len(content)} chars)",
            )

        # Refresh the prompt compiler's feelings cache
        if _prompt_compiler is not None and hasattr(
            _prompt_compiler, "refresh_feelings_cache"
        ):
            _prompt_compiler.refresh_feelings_cache()

        msg = (
            f"Feelings {'replaced' if action == 'replace' else 'updated'} "
            f"successfully ({len(content)} chars)"
        )
        log.info(msg)
        return {"success": True, "message": msg, "backup": backup_name}

    except Exception as exc:
        log.error("Failed to update feelings: %s", exc)
        return {
            "success": False,
            "message": f"Failed to update feelings: {exc}",
            "backup": "",
        }


# ------------------------------------------------------------------ #
#  Tool: update_user_profile                                           #
# ------------------------------------------------------------------ #

async def _update_user_profile(
    section: str,
    content: str,
    action: str = "append",
) -> dict:
    """Update a section of the user profile.

    Parameters
    ----------
    section:
        Which section heading to modify (e.g. ``"交互偏好"``).  The
        section is matched by looking for ``## {section}`` in the file.
    content:
        Text to write.
    action:
        ``"append"`` (default) — add content at the end of the section.
        ``"replace"`` — replace the section's body (keeps the heading).

    Returns
    -------
    ``{"success": bool, "message": str, "backup": str}``
    """
    profile_path = data_dir() / "user_profile.md"

    try:
        validate_path_within(profile_path, data_dir())
    except Exception:
        return {"success": False, "error": f"Path validation failed for {profile_path}"}

    try:
        # Ensure the file exists (create from example if available)
        if not profile_path.exists():
            example = data_dir() / "user_profile.md.example"
            if example.exists():
                shutil.copy2(example, profile_path)
            else:
                profile_path.write_text(
                    f"# User Profile\n\n## {section}\n\n", encoding="utf-8",
                )

        # Backup before edit
        backup_name = _create_backup(profile_path)

        existing = profile_path.read_text(encoding="utf-8")

        # Find the section
        heading = f"## {section}"
        heading_idx = existing.find(heading)

        if heading_idx == -1:
            # Section doesn't exist — append new section at end
            new_section = f"\n\n{heading}\n\n{content}"
            profile_path.write_text(
                existing.rstrip() + new_section, encoding="utf-8",
            )
            _audit(
                f"memory_self_edit:profile:{section}:create",
                f"Created new section '{section}' ({len(content)} chars)",
            )
        else:
            # Find end of section (next ## heading or end of file)
            after_heading = heading_idx + len(heading)
            next_heading = existing.find("\n## ", after_heading)
            if next_heading == -1:
                section_end = len(existing)
            else:
                section_end = next_heading

            if action == "replace":
                # Replace section body, keep heading
                new_body = f"{heading}\n\n{content}\n"
                updated = (
                    existing[:heading_idx]
                    + new_body
                    + existing[section_end:]
                )
                _audit(
                    f"memory_self_edit:profile:{section}:replace",
                    f"Replaced section '{section}' ({len(content)} chars)",
                )
            else:
                # Append to end of section
                insert_point = section_end
                date_str = datetime.now().strftime("%Y-%m-%d")
                addition = f"\n- ({date_str}) {content}"
                updated = (
                    existing[:insert_point]
                    + addition
                    + existing[insert_point:]
                )
                _audit(
                    f"memory_self_edit:profile:{section}:append",
                    f"Appended to section '{section}' ({len(content)} chars)",
                )

            profile_path.write_text(updated, encoding="utf-8")

        msg = (
            f"Profile section '{section}' "
            f"{'replaced' if action == 'replace' else 'updated'} "
            f"successfully ({len(content)} chars)"
        )
        log.info(msg)
        return {"success": True, "message": msg, "backup": backup_name}

    except Exception as exc:
        log.error("Failed to update user profile: %s", exc)
        return {
            "success": False,
            "message": f"Failed to update user profile: {exc}",
            "backup": "",
        }


# ------------------------------------------------------------------ #
#  Tool registration                                                   #
# ------------------------------------------------------------------ #

def get_memory_tools() -> list[ToolSpec]:
    """Return ToolSpec objects for both memory self-edit tools."""
    return [
        ToolSpec(
            name="update_feelings",
            description=(
                "Update your own feelings/emotional memory. "
                "Use action='append' to add a new dated entry, "
                "or action='replace' to rewrite the entire file. "
                "Creates a backup before editing."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": (
                            "The text to write. For append: a new feeling/memory entry. "
                            "For replace: the complete new feelings content."
                        ),
                    },
                    "action": {
                        "type": "string",
                        "enum": ["append", "replace"],
                        "default": "append",
                        "description": (
                            "'append' adds a dated entry at the end; "
                            "'replace' overwrites the entire file."
                        ),
                    },
                },
                "required": ["content"],
            },
            risk_level=RiskLevel.LOW,
            handler=_update_feelings,
        ),
        ToolSpec(
            name="update_user_profile",
            description=(
                "Update a section of the user's profile. "
                "Sections are matched by heading name (e.g. '交互偏好', '竞赛'). "
                "Creates a backup before editing."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": (
                            "The section heading to modify "
                            "(e.g. '交互偏好', '基本信息', '竞赛')."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "The text to write into the section."
                        ),
                    },
                    "action": {
                        "type": "string",
                        "enum": ["append", "replace"],
                        "default": "append",
                        "description": (
                            "'append' adds content at the end of the section; "
                            "'replace' replaces the section body."
                        ),
                    },
                },
                "required": ["section", "content"],
            },
            risk_level=RiskLevel.LOW,
            handler=_update_user_profile,
        ),
    ]
