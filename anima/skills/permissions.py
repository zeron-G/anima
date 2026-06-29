"""Skill capability manifest + permission model (L0 additive growth safety).

A skill is the SAFE way for Eva to grow — "install a capability" should be
undo-able by flipping a flag, not a git revert. But that only holds if a skill
can't quietly do everything: read the DB URL, exfiltrate keys, or be installed
from an arbitrary remote with no review. So each skill DECLARES what it needs in
its ``_meta.json`` ``permissions`` block, and the runtime is default-deny.

What is actually ENFORCED here (cheap + strong):
  - **Secret env is always stripped** from a skill's subprocess — a skill can
    never read ANIMA's DATABASE_URL / provider keys / dashboard creds, declared
    or not (docs/EVOLUTION_SAFETY_DESIGN.md §1: "install plugin" must not equal
    "arbitrary code execution with all secrets").
  - **Install approval gate**: a skill from a REMOTE source, or one declaring
    elevated permissions (shell / network / filesystem-write), needs an
    out-of-band human approval token before it installs.

The network/filesystem fields are DECLARATIONS (they inform the approval
decision and are surfaced to the human); hard sandboxing of an arbitrary shell
command is out of scope here and tracked for the container-isolation phase.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from anima.utils.logging import get_logger

log = get_logger("skills.permissions")

# Env var substrings that mark a secret a skill must never receive. Mirrors the
# evolution review's exfil concern. ANTHROPIC/CLAUDE are NOT special-cased here —
# a skill has no business with them either.
_SECRET_ENV_MARKERS = (
    "DATABASE", "POSTGRES", "NEON", "DSN", "ANTHROPIC", "OPENAI", "DEEPSEEK",
    "GROQ", "GEMINI", "GOOGLE", "MISTRAL", "XAI", "CLAUDE", "R2_", "CLOUDFLARE",
    "CF_API", "CF_TOKEN", "AWS", "AZURE", "DISCORD", "TELEGRAM", "SLACK",
    "GITHUB_TOKEN", "DASHBOARD", "JWT", "SECRET", "PASSWORD", "PASSWD",
    "PRIVATE_KEY", "API_KEY", "APIKEY", "TOKEN",
)


def _is_secret_env(key: str) -> bool:
    u = key.upper()
    return any(m in u for m in _SECRET_ENV_MARKERS)


def build_skill_env(skill_path: Path | None = None) -> dict[str, str]:
    """Environment for a skill subprocess: everything EXCEPT ANIMA secrets.
    Replaces passing the full os.environ (which leaked the DB URL + every key)."""
    env = {k: v for k, v in os.environ.items() if not _is_secret_env(k)}
    env["PYTHONIOENCODING"] = "utf-8"
    if skill_path is not None:
        env["PYTHONPATH"] = str(skill_path)
    return env


@dataclass
class SkillPermissions:
    """Declared capability requirements from a skill's _meta.json `permissions`."""
    shell: bool = False
    network: list[str] = field(default_factory=list)        # allowed hosts ([] none, ["*"] any)
    fs_read: list[str] = field(default_factory=list)
    fs_write: list[str] = field(default_factory=list)
    env: list[str] = field(default_factory=list)            # extra non-secret env names requested
    risk: str = "medium"
    declared: bool = False                                   # was a permissions block present?

    @classmethod
    def from_meta(cls, data: dict) -> "SkillPermissions":
        p = (data or {}).get("permissions")
        if not isinstance(p, dict):
            # No declaration → minimal, and flagged so install can require review.
            return cls(declared=False)
        fs = p.get("filesystem", {}) or {}
        return cls(
            shell=bool(p.get("shell", False)),
            network=list(p.get("network", []) or []),
            fs_read=list(fs.get("read", []) or []),
            fs_write=list(fs.get("write", []) or []),
            env=list(p.get("env", []) or []),
            risk=str(p.get("risk", "medium")),
            declared=True,
        )

    def is_elevated(self) -> bool:
        """Requests something beyond a self-contained, read-only command."""
        return bool(self.shell or self.network or self.fs_write)

    def summary(self) -> dict:
        return {
            "shell": self.shell, "network": self.network,
            "fs_read": self.fs_read, "fs_write": self.fs_write,
            "risk": self.risk, "declared": self.declared,
        }


def _is_remote_source(source: str) -> bool:
    return source.startswith(("http://", "https://", "git@", "ssh://"))


# ── Install approval (out-of-band, mirrors the evolution core-module gate) ──
def skill_approvals_dir() -> Path:
    from anima.config import data_dir
    d = data_dir() / ".guardian" / "skill_approvals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_skill_approved(name: str) -> bool:
    if not name:
        return False
    return (skill_approvals_dir() / f"{name}.approved").exists()


def requires_install_approval(source: str, perms: SkillPermissions) -> tuple[bool, str]:
    """Whether installing this skill needs a human approval token, and why.

    Remote code, elevated permissions, or an undeclared permission set all
    require explicit human sign-off; a local, low-permission, declared skill
    installs freely."""
    if _is_remote_source(source):
        return True, "remote source"
    if perms.is_elevated():
        return True, "elevated permissions (shell/network/fs-write)"
    if not perms.declared:
        return True, "no permissions block declared"
    return False, ""
