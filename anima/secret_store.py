"""Centralized secret access — one interface, pluggable backend.

Every API key, password, and token flows through here. Today the backend is the
process environment (loaded from ``.env`` by ``config._bootstrap_dotenv`` or the
real OS env). Swapping to HashiCorp Vault / AWS Secrets Manager / Azure Key Vault
is a single ``SecretProvider`` implementation + ``set_secret_provider()`` — no
business-code changes downstream.

Two entry points:
    get_secret("OPENAI_API_KEY")        — fetch a secret by its env-var name
    resolve("${ANIMA_NETWORK_SECRET}")  — expand a ${VAR} / env:VAR reference that
                                          appears in a CONFIG value (config holds
                                          the reference; the value lives in .env /
                                          a vault, never in the YAML)

Design rule: secrets never live in tracked files. Config (default.yaml /
local/env.yaml) may hold only *references* like "${ANIMA_NETWORK_SECRET}".
"""
from __future__ import annotations

import os
import re
from typing import Protocol, runtime_checkable

# Canonical secret env-var names — single source of truth for .env.example,
# audits, and the dashboard's "what's configured" view. Add new secrets here.
KNOWN_SECRETS: list[str] = [
    # LLM providers
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_OAUTH_TOKEN",
    "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
    # Channels
    "DISCORD_BOT_TOKEN", "TELEGRAM_BOT_TOKEN",
    # Dashboard auth
    "ANIMA_DASHBOARD_PASSWORD", "ANIMA_DASHBOARD_JWT_SECRET",
    # Distributed mesh + remote nodes
    "ANIMA_NETWORK_SECRET", "ANIMA_LAPTOP_SSH_PASSWORD",
    # Data store (Neon / Postgres + pgvector). Contains an embedded password.
    "DATABASE_URL",
    # Local Postgres fallback (offline failover + test DB). Embedded password.
    "LOCAL_DATABASE_URL",
]


@runtime_checkable
class SecretProvider(Protocol):
    """A secret backend. Implement ``get`` and register via set_secret_provider."""
    def get(self, name: str) -> str | None: ...


class EnvSecretProvider:
    """Default backend: read from the process environment (.env / OS env)."""
    def get(self, name: str) -> str | None:
        return os.environ.get(name)


_provider: SecretProvider = EnvSecretProvider()


def set_secret_provider(provider: SecretProvider) -> None:
    """Swap the secret backend (e.g. a Vault / cloud-KV provider).

    One call at startup; every get_secret()/resolve() downstream uses it.
    """
    global _provider
    _provider = provider


def get_provider_name() -> str:
    return type(_provider).__name__


class MissingSecret(RuntimeError):
    """Raised by get_secret(..., required=True) when a secret is unset."""


def get_secret(name: str, default: str = "", *, required: bool = False) -> str:
    """Fetch a secret by name. Returns the stripped value, else ``default``.

    required=True raises MissingSecret when unset (use for must-have secrets).
    """
    try:
        raw = _provider.get(name)
    except Exception:
        raw = None
    val = (raw or "").strip()
    if not val:
        if required:
            raise MissingSecret(f"Required secret '{name}' is not set")
        return default
    return val


_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$|^env:([A-Za-z_][A-Za-z0-9_]*)$")


def resolve(value: str | None) -> str:
    """Expand a config secret *reference* to its value.

    ``"${VAR}"`` or ``"env:VAR"`` → ``get_secret(VAR)``. Any other string is
    returned unchanged (plain values still work; empty stays empty). This lets
    config files carry a reference while the real secret lives in .env / a vault.
    """
    if not value or not isinstance(value, str):
        return value or ""
    m = _REF_RE.match(value.strip())
    if m:
        return get_secret(m.group(1) or m.group(2))
    return value
