"""Configuration system + path resolution.

Path model (Phase 1 — code/state separation):

  package_root()  — where the anima *code* lives (anchored to this file).
                    Ships with the kernel; read-only resources live here.
  source_tree()   — the git working tree root, or None when not running from
                    a source checkout (e.g. pip-installed). Used for git /
                    evolution / spawn / dev-watch, which require a source repo.
  home_dir()      — where *user state* lives (data, agents/persona, secrets).
                    Resolution (first match wins):
                      1. $ANIMA_HOME                (explicit personal private dir)
                      2. source tree root           (dev checkout uses its own
                                                     ./data + ./agents — project-local)
                      3. ~/.anima                   (if it already exists)
                      4. bootstrap: create & use ~/.anima (installed, no home yet)

  data_dir()      = home_dir()/data       (user runtime data: anima.db, chroma…)
  agent_dir()     = home_dir()/agents/<name>  (live persona instance)
  config_dir()    = source config/ when in a checkout, else packaged resources
  prompts_dir()   = source prompts/ when in a checkout, else packaged resources

Config load order (later overrides earlier):
  1. config/default.yaml          — project defaults (shipped with kernel)
  2. config/profiles/*.yaml        — runtime/deployment profile overrides
  3. agents/<name>/config.yaml     — agent personality overrides
  4. home_dir()/config.yaml        — machine-specific settings (canonical)
  5. local/env.yaml                — legacy machine settings (source checkout)
  6. .env                          — secret keys
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# ── Path anchors ─────────────────────────────────────────────────────────

def package_root() -> Path:
    """Directory containing the anima package code (the kernel)."""
    return Path(__file__).resolve().parent


def source_tree() -> Path | None:
    """The git working tree root, or None if not running from a source checkout.

    Walks up from the package directory looking for a `.git` entry. Returns None
    when the kernel is installed (no source repo) — callers that need a repo
    (evolution, spawn, dev-watch) must guard against None instead of assuming
    a project tree exists.
    """
    start = package_root().parent
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


_HOME_OVERRIDE: Path | None = None
_DATA_DIR_OVERRIDE: Path | None = None


def set_home(path: Path | str | None) -> None:
    """Override the home dir (test/embedding hook). Pass None to clear."""
    global _HOME_OVERRIDE
    _HOME_OVERRIDE = Path(path).expanduser() if path else None


def home_dir() -> Path:
    """Resolve the user-state home directory (see module docstring)."""
    if _HOME_OVERRIDE is not None:
        return _HOME_OVERRIDE

    env = os.environ.get("ANIMA_HOME", "").strip()
    if env:
        return Path(env).expanduser()

    # Project-local: a source checkout keeps its own ./data and ./agents.
    st = source_tree()
    if st is not None:
        return st

    dot = Path.home() / ".anima"
    if dot.exists():
        return dot

    # Installed with no configured home: create and use ~/.anima.
    dot.mkdir(parents=True, exist_ok=True)
    return dot


def set_data_dir(path: Path | str | None) -> None:
    """Override the data dir independently of home (test/embedding hook)."""
    global _DATA_DIR_OVERRIDE
    _DATA_DIR_OVERRIDE = Path(path).expanduser() if path else None


def _bootstrap_dotenv() -> None:
    """Load .env secrets at import time from the first location that exists.

    Order mirrors home resolution ($ANIMA_HOME → source tree → ~/.anima) but
    never creates directories — it only reads an existing .env.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    candidates: list[Path] = []
    env_home = os.environ.get("ANIMA_HOME", "").strip()
    if env_home:
        candidates.append(Path(env_home).expanduser() / ".env")
    st = source_tree()
    if st is not None:
        candidates.append(st / ".env")
    candidates.append(Path.home() / ".anima" / ".env")
    for candidate in candidates:
        if candidate.exists():
            load_dotenv(candidate)
            return


_bootstrap_dotenv()


# Resources are bundled inside the package under _resources/ for installed use.
_PACKAGED_RESOURCES = "_resources"

_DEFAULT_CONFIG_RELPATH = ("config", "default.yaml")
_LEGACY_LOCAL_RELPATH = ("local", "env.yaml")

# Global config singleton
_config: dict[str, Any] = {}
_local: dict[str, Any] = {}
_active_profile: str = "default"


# ── Resource / state directories ─────────────────────────────────────────

def project_root() -> Path:
    """Deprecated: prefer source_tree()/package_root()/home_dir() explicitly.

    Kept as a compatibility shim. In a source checkout it returns the repo
    root (identical to the historical `Path(__file__).parent.parent`); when
    installed it falls back to the package's parent.
    """
    st = source_tree()
    return st if st is not None else package_root().parent


def data_dir() -> Path:
    d = _DATA_DIR_OVERRIDE if _DATA_DIR_OVERRIDE is not None else (home_dir() / "data")
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    """Resolve the SQLite database path under the data dir.

    `memory.db_path` config is interpreted as: absolute path → used as-is;
    relative path → placed under data_dir() (a legacy leading "data/" segment
    is stripped so "data/anima.db" and "anima.db" both land in data_dir()).
    """
    raw = (get("memory.db_path", "") or "").strip()
    if not raw:
        return data_dir() / "anima.db"
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    parts = list(p.parts)
    if parts and parts[0] == "data":
        parts = parts[1:]
    return data_dir().joinpath(*parts) if parts else data_dir() / "anima.db"


def config_dir() -> Path:
    """Directory holding default.yaml + profiles/ (shipped with the kernel)."""
    st = source_tree()
    if st is not None and (st / "config").exists():
        return st / "config"
    return package_root() / _PACKAGED_RESOURCES / "config"


def prompts_dir() -> Path:
    st = source_tree()
    if st is not None and (st / "prompts").exists():
        return st / "prompts"
    return package_root() / _PACKAGED_RESOURCES / "prompts"


def profile_dir() -> Path:
    return config_dir() / "profiles"


def skills_dir() -> Path:
    """Built-in skills directory (code asset shipped with the kernel)."""
    st = source_tree()
    if st is not None and (st / "skills").exists():
        return st / "skills"
    return package_root() / _PACKAGED_RESOURCES / "skills"


def workspace_root() -> Path:
    """Default working directory for agentic tools (shell, claude-code, audits).

    A source checkout operates on the repo itself; an installed kernel falls
    back to the user's home dir (there is no source tree to act on).
    """
    return source_tree() or home_dir()


def agents_dir() -> Path:
    """User's live agents directory (persona instances)."""
    return home_dir() / "agents"


def seed_agents_dir() -> Path:
    """Read-only persona seeds shipped with the kernel (Phase 2 bootstrap source)."""
    st = source_tree()
    if st is not None and (st / "agents").exists():
        return st / "agents"
    return package_root() / _PACKAGED_RESOURCES / "agents"


def _default_config_path() -> Path:
    return config_dir() / "default.yaml"


def _home_config_path() -> Path:
    return home_dir() / "config.yaml"


def _legacy_local_path() -> Path | None:
    st = source_tree()
    if st is None:
        return None
    return st.joinpath(*_LEGACY_LOCAL_RELPATH)


# ── Config loading ───────────────────────────────────────────────────────

def load_config(
    path: Path | str | None = None,
    *,
    profile: str | None = None,
    local_path: Path | str | None = None,
    include_local: bool = True,
) -> dict[str, Any]:
    """Load configuration: default.yaml → profile → agent overrides → local."""
    global _config, _local, _active_profile

    config_path = Path(path) if path else _default_config_path()
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            _config = yaml.safe_load(f) or {}
    else:
        _config = {}

    requested_profile = (profile or os.environ.get("ANIMA_PROFILE", "")).strip()
    _active_profile = requested_profile or "default"
    if requested_profile:
        profile_path = resolve_profile_path(requested_profile)
        if not profile_path.exists():
            raise FileNotFoundError(f"Unknown ANIMA profile '{requested_profile}': {profile_path}")
        with open(profile_path, "r", encoding="utf-8") as f:
            profile_overrides = yaml.safe_load(f) or {}
        _deep_merge(_config, profile_overrides)

    # Merge agent-specific overrides
    agent_cfg_path = agent_dir() / "config.yaml"
    if agent_cfg_path.exists():
        with open(agent_cfg_path, "r", encoding="utf-8") as f:
            agent_overrides = yaml.safe_load(f) or {}
        _deep_merge(_config, agent_overrides)

    # Inject Discord token from environment variable if not set
    discord_cfg = _config.setdefault("channels", {}).setdefault("discord", {})
    if not discord_cfg.get("token"):
        env_token = os.environ.get("DISCORD_BOT_TOKEN", "")
        if env_token:
            discord_cfg["token"] = env_token

    # Merge machine-specific settings (gitignored). Canonical location is
    # home_dir()/config.yaml; the legacy source-tree local/env.yaml is still
    # honored (and wins) for backward compatibility in a dev checkout.
    _local = {}
    if include_local:
        layers: list[dict] = []
        if local_path is not None:
            explicit = Path(local_path)
            if explicit.exists():
                with open(explicit, "r", encoding="utf-8") as f:
                    layers.append(yaml.safe_load(f) or {})
        else:
            home_cfg = _home_config_path()
            if home_cfg.exists():
                with open(home_cfg, "r", encoding="utf-8") as f:
                    layers.append(yaml.safe_load(f) or {})
            legacy = _legacy_local_path()
            if legacy and legacy.exists() and legacy != home_cfg:
                with open(legacy, "r", encoding="utf-8") as f:
                    layers.append(yaml.safe_load(f) or {})
        for layer in layers:
            _deep_merge(_local, layer)
            _deep_merge(_config, layer)

    runtime_cfg = _config.setdefault("runtime", {})
    runtime_cfg.setdefault("profile", _active_profile)

    return _config


def _deep_merge(base: dict, override: dict) -> None:
    """Recursively merge override into base (in-place)."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def get_config() -> dict[str, Any]:
    if not _config:
        load_config()
    return _config


def active_profile() -> str:
    if not _config:
        load_config()
    return _config.get("runtime", {}).get("profile", _active_profile or "default")


def resolve_profile_path(profile_name: str) -> Path:
    return profile_dir() / f"{profile_name}.yaml"


def available_profiles() -> list[str]:
    pdir = profile_dir()
    if not pdir.exists():
        return []
    return sorted(p.stem for p in pdir.glob("*.yaml"))


def get(key: str, default: Any = None) -> Any:
    """Get a config value by dot-separated key path."""
    cfg = get_config()
    parts = key.split(".")
    current = cfg
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def _load_local() -> None:
    """Load machine-local config into _local dict (home config + legacy)."""
    global _local
    _local = {}
    home_cfg = _home_config_path()
    if home_cfg.exists():
        with open(home_cfg, "r", encoding="utf-8") as f:
            _deep_merge(_local, yaml.safe_load(f) or {})
    legacy = _legacy_local_path()
    if legacy and legacy.exists() and legacy != home_cfg:
        with open(legacy, "r", encoding="utf-8") as f:
            _deep_merge(_local, yaml.safe_load(f) or {})


def local_get(key: str, default: Any = None) -> Any:
    """Get a value from machine-local config by dot-separated key path."""
    if not _local:
        _load_local()
    parts = key.split(".")
    current = _local
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def agent_dir() -> Path:
    """Return the active agent's directory (live persona instance under home)."""
    if _config:
        name = _config.get("agent", {}).get("name", "default")
    else:
        default_cfg = _default_config_path()
        if default_cfg.exists():
            with open(default_cfg, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            name = raw.get("agent", {}).get("name", "default")
        else:
            name = "default"
    return agents_dir() / name


def agent_name() -> str:
    return get("agent.name", "default")
