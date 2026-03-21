"""Shared constants for all LLM providers."""
from __future__ import annotations

import os
from pathlib import Path

# ── Anthropic ──
ANTHROPIC_API_BASE = (
    os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    or "https://api.anthropic.com"
).rstrip("/")
ANTHROPIC_VERSION = "2023-06-01"
CLAUDE_CODE_VERSION = "2.1.76"
OAUTH_BETA_HEADERS = (
    "claude-code-20250219,oauth-2025-04-20,"
    "fine-grained-tool-streaming-2025-05-14"
)
CLAUDE_CODE_IDENTITY = (
    "You are Claude Code, Anthropic's official CLI for Claude."
)
_CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"

# ── OpenAI ──
_OPENAI_API_BASE = "https://api.openai.com"
_LOCAL_LLM_BASE = "http://localhost:8080"
