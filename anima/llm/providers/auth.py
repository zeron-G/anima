"""Anthropic auth helpers — token loading and type detection."""
from __future__ import annotations

import json
import os

from anima.llm.providers.constants import _CREDENTIALS_PATH
from anima.utils.logging import get_logger

log = get_logger("llm_providers")


def _load_token_from_credentials() -> str:
    """Try to read OAuth token from Claude Code's credentials."""
    try:
        if _CREDENTIALS_PATH.exists():
            data = json.loads(_CREDENTIALS_PATH.read_text(encoding="utf-8"))
            token = data.get("claudeAiOauth", {}).get("accessToken", "")
            if token:
                log.debug("Loaded OAuth token from credentials")
                return token
    except Exception as e:
        log.debug("Could not read credentials: %s", e)
    return ""


def _get_anthropic_token() -> str:
    """Anthropic auth token. Priority: env AUTH_TOKEN > env OAUTH > credentials > env APIKEY."""
    from anima.secret_store import get_secret
    return (
        get_secret("ANTHROPIC_AUTH_TOKEN")
        or get_secret("ANTHROPIC_OAUTH_TOKEN")
        or _load_token_from_credentials()
        or get_secret("ANTHROPIC_API_KEY")
    )


# Expose for router usage tracking
_get_token = _get_anthropic_token


def _is_oauth_token(token: str) -> bool:
    return token.startswith("sk-ant-oat")
