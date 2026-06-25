"""Build-time customization for ANIMA (metadata lives in pyproject.toml).

The kernel resolves read-only assets (config/, prompts/, skills/) from
``anima/_resources/`` when running *installed* with no source tree
(see ``anima.config.config_dir`` / ``prompts_dir`` / ``skills_dir``).

To keep a single source of truth, those top-level asset directories are
copied into ``anima/_resources/`` at build time rather than being duplicated
in git. ``anima/_resources/`` is a build artifact (gitignored).

Includes the persona *seed* (``agents/_seed``) — the publishable born-state
(identity/rules/examples/baseline persona, no lived memory). ``anima init``
copies it from ``seed_agents_dir()`` into the user's private live instance.
The live ``agents/<name>`` instance itself is gitignored and never bundled.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

_ROOT = Path(__file__).parent.resolve()
_RESOURCES = _ROOT / "anima" / "_resources"

# Read-only assets bundled into the wheel for installed (no-source) use.
# Includes the persona seed; excludes the private live instance (agents/<name>).
_BUNDLE = ("config", "prompts", "skills", "agents/_seed")


def _sync_resources() -> None:
    """Mirror the bundled asset dirs into anima/_resources/ (idempotent)."""
    for name in _BUNDLE:
        src = _ROOT / name
        if not src.exists():
            continue
        dst = _RESOURCES / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


class build_py_with_resources(build_py):
    """Populate anima/_resources/ before the standard package-data collection."""

    def run(self) -> None:
        _sync_resources()
        super().run()


setup(cmdclass={"build_py": build_py_with_resources})
