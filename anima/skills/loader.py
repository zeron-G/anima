"""Skill loader — discovers and loads skills from directories.

Supports:
- ANIMA native skills (Python modules with _meta.json)
- OpenClaw-compatible skills (same _meta.json format)
- Auto-discovery from skills/ directory
"""

import json
import subprocess
from pathlib import Path

from anima.config import project_root
from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger

log = get_logger("skills")


class SkillMeta:
    """Parsed skill metadata from _meta.json."""
    def __init__(self, data: dict, path: Path):
        self.name = data.get("name", path.name)
        self.version = data.get("version", "0.0.0")
        self.description = data.get("description", "")
        self.entry_point = data.get("entry_point", "")
        self.commands = data.get("commands", {})
        self.cron = data.get("cron", {})
        self.path = path


class SkillLoader:
    """Discovers and loads skills from the filesystem."""

    def __init__(self):
        self._skills: dict[str, SkillMeta] = {}
        self._default_dir = project_root() / "skills"
        self._skill_dirs = [
            self._default_dir,           # ANIMA native
        ]

    def add_skill_dir(self, path: str | Path) -> None:
        """Add an additional directory to scan for skills."""
        self._skill_dirs.append(Path(path))

    def discover(self) -> list[SkillMeta]:
        """Scan all skill directories and load metadata."""
        self._skills.clear()
        for base_dir in self._skill_dirs:
            if not base_dir.exists():
                continue
            for skill_dir in base_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                meta_file = skill_dir / "_meta.json"
                if not meta_file.exists():
                    continue
                try:
                    data = json.loads(meta_file.read_text(encoding="utf-8"))
                    meta = SkillMeta(data, skill_dir)
                    self._skills[meta.name] = meta
                    log.info("Discovered skill: %s v%s at %s", meta.name, meta.version, skill_dir)
                except Exception as e:
                    log.warning("Failed to load skill from %s: %s", skill_dir, e)
        return list(self._skills.values())

    def get_skill(self, name: str) -> SkillMeta | None:
        return self._skills.get(name)

    def list_skills(self) -> list[dict]:
        return [
            {"name": s.name, "version": s.version, "description": s.description,
             "commands": list(s.commands.keys()), "path": str(s.path)}
            for s in self._skills.values()
        ]

    def run_skill_command(self, skill_name: str, command: str, timeout: int = 120) -> dict:
        """Run a skill command synchronously (for tool execution)."""
        skill = self._skills.get(skill_name)
        if not skill:
            return {"success": False, "error": f"Skill '{skill_name}' not found"}

        cmd_spec = skill.commands.get(command)
        if not cmd_spec:
            return {"success": False, "error": f"Command '{command}' not found in skill '{skill_name}'"}

        usage = cmd_spec.get("usage", "")
        if not usage:
            return {"success": False, "error": "No usage string defined for this command"}

        try:
            import os
            result = subprocess.run(
                usage, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=str(skill.path),
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Timed out after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_cron_jobs(self) -> list[dict]:
        """Extract cron job definitions from all loaded skills."""
        jobs = []
        for skill in self._skills.values():
            for job_name, job_spec in skill.cron.items():
                jobs.append({
                    "skill": skill.name,
                    "name": f"{skill.name}:{job_name}",
                    "cron_expr": job_spec.get("schedule", ""),
                    "command": job_spec.get("command", ""),
                    "description": job_spec.get("description", ""),
                    "cwd": str(skill.path),
                })
        return jobs

    async def install(self, source: str, name: str = "") -> dict:
        """Install a skill from a git URL or local path."""
        import asyncio
        import shutil

        skill_name = name or Path(source).stem
        target_dir = self._default_dir / skill_name

        if target_dir.exists():
            return {"success": False, "error": f"Skill '{skill_name}' already exists"}

        try:
            if source.startswith(("http://", "https://", "git@")):
                await asyncio.to_thread(
                    subprocess.run,
                    ["git", "clone", "--depth", "1", source, str(target_dir)],
                    capture_output=True, timeout=60, check=True,
                )
            else:
                src = Path(source)
                if not src.exists():
                    return {"success": False, "error": f"Source not found: {source}"}
                await asyncio.to_thread(shutil.copytree, src, target_dir)

            # Validate _meta.json
            meta_path = target_dir / "_meta.json"
            if not meta_path.exists():
                shutil.rmtree(target_dir)
                return {"success": False, "error": "No _meta.json found in skill"}

            # Reload
            self.discover()
            return {"success": True, "installed": skill_name, "path": str(target_dir)}
        except Exception as e:
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            return {"success": False, "error": str(e)}

    async def uninstall(self, name: str) -> dict:
        """Remove an installed skill."""
        import shutil

        skill = self.get_skill(name)
        if not skill:
            return {"success": False, "error": f"Skill '{name}' not found"}

        try:
            shutil.rmtree(skill.path)
            self.discover()  # Refresh
            return {"success": True, "uninstalled": name}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def upgrade(self, name: str) -> dict:
        """Upgrade a git-based skill via git pull."""
        import asyncio

        skill = self.get_skill(name)
        if not skill:
            return {"success": False, "error": f"Skill '{name}' not found"}

        git_dir = skill.path / ".git"
        if not git_dir.exists():
            return {"success": False, "error": f"Skill '{name}' is not git-based"}

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["git", "pull"], cwd=str(skill.path),
                capture_output=True, text=True, timeout=30,
            )
            self.discover()  # Refresh
            return {"success": True, "upgraded": name, "output": result.stdout[:200]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def generate_tools(self) -> list[ToolSpec]:
        """Generate ToolSpec objects for each skill command."""
        tools = []
        for skill in self._skills.values():
            for cmd_name, cmd_spec in skill.commands.items():
                tool_name = f"skill_{skill.name}_{cmd_name}"

                async def _handler(skill_name=skill.name, command=cmd_name, timeout: int = 120):
                    return self.run_skill_command(skill_name, command, timeout)

                tools.append(ToolSpec(
                    name=tool_name,
                    description=f"[{skill.name}] {cmd_spec.get('description', cmd_name)}",
                    parameters={
                        "type": "object",
                        "properties": {
                            "timeout": {"type": "integer", "default": 120, "description": "Timeout in seconds"},
                        },
                    },
                    risk_level=RiskLevel.MEDIUM,
                    handler=_handler,
                ))
        return tools
