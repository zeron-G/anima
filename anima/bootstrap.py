"""`anima init` — bootstrap a private ANIMA_HOME from the persona seed.

Creates the user-state skeleton so a freshly-installed kernel can run:
  <home>/data/{logs,uploads,notes,workspace}   runtime data dirs
  <home>/agents/<name>/                          live persona instance (copied from seed)
  <home>/config.yaml                             machine-local overrides (template)
  <home>/.env                                    secret keys (from .env.example)

The persona *seed* (``agents/_seed``, resolved via config.seed_agents_dir())
is copied into the live instance; thereafter the instance evolves privately
and the kernel never writes back into the published seed.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from anima import config as _cfg

_DATA_SUBDIRS = ("logs", "uploads", "notes", "workspace")

_CONFIG_TEMPLATE = """\
# ANIMA machine-local config (gitignored; highest-priority override layer).
# Anything here overrides config/default.yaml -> profiles -> agent config.
# Examples:
#   llm:
#     tier1: {model: claude-opus-4-8}
#   dashboard:
#     port: 8420
"""

_ENV_TEMPLATE = """\
# ANIMA secrets — fill in ONE auth method (never commit this file).
# Claude Code OAuth is auto-discovered if you've run `claude login`; otherwise:
# ANTHROPIC_OAUTH_TOKEN=sk-ant-oat01-...
# ANTHROPIC_API_KEY=sk-ant-api03-...
"""

_HELP = """\
anima init — set up a private ANIMA home (data + persona instance).

Usage:
  python -m anima init [--home DIR] [--name NAME] [--force]

Options:
  --home DIR   Target home dir (default: $ANIMA_HOME, else the source tree,
               else ~/.anima). Remember to `set ANIMA_HOME=DIR` to use a
               custom location at runtime.
  --name NAME  Persona instance name under <home>/agents/ (default: eva).
  --force      Overwrite an existing persona instance (DESTROYS its memory).
"""


def init_home(home: Path | None = None, *, name: str = "eva", force: bool = False) -> tuple[Path, list[str]]:
    """Create the home skeleton + persona instance. Returns (home, created_items).

    Idempotent: existing files/dirs are kept (an existing persona instance is
    preserved unless ``force`` is set) so re-running never destroys live state.
    """
    home = Path(home).expanduser() if home else _cfg.home_dir()
    home.mkdir(parents=True, exist_ok=True)
    created: list[str] = []

    # 1. data skeleton
    data = home / "data"
    data.mkdir(parents=True, exist_ok=True)
    for sub in _DATA_SUBDIRS:
        (data / sub).mkdir(parents=True, exist_ok=True)

    # 2. live persona instance, copied from the seed
    seed = _cfg.seed_agents_dir()
    instance = home / "agents" / name
    if instance.exists() and not force:
        created.append(f"agents/{name}: kept (already exists)")
    else:
        if not seed.exists():
            raise RuntimeError(
                f"persona seed not found at {seed} — kernel install is incomplete"
            )
        if instance.exists():
            shutil.rmtree(instance)
        instance.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(seed, instance)
        # The seed README is documentation, not part of a live instance.
        (instance / "README.md").unlink(missing_ok=True)
        created.append(f"agents/{name} (from seed)")

    # 3. machine-local config template
    cfg_path = home / "config.yaml"
    if not cfg_path.exists():
        cfg_path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
        created.append("config.yaml")

    # 4. .env (from .env.example when available, else a minimal template)
    env_path = home / ".env"
    if not env_path.exists():
        example: Path | None = None
        st = _cfg.source_tree()
        candidates = []
        if st is not None:
            candidates.append(st / ".env.example")
        candidates.append(_cfg.package_root() / "_resources" / ".env.example")
        for cand in candidates:
            if cand.exists():
                example = cand
                break
        if example is not None:
            shutil.copyfile(example, env_path)
        else:
            env_path.write_text(_ENV_TEMPLATE, encoding="utf-8")
        created.append(".env (fill in your keys)")

    return home, created


def handle_init(args: list[str]) -> None:
    """CLI handler for `anima init` (invoked from both entry points)."""
    home_arg = ""
    name = "eva"
    force = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            print(_HELP)
            return
        if a == "--home" and i + 1 < len(args):
            home_arg = args[i + 1]; i += 2
        elif a == "--name" and i + 1 < len(args):
            name = args[i + 1]; i += 2
        elif a == "--force":
            force = True; i += 1
        else:
            i += 1

    target = Path(home_arg).expanduser() if home_arg else None
    home, created = init_home(target, name=name, force=force)

    print(f"ANIMA home ready: {home}")
    for item in created:
        print(f"  + {item}")
    if home_arg and not _is_default_home(home):
        print("\nTo use this home at runtime, set the environment variable:")
        print(f"  Windows:  set ANIMA_HOME={home}")
        print(f"  POSIX:    export ANIMA_HOME={home}")
    print("\nNext: edit <home>/.env with your keys, then `python -m anima --headless`.")


def _is_default_home(home: Path) -> bool:
    """True if `home` is already where the kernel resolves home_dir() by default."""
    try:
        return home.resolve() == _cfg.home_dir().resolve()
    except Exception:
        return False


def main() -> None:
    handle_init(sys.argv[1:])


if __name__ == "__main__":
    main()
