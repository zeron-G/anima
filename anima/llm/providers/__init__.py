"""LLM provider package -- multi-provider completion API.

Backward compatible: ``from anima.llm.providers import completion`` still works.
"""
from anima.llm.providers.router import completion
from anima.llm.providers.stream import StreamEvent, completion_stream
from anima.llm.providers.auth import (
    _get_token, _get_anthropic_token, _is_oauth_token,
    _load_token_from_credentials,
)
from anima.llm.providers.anthropic_sdk import (
    HAS_ANTHROPIC_SDK, _get_anthropic_client, _anthropic_client,
)
from anima.llm.providers.anthropic_http import _parse_anthropic_response
from anima.llm.providers.constants import (
    ANTHROPIC_API_BASE, ANTHROPIC_VERSION, CLAUDE_CODE_VERSION,
    CLAUDE_CODE_IDENTITY, OAUTH_BETA_HEADERS,
    _CREDENTIALS_PATH, _OPENAI_API_BASE, _LOCAL_LLM_BASE,
)
from anima.llm.providers.local import LocalServerManager, get_local_server_manager
from anima.llm.providers.message_convert import _fix_api_messages

__all__ = [
    # Router
    "completion", "completion_stream",
    # Streaming
    "StreamEvent",
    # Auth
    "_get_token", "_get_anthropic_token", "_is_oauth_token",
    "_load_token_from_credentials",
    # Anthropic SDK
    "HAS_ANTHROPIC_SDK", "_get_anthropic_client", "_anthropic_client",
    # Anthropic HTTP
    "_parse_anthropic_response",
    # Constants
    "ANTHROPIC_API_BASE", "ANTHROPIC_VERSION", "CLAUDE_CODE_VERSION",
    "CLAUDE_CODE_IDENTITY", "OAUTH_BETA_HEADERS",
    "_CREDENTIALS_PATH", "_OPENAI_API_BASE", "_LOCAL_LLM_BASE",
    # Local server
    "LocalServerManager", "get_local_server_manager",
    # Message conversion
    "_fix_api_messages",
]
