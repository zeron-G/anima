"""Skill lifecycle management tools — install, uninstall, upgrade."""

from __future__ import annotations

from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger

log = get_logger("skill_management")

_skill_loader = None

def set_skill_loader(loader) -> None:
    global _skill_loader
    _skill_loader = loader

async def _install_skill(source: str, name: str = "") -> dict:
    if not _skill_loader:
        return {"success": False, "error": "Skill loader not initialized"}
    return await _skill_loader.install(source, name)

async def _uninstall_skill(name: str) -> dict:
    if not _skill_loader:
        return {"success": False, "error": "Skill loader not initialized"}
    return await _skill_loader.uninstall(name)

async def _upgrade_skill(name: str) -> dict:
    if not _skill_loader:
        return {"success": False, "error": "Skill loader not initialized"}
    return await _skill_loader.upgrade(name)

async def _list_skills() -> dict:
    if not _skill_loader:
        return {"success": False, "error": "Skill loader not initialized"}
    return {"success": True, "skills": _skill_loader.list_skills()}

def get_skill_management_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="install_skill",
            description="Install a skill from git URL or local path",
            parameters={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Git URL or local path"},
                    "name": {"type": "string", "description": "Skill name (optional, inferred from source)"},
                },
                "required": ["source"],
            },
            risk_level=RiskLevel.HIGH,
            handler=_install_skill,
        ),
        ToolSpec(
            name="uninstall_skill",
            description="Remove an installed skill",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name to uninstall"},
                },
                "required": ["name"],
            },
            risk_level=RiskLevel.HIGH,
            handler=_uninstall_skill,
        ),
        ToolSpec(
            name="upgrade_skill",
            description="Upgrade a git-based skill via git pull",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name to upgrade"},
                },
                "required": ["name"],
            },
            risk_level=RiskLevel.MEDIUM,
            handler=_upgrade_skill,
        ),
        ToolSpec(
            name="list_skills",
            description="List all installed skills",
            parameters={"type": "object", "properties": {}},
            risk_level=RiskLevel.SAFE,
            handler=_list_skills,
        ),
    ]
