"""Centralized secret access (anima/secret_store.py)."""

from __future__ import annotations

import pytest

from anima.secret_store import (
    get_secret, resolve, set_secret_provider, EnvSecretProvider, MissingSecret,
)


def test_get_secret_strips(monkeypatch):
    monkeypatch.setenv("X_SECRET", "  val  ")
    assert get_secret("X_SECRET") == "val"


def test_get_secret_default_and_required(monkeypatch):
    monkeypatch.delenv("NOPE_SECRET", raising=False)
    assert get_secret("NOPE_SECRET", "d") == "d"
    with pytest.raises(MissingSecret):
        get_secret("NOPE_SECRET", required=True)


def test_resolve_references(monkeypatch):
    monkeypatch.setenv("NET_SECRET", "s3cr3t")
    assert resolve("${NET_SECRET}") == "s3cr3t"
    assert resolve("env:NET_SECRET") == "s3cr3t"
    # Non-reference strings pass through unchanged (plain values still work)
    assert resolve("plain-value") == "plain-value"
    assert resolve("") == ""
    assert resolve(None) == ""


def test_pluggable_provider():
    """Swapping the backend (e.g. Vault) needs no business-code changes."""
    class FakeVault:
        def get(self, name):
            return {"K_SECRET": "from-vault"}.get(name)

    try:
        set_secret_provider(FakeVault())
        assert get_secret("K_SECRET") == "from-vault"
        assert get_secret("ABSENT", "fallback") == "fallback"
        assert resolve("${K_SECRET}") == "from-vault"
    finally:
        set_secret_provider(EnvSecretProvider())  # restore global for other tests
