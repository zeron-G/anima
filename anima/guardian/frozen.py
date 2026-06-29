"""Frozen recovery core — the modules self-evolution and the self-repair Fixer
may NEVER modify, no matter what flag a proposal sets.

These are the files that decide *whether* evolution may proceed and *how* a bad
evolution is undone. If evolution could rewrite the governance gate, the
watchdog, the rollback logic, or the engine itself, every safety guarantee
collapses (see docs/CODE_REVIEW_2026-06.md P0-1 and docs/EVOLUTION_SAFETY_DESIGN.md §1).

Only a human editing the source tree directly can change a frozen file.

Paths are matched against the repo-relative POSIX form (e.g. ``anima/core/reload.py``).
Directory entries end with ``/`` and match any file beneath them.
"""
from __future__ import annotations

# Exact files and directory prefixes (POSIX, repo-relative).
FROZEN_PATHS: tuple[str, ...] = (
    "anima/guardian/",        # supervisor brain + Fixer + ledger + handoff + frozen itself
    "anima/watchdog.py",      # external process-survival limb
    "anima/core/reload.py",   # checkpoint / hot-reload
    "anima/core/governance.py",  # the evolution gate itself
    "anima/core/boot_health.py",  # known-good anchor + boot self-test + auto-revert
    "anima/evolution/",       # evolution may not rewrite the evolution engine/sandbox/deploy
    "anima/skills/",          # the L0 skill permission model (env scrub + install gate)
    "anima/main.py",          # startup + restart loop
    "anima/__main__.py",
)


def _normalize(path: str) -> str:
    """Repo-relative POSIX form, lowercased drive-agnostic, no leading ./ or /."""
    p = str(path).replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    # If an absolute path slipped in, keep only the tail from the last 'anima/'.
    idx = p.rfind("anima/")
    if idx > 0:
        p = p[idx:]
    return p.lstrip("/")


def is_frozen(path: str) -> bool:
    """True if `path` is part of the frozen recovery core (never auto-modifiable)."""
    p = _normalize(path)
    if not p:
        return False
    for entry in FROZEN_PATHS:
        if entry.endswith("/"):
            if p == entry.rstrip("/") or p.startswith(entry):
                return True
        elif p == entry:
            return True
    return False


def frozen_hits(paths) -> list[str]:
    """Subset of `paths` that are frozen (for gate error messages / audit)."""
    return [p for p in (paths or []) if is_frozen(p)]
