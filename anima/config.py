"""Configuration system — loads from config/default.yaml with overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
    # Load .env from project root
    _env_path = Path(__file__).parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "default.yaml"

# Global config singleton
_config: dict[str, Any] = {}


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load configuration from YAML file, then merge agent overrides."""
    global _config
    config_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            _config = yaml.safe_load(f) or {}
    else:
        _config = {}

    # Merge agent-specific config overrides if they exist
    agent_cfg_path = agent_dir() / "config.yaml"
    if agent_cfg_path.exists():
        with open(agent_cfg_path, "r", encoding="utf-8") as f:
            agent_overrides = yaml.safe_load(f) or {}
        _deep_merge(_config, agent_overrides)

    return _config


def _deep_merge(base: dict, override: dict) -> None:
    """Recursively merge override into base (in-place)."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def get_config() -> dict[str, Any]:
    """Get the current configuration. Loads default if not yet loaded."""
    if not _config:
        load_config()
    return _config


def get(key: str, default: Any = None) -> Any:
    """Get a config value by dot-separated key path.

    Example: get("heartbeat.script_interval_s", 15)
    """
    cfg = get_config()
    parts = key.split(".")
    current = cfg
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return default
    return current


def project_root() -> Path:
    """Return the project root directory."""
    return Path(__file__).parent.parent


def data_dir() -> Path:
    """Return the data directory, creating it if needed."""
    d = project_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prompts_dir() -> Path:
    """Return the prompts directory."""
    return project_root() / "prompts"


def config_dir() -> Path:
    """Return the config directory."""
    return project_root() / "config"


def agents_dir() -> Path:
    """Return the agents directory."""
    return project_root() / "agents"


def agent_dir() -> Path:
    """Return the active agent's directory.

    Reads agent.name from config (without triggering full load to avoid recursion).
    Falls back to reading default.yaml directly.
    """
    if _config:
        name = _config.get("agent", {}).get("name", "default")
    else:
        # Bootstrap: read agent name from default config directly
        if _DEFAULT_CONFIG_PATH.exists():
            with open(_DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            name = raw.get("agent", {}).get("name", "default")
        else:
            name = "default"
    return project_root() / "agents" / name


def agent_name() -> str:
    """Return the active agent's name."""
    return get("agent.name", "default")
