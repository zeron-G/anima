"""Codex OAuth -- ChatGPT subscription via Responses API."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from anima.utils.logging import get_logger

log = get_logger("llm_providers")

# ── Codex constants ──
_CODEX_AUTH_PATH = Path.home() / ".codex" / "auth.json"
_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_CODEX_REFRESH_URL = "https://auth.openai.com/oauth/token"
_codex_tokens: dict = {}
_codex_last_load: float = 0


def _load_codex_tokens() -> dict:
    """Load Codex OAuth tokens from ~/.codex/auth.json."""
    global _codex_tokens, _codex_last_load
    if time.time() - _codex_last_load < 60 and _codex_tokens:
        return _codex_tokens
    if not _CODEX_AUTH_PATH.exists():
        return {}
    try:
        data = json.loads(_CODEX_AUTH_PATH.read_text(encoding="utf-8"))
        if data.get("auth_mode") != "chatgpt":
            return {}
        _codex_tokens = data.get("tokens", {})
        _codex_last_load = time.time()
        return _codex_tokens
    except Exception as e:
        log.warning("Failed to read Codex auth: %s", e)
        return {}


def _codex_token_expired(tokens: dict) -> bool:
    """Check if the Codex access token is expired or near expiry (5min buffer)."""
    access = tokens.get("access_token", "")
    if not access:
        return True
    try:
        import base64
        payload = access.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return time.time() > (claims.get("exp", 0) - 300)
    except Exception:
        return False


async def _refresh_codex_token() -> dict:
    """Refresh the Codex OAuth access token via auth.openai.com."""
    global _codex_tokens
    tokens = _load_codex_tokens()
    refresh = tokens.get("refresh_token", "")
    if not refresh:
        log.warning("No Codex refresh token available")
        return {}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _CODEX_REFRESH_URL,
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "client_id": _CODEX_CLIENT_ID,
                    "scope": "openid profile email offline_access",
                },
            )
        if resp.status_code != 200:
            log.warning("Codex token refresh failed: %d %s", resp.status_code, resp.text[:200])
            return {}
        new_data = resp.json()
        tokens["access_token"] = new_data["access_token"]
        tokens["id_token"] = new_data.get("id_token", tokens.get("id_token", ""))
        if new_data.get("refresh_token"):
            tokens["refresh_token"] = new_data["refresh_token"]
        # Write back to auth.json
        auth_data = json.loads(_CODEX_AUTH_PATH.read_text(encoding="utf-8"))
        auth_data["tokens"] = tokens
        auth_data["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        _CODEX_AUTH_PATH.write_text(json.dumps(auth_data, indent=2), encoding="utf-8")
        _codex_tokens = tokens
        log.info("Codex OAuth token refreshed successfully")
        return tokens
    except Exception as e:
        log.warning("Codex token refresh error: %s", e)
        return {}


async def _codex_completion(
    model: str,
    messages: list[dict],
    max_tokens: int = 4096,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> dict:
    """Call OpenAI via Codex OAuth (Responses API at chatgpt.com).

    Converts from Anthropic Messages format to OpenAI Responses API format,
    sends to chatgpt.com/backend-api/codex/responses, and converts back.
    """
    tokens = _load_codex_tokens()
    if not tokens:
        raise RuntimeError("No Codex OAuth tokens (check ~/.codex/auth.json)")

    if _codex_token_expired(tokens):
        tokens = await _refresh_codex_token()
        if not tokens:
            raise RuntimeError("Codex token refresh failed")

    access_token = tokens.get("access_token", "")
    account_id = tokens.get("account_id", "")

    if model.startswith("codex/"):
        model = model[len("codex/"):]

    # Convert messages -> Responses API format
    instructions = ""
    input_items: list[dict] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            instructions += (content if isinstance(content, str) else str(content)) + "\n"
            continue

        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    if block:
                        input_items.append({"role": role, "content": str(block)})
                    continue
                btype = block.get("type", "")
                if btype == "tool_use":
                    input_items.append({
                        "type": "function_call",
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                        "call_id": block.get("id", ""),
                    })
                elif btype == "tool_result":
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": block.get("tool_use_id", ""),
                        "output": str(block.get("content", "")),
                    })
                elif btype == "text":
                    text = block.get("text", "")
                    if text:
                        input_items.append({"role": role, "content": text})
                else:
                    text = block.get("text", block.get("content", ""))
                    if text:
                        input_items.append({"role": role, "content": str(text)})
        elif content:
            input_items.append({"role": role, "content": str(content)})

    # Build payload
    payload: dict[str, Any] = {
        "model": model,
        "instructions": instructions.strip(),
        "input": input_items,
        # Note: Codex endpoint doesn't support max_output_tokens or temperature
        "stream": False,
        "store": False,
    }

    if tools:
        payload["tools"] = [
            {
                "type": "function",
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", t.get("parameters", {})),
            }
            for t in tools
        ]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "ChatGPT-Account-ID": account_id,
        "Content-Type": "application/json",
    }

    # Codex requires streaming -- collect SSE events into final response
    payload["stream"] = True
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    _timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)

    async def _stream_request(hdrs: dict) -> dict:
        content_parts: list[str] = []
        tool_calls_out: list[dict] = []
        usage_out: dict = {}

        async with httpx.AsyncClient(timeout=_timeout) as client:
            async with client.stream(
                "POST", f"{_CODEX_BASE_URL}/responses",
                headers=hdrs, content=body,
            ) as resp:
                if resp.status_code != 200:
                    error_body = ""
                    async for chunk in resp.aiter_text():
                        error_body += chunk
                        if len(error_body) > 500:
                            break
                    raise RuntimeError(
                        f"Codex API error {resp.status_code}: {error_body[:500]}"
                    )

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if not data_str:
                        continue
                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type", "")

                    # Text output
                    if etype == "response.output_text.delta":
                        content_parts.append(event.get("delta", ""))
                    # Function call completed (collect from response.completed instead
                    # to avoid duplicates -- intermediate events lack the name field)
                    # Response completed -- extract usage
                    elif etype == "response.completed":
                        r = event.get("response", {})
                        u = r.get("usage", {})
                        usage_out.update({
                            "prompt_tokens": u.get("input_tokens", 0),
                            "completion_tokens": u.get("output_tokens", 0),
                        })
                        # Also extract any output we might have missed
                        for item in r.get("output", []):
                            if item.get("type") == "message":
                                for block in item.get("content", []):
                                    if block.get("type") == "output_text":
                                        t = block.get("text", "")
                                        if t and t not in "".join(content_parts):
                                            content_parts.append(t)
                            elif item.get("type") == "function_call":
                                tc = {
                                    "id": item.get("call_id", item.get("id", "")),
                                    "name": item.get("name", ""),
                                    "arguments": item.get("arguments", "{}"),
                                }
                                if tc not in tool_calls_out:
                                    tool_calls_out.append(tc)

        return {
            "content": "".join(content_parts),
            "tool_calls": tool_calls_out,
            "usage": usage_out,
            "model": model if not model.startswith("codex/") else model[6:],
        }

    try:
        return await _stream_request(headers)
    except RuntimeError as e:
        if "401" in str(e):
            tokens = await _refresh_codex_token()
            if tokens:
                headers["Authorization"] = f"Bearer {tokens['access_token']}"
                return await _stream_request(headers)
        raise


def _parse_codex_response(data: dict, model: str) -> dict:
    """Parse Responses API response into standard format."""
    content = ""
    tool_calls = []
    for item in data.get("output", []):
        itype = item.get("type", "")
        if itype == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    content += block.get("text", "")
        elif itype == "function_call":
            tool_calls.append({
                "id": item.get("call_id", item.get("id", "")),
                "name": item.get("name", ""),
                "arguments": item.get("arguments", "{}"),
            })
    usage = data.get("usage", {})
    return {
        "content": content,
        "tool_calls": tool_calls,
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
        },
        "model": model,
    }
