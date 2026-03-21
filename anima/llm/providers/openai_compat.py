"""OpenAI Chat Completions API -- for local models and OpenAI cloud."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from anima.utils.logging import get_logger

log = get_logger("llm_providers")


async def _openai_completion(
    base_url: str,
    model_id: str | None,
    api_key: str | None,
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> dict:
    """Call any OpenAI-compatible endpoint.

    Used for: local llama.cpp, ollama, vLLM, OpenAI cloud, etc.
    Handles Anthropic->OpenAI message format conversion transparently.
    """
    # Convert messages from Anthropic format to OpenAI format
    api_messages = _anthropic_to_openai_messages(messages)

    payload: dict[str, Any] = {
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    if model_id:
        payload["model"] = model_id

    # Convert tool format
    openai_tools = _convert_tools_to_openai(tools)
    if openai_tools:
        payload["tools"] = openai_tools
        payload["tool_choice"] = "auto"

    headers: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    log.debug("OpenAI-compat call: %s (model=%s)", base_url, model_id or "default")

    # Serialize JSON as UTF-8 bytes explicitly (Windows GBK locale safety)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    # Local models can be slow (large prompt processing) but should never hang silently.
    # read=180s is generous for local generation; connect should be instant.
    _openai_timeout = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)
    async with httpx.AsyncClient(timeout=_openai_timeout) as client:
        resp = await client.post(
            f"{base_url}/v1/chat/completions",
            headers=headers,
            content=body,
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"OpenAI API error {resp.status_code}: {resp.text[:500]}"
        )

    return _parse_openai_response(resp.json(), model_id or "local")


# -- Message format conversion --

def _flatten_content(content: Any) -> str:
    """Normalize Anthropic content blocks to plain string.

    Anthropic: [{"type":"text","text":"..."}, {"type":"tool_result",...}]
    OpenAI: plain string
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type", "")
                if btype == "text":
                    parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    parts.append(
                        f"[Tool call: {block.get('name', '?')}"
                        f"({json.dumps(block.get('input', {}))})]"
                    )
                elif btype == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, list):
                        inner = " ".join(
                            b.get("text", "")
                            for b in inner
                            if isinstance(b, dict)
                        )
                    parts.append(f"[Tool result: {inner}]")
                else:
                    parts.append(str(block))
        return "\n".join(p for p in parts if p)
    return str(content) if content else ""


def _anthropic_to_openai_messages(messages: list[dict]) -> list[dict]:
    """Convert Anthropic-format messages to OpenAI-format.

    Handles: content blocks -> strings, tool role -> user, system merging,
    consecutive same-role merging, empty message removal.
    """
    converted = []
    system_parts: list[str] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = _flatten_content(msg.get("content", ""))
        if not content.strip():
            continue

        if role == "system":
            system_parts.append(content)
        elif role == "tool":
            converted.append({
                "role": "user",
                "content": f"[Tool Result] {content}",
            })
        else:
            converted.append({"role": role, "content": content})

    # Merge consecutive same-role messages
    merged: list[dict] = []
    for msg in converted:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] += "\n\n" + msg["content"]
        else:
            merged.append(msg)

    # Inject system as first user message (most local models handle
    # system role poorly; prepending to first user msg is more reliable)
    if system_parts:
        system_text = "\n\n".join(system_parts)
        if merged and merged[0]["role"] == "user":
            merged[0]["content"] = (
                f"[System Instructions]\n{system_text}\n\n"
                f"[User Message]\n{merged[0]['content']}"
            )
        else:
            merged.insert(0, {"role": "user", "content": system_text})
            # Fix alternation if next is also user
            if len(merged) > 1 and merged[1]["role"] == "user":
                merged[0]["content"] += "\n\n" + merged[1]["content"]
                merged.pop(1)

    if not merged:
        merged = [{"role": "user", "content": "(empty)"}]

    return merged


# -- Tool format conversion --

def _convert_tools_to_openai(
    tools: list[dict] | None,
) -> list[dict] | None:
    """Convert Anthropic tool schema to OpenAI function-calling format.

    Anthropic: {"name", "description", "input_schema": {...}}
    OpenAI:    {"type":"function", "function": {"name", "description", "parameters"}}
    """
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get(
                    "input_schema", t.get("parameters", {})
                ),
            },
        }
        for t in tools
    ]


# -- Response parsing --

def _parse_openai_response(data: dict, model_name: str) -> dict:
    """Parse OpenAI Chat Completions response into standard format."""
    choices = data.get("choices", [])
    if not choices:
        return {
            "content": "",
            "tool_calls": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "model": model_name,
        }

    msg = choices[0].get("message", {})
    content = msg.get("content", "") or ""

    # Strip <think>...</think> blocks (Qwen/DeepSeek thinking mode)
    if "<think>" in content:
        content = re.sub(
            r"<think>.*?</think>\s*", "", content, flags=re.DOTALL
        ).strip()

    tool_calls = []
    for tc in msg.get("tool_calls", []):
        fn = tc.get("function", {})
        tool_calls.append({
            "id": tc.get("id", ""),
            "name": fn.get("name", ""),
            "arguments": fn.get("arguments", "{}"),
        })

    usage = data.get("usage", {})
    return {
        "content": content,
        "tool_calls": tool_calls,
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
        },
        "model": model_name,
    }
