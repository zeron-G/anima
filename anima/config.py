"""Configuration system — loads default.yaml + optional profile + agent overrides + local/env.yaml.

Load order (later overrides earlier):
  1. config/default.yaml      — project defaults (committed to git)
  2. config/profiles/*.yaml    — runtime/deployment profile overrides
  3. agents/<name>/config.yaml — agent personality overrides
  4. local/env.yaml            — machine-specific settings (gitignored)
  5. .env                      — secret keys (gitignored)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "default.yaml"
_PROFILE_CONFIG_DIR = Path(__file__).parent.parent / "config" / "profiles"
_LOCAL_CONFIG_PATH = Path(__file__).parent.parent / "local" / "env.yaml"

# Global config singleton
_config: dict[str, Any] = {}
_local: dict[str, Any] = {}
_active_profile: str = "default"


def load_config(
    path: Path | str | None = None,
    *,
    profile: str | None = None,
    local_path: Path | str | None = None,
    include_local: bool = True,
) -> dict[str, Any]:
    """Load configuration: default.yaml → profile → agent overrides → local/env.yaml."""
    global _config, _local, _active_profile

    config_path = Path(path) if path else _DEFAULT_CONFIG_PATH
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

    # Merge local environment (machine-specific, gitignored)
    # Deep-merge ALL local settings into config — llm, network, channels, etc.
    _local = {}
    resolved_local_path = Path(local_path) if local_path else _LOCAL_CONFIG_PATH
    if include_local and resolved_local_path.exists():
        with open(resolved_local_path, "r", encoding="utf-8") as f:
            _local = yaml.safe_load(f) or {}
        _deep_merge(_config, _local)

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


def profile_dir() -> Path:
    return _PROFILE_CONFIG_DIR


def resolve_profile_path(profile_name: str) -> Path:
    return _PROFILE_CONFIG_DIR / f"{profile_name}.yaml"


def available_profiles() -> list[str]:
    if not _PROFILE_CONFIG_DIR.exists():
        return []
    return sorted(p.stem for p in _PROFILE_CONFIG_DIR.glob("*.yaml"))


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
    """Load local/env.yaml into _local dict."""
    global _local
    if _LOCAL_CONFIG_PATH.exists():
        with open(_LOCAL_CONFIG_PATH, "r", encoding="utf-8") as f:
            _local = yaml.safe_load(f) or {}


def local_get(key: str, default: Any = None) -> Any:
    """Get a value from local/env.yaml by dot-separated key path."""
    if not _local and _LOCAL_CONFIG_PATH.exists():
        _load_local()
    parts = key.split(".")
    current = _local
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def project_root() -> Path:
    return Path(__file__).parent.parent


def data_dir() -> Path:
    d = project_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prompts_dir() -> Path:
    return project_root() / "prompts"


def config_dir() -> Path:
    return project_root() / "config"


def agents_dir() -> Path:
    return project_root() / "agents"


def agent_dir() -> Path:
    """Return the active agent's directory."""
    if _config:
        name = _config.get("agent", {}).get("name", "default")
    else:
        if _DEFAULT_CONFIG_PATH.exists():
            with open(_DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            name = raw.get("agent", {}).get("name", "default")
        else:
            name = "default"
    return project_root() / "agents" / name


def agent_name() -> str:
    return get("agent.name", "default")
