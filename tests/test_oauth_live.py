"""Live test — verifies OAuth token auth works end-to-end.

Makes REAL API calls. Uses minimal tokens.
Run: pytest tests/test_oauth_live.py -v -s
"""

import os
import pytest

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

from anima.llm.providers import (
    completion, _is_oauth_token, _get_token,
    _load_token_from_credentials, CLAUDE_CODE_VERSION,
)


def test_claude_code_version_matches_local():
    """Verify we're spoofing the correct Claude Code version."""
    # This should match `claude --version` output
    assert CLAUDE_CODE_VERSION == "2.1.76"


def test_credentials_file_discovery():
    """Verify we can read token from ~/.claude/.credentials.json."""
    token = _load_token_from_credentials()
    if not token:
        pytest.skip("No Claude Code credentials file found")
    assert _is_oauth_token(token), f"Credentials token should be OAuth, got: {token[:20]}..."
    print(f"[OK] Credentials token: {token[:20]}...{token[-6:]}")


def test_token_auto_discovery():
    """Verify token is found (env or credentials)."""
    token = _get_token()
    assert token, "No token found from any source"
    print(f"[OK] Token found: {'OAuth' if _is_oauth_token(token) else 'API Key'} ({token[:20]}...)")


@pytest.mark.asyncio
async def test_oauth_haiku():
    """Haiku via OAuth — fast/cheap model."""
    token = _get_token()
    if not token or not _is_oauth_token(token):
        pytest.skip("No OAuth token")

    result = await completion(
        model="claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "Reply: ok"}],
        max_tokens=10,
        temperature=0.0,
    )
    assert result["content"]
    print(f"[OK] Haiku: {result['content']} | {result['usage']}")


@pytest.mark.asyncio
async def test_oauth_sonnet():
    """Sonnet 4.6 via OAuth — tier1 model."""
    token = _get_token()
    if not token or not _is_oauth_token(token):
        pytest.skip("No OAuth token")

    result = await completion(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "Reply: ok"}],
        max_tokens=10,
        temperature=0.0,
    )
    assert result["content"]
    print(f"[OK] Sonnet 4.6: {result['content']} | {result['usage']}")


@pytest.mark.asyncio
async def test_oauth_opus():
    """Opus 4.6 via OAuth — most capable model."""
    token = _get_token()
    if not token or not _is_oauth_token(token):
        pytest.skip("No OAuth token")

    result = await completion(
        model="claude-opus-4-6",
        messages=[{"role": "user", "content": "Reply with one word: ok"}],
        max_tokens=10,
        temperature=0.0,
    )
    assert result["content"]
    print(f"[OK] Opus 4.6: {result['content']} | {result['usage']}")


@pytest.mark.asyncio
async def test_oauth_with_agent_system_prompt():
    """Verify Eva's system prompt works through OAuth with identity injection."""
    token = _get_token()
    if not token or not _is_oauth_token(token):
        pytest.skip("No OAuth token")

    result = await completion(
        model="claude-haiku-4-5-20251001",
        messages=[
            {"role": "system", "content": "You are Eva. Reply in Chinese, one sentence."},
            {"role": "user", "content": "Who are you?"},
        ],
        max_tokens=80,
        temperature=0.7,
    )
    assert result["content"]
    print(f"[OK] Eva: {result['content']}")
