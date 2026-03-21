"""Streaming completion API -- Anthropic SSE and OpenAI-compatible SSE."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from anima.llm.providers.constants import (
    ANTHROPIC_API_BASE, ANTHROPIC_VERSION, CLAUDE_CODE_IDENTITY,
    CLAUDE_CODE_VERSION, OAUTH_BETA_HEADERS,
    _LOCAL_LLM_BASE, _OPENAI_API_BASE,
)
from anima.llm.providers.auth import _get_anthropic_token, _is_oauth_token
from anima.llm.providers.message_convert import _fix_api_messages
from anima.llm.providers.openai_compat import (
    _anthropic_to_openai_messages, _convert_tools_to_openai,
)
from anima.llm.providers.local import _local_server


@dataclass
class StreamEvent:
    """A single event from a streaming LLM response.

    Event types:
      - text_delta:      Incremental text content (chunk in .text)
      - tool_use_start:  A tool call has started (tool name/id in .tool_call)
      - tool_input_delta: Incremental JSON for tool input
      - tool_use_done:   Tool input is complete
      - message_complete: Full response is done (.content, .tool_calls, .usage populated)
      - error:           An error occurred (.error populated)
    """
    type: str
    text: str = ""
    tool_call: dict = field(default_factory=dict)
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""
    error: str = ""


async def completion_stream(
    model: str,
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Streaming completion -- yields StreamEvent objects as they arrive.

    Same routing logic as completion(), but returns an async generator
    of StreamEvent instead of a single dict.

    Falls back to non-streaming (yields a single message_complete event)
    if the provider doesn't support streaming.
    """
    if model.startswith("local/"):
        base = os.environ.get("LOCAL_LLM_BASE_URL", _LOCAL_LLM_BASE)
        model_id = model.removeprefix("local/").strip() or None
        if not await _local_server.ensure_running(timeout=60):
            yield StreamEvent(type="error", error="Local LLM server failed to start")
            return
        _local_server.mark_used()
        async for event in _openai_completion_stream(
            base_url=base, model_id=model_id, api_key=None,
            messages=messages, max_tokens=max_tokens,
            temperature=temperature, tools=tools,
        ):
            yield event
        return

    if model.startswith("openai/"):
        base = os.environ.get("OPENAI_API_BASE", _OPENAI_API_BASE)
        model_id = model.removeprefix("openai/").strip()
        api_key = os.environ.get("OPENAI_API_KEY", "")
        async for event in _openai_completion_stream(
            base_url=base, model_id=model_id, api_key=api_key,
            messages=messages, max_tokens=max_tokens,
            temperature=temperature, tools=tools,
        ):
            yield event
        return

    # Default: Anthropic streaming
    async for event in _anthropic_completion_stream(
        model=model, messages=messages, max_tokens=max_tokens,
        temperature=temperature, tools=tools,
    ):
        yield event


# -- Anthropic streaming --

async def _anthropic_completion_stream(
    model: str,
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Stream from Anthropic Messages API via SSE."""
    token = _get_anthropic_token()
    if not token:
        yield StreamEvent(type="error", error="No Anthropic auth token")
        return

    if model.startswith("anthropic/"):
        model = model[len("anthropic/"):]

    is_oauth = _is_oauth_token(token)

    # Build headers (same as non-streaming)
    if is_oauth:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "anthropic-version": ANTHROPIC_VERSION,
            "anthropic-beta": OAUTH_BETA_HEADERS,
            "user-agent": f"claude-cli/{CLAUDE_CODE_VERSION}",
            "x-app": "cli",
            "anthropic-dangerous-direct-browser-access": "true",
            "accept": "text/event-stream",
        }
    else:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": token,
            "anthropic-version": ANTHROPIC_VERSION,
            "accept": "text/event-stream",
        }

    # Build messages (same as non-streaming)
    system_prompt = None
    api_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_prompt = msg["content"]
        else:
            content = msg.get("content", "")
            if isinstance(content, str) and not content.strip():
                continue
            if isinstance(content, list) and not content:
                continue
            api_messages.append(msg)

    api_messages = _fix_api_messages(api_messages)

    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": api_messages,
        "stream": True,
    }

    if is_oauth:
        blocks = [{"type": "text", "text": CLAUDE_CODE_IDENTITY}]
        if system_prompt:
            blocks.append({"type": "text", "text": system_prompt})
        payload["system"] = blocks
    elif system_prompt:
        payload["system"] = system_prompt

    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = {"type": "auto"}

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    # Streaming needs longer read timeout (response comes in chunks over minutes)
    _stream_timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

    # Accumulate full response for the final message_complete event
    full_content = ""
    tool_calls: list[dict] = []
    current_tool: dict = {}
    current_tool_input = ""
    usage_data: dict = {}

    try:
        async with httpx.AsyncClient(timeout=_stream_timeout) as client:
            async with client.stream(
                "POST",
                f"{ANTHROPIC_API_BASE}/v1/messages",
                headers=headers,
                content=body,
            ) as response:
                if response.status_code != 200:
                    error_body = ""
                    async for chunk in response.aiter_text():
                        error_body += chunk
                        if len(error_body) > 500:
                            break
                    yield StreamEvent(
                        type="error",
                        error=f"Anthropic API error {response.status_code}: {error_body[:500]}",
                    )
                    return

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = data.get("type", "")

                    if event_type == "content_block_start":
                        block = data.get("content_block", {})
                        if block.get("type") == "tool_use":
                            current_tool = {
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                            }
                            current_tool_input = ""
                            yield StreamEvent(
                                type="tool_use_start",
                                tool_call={"id": current_tool["id"], "name": current_tool["name"]},
                            )

                    elif event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        delta_type = delta.get("type", "")

                        if delta_type == "text_delta":
                            text = delta.get("text", "")
                            full_content += text
                            yield StreamEvent(type="text_delta", text=text)

                        elif delta_type == "input_json_delta":
                            partial = delta.get("partial_json", "")
                            current_tool_input += partial
                            yield StreamEvent(type="tool_input_delta", text=partial)

                    elif event_type == "content_block_stop":
                        if current_tool:
                            try:
                                args = json.loads(current_tool_input) if current_tool_input else {}
                            except json.JSONDecodeError:
                                args = {}
                            tool_calls.append({
                                "id": current_tool["id"],
                                "name": current_tool["name"],
                                "arguments": json.dumps(args),
                            })
                            yield StreamEvent(
                                type="tool_use_done",
                                tool_call={
                                    "id": current_tool["id"],
                                    "name": current_tool["name"],
                                    "arguments": json.dumps(args),
                                },
                            )
                            current_tool = {}
                            current_tool_input = ""

                    elif event_type == "message_delta":
                        delta_usage = data.get("usage", {})
                        if delta_usage:
                            usage_data["completion_tokens"] = delta_usage.get("output_tokens", 0)

                    elif event_type == "message_start":
                        msg_usage = data.get("message", {}).get("usage", {})
                        if msg_usage:
                            usage_data["prompt_tokens"] = msg_usage.get("input_tokens", 0)

                    elif event_type == "error":
                        yield StreamEvent(
                            type="error",
                            error=data.get("error", {}).get("message", "Unknown streaming error"),
                        )
                        return

        # Final message_complete event
        yield StreamEvent(
            type="message_complete",
            content=full_content,
            tool_calls=tool_calls,
            usage={
                "prompt_tokens": usage_data.get("prompt_tokens", 0),
                "completion_tokens": usage_data.get("completion_tokens", 0),
            },
            model=model,
        )

    except httpx.ReadTimeout:
        yield StreamEvent(type="error", error=f"Anthropic streaming read timeout ({model})")
    except httpx.ConnectTimeout:
        yield StreamEvent(type="error", error=f"Anthropic connection timeout ({model})")
    except Exception as e:
        yield StreamEvent(type="error", error=f"Anthropic streaming error: {str(e)[:300]}")


# -- OpenAI-compatible streaming --

async def _openai_completion_stream(
    base_url: str,
    model_id: str | None,
    api_key: str | None,
    messages: list[dict],
    max_tokens: int = 2048,
    temperature: float = 0.7,
    tools: list[dict] | None = None,
) -> AsyncIterator[StreamEvent]:
    """Stream from any OpenAI-compatible endpoint via SSE."""
    api_messages = _anthropic_to_openai_messages(messages)

    payload: dict[str, Any] = {
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    if model_id:
        payload["model"] = model_id

    openai_tools = _convert_tools_to_openai(tools)
    if openai_tools:
        payload["tools"] = openai_tools
        payload["tool_choice"] = "auto"

    h: dict[str, str] = {"Content-Type": "application/json; charset=utf-8"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    _stream_timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

    full_content = ""
    tool_calls: list[dict] = []
    # OpenAI streams tool calls across multiple chunks -- track by index
    tool_call_accum: dict[int, dict] = {}

    try:
        async with httpx.AsyncClient(timeout=_stream_timeout) as client:
            async with client.stream(
                "POST",
                f"{base_url}/v1/chat/completions",
                headers=h,
                content=body,
            ) as response:
                if response.status_code != 200:
                    error_body = ""
                    async for chunk in response.aiter_text():
                        error_body += chunk
                        if len(error_body) > 500:
                            break
                    yield StreamEvent(
                        type="error",
                        error=f"OpenAI API error {response.status_code}: {error_body[:500]}",
                    )
                    return

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    choices = data.get("choices", [])
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {})

                    # Text content
                    if "content" in delta and delta["content"]:
                        text = delta["content"]
                        # Strip <think> blocks from local models
                        if "<think>" not in full_content and "<think>" in text:
                            pass  # will strip at end
                        full_content += text
                        yield StreamEvent(type="text_delta", text=text)

                    # Tool calls (accumulated across chunks)
                    if "tool_calls" in delta:
                        for tc_delta in delta["tool_calls"]:
                            idx = tc_delta.get("index", 0)
                            if idx not in tool_call_accum:
                                tool_call_accum[idx] = {
                                    "id": tc_delta.get("id", ""),
                                    "name": tc_delta.get("function", {}).get("name", ""),
                                    "arguments": "",
                                }
                                yield StreamEvent(
                                    type="tool_use_start",
                                    tool_call={"id": tool_call_accum[idx]["id"],
                                               "name": tool_call_accum[idx]["name"]},
                                )
                            fn_delta = tc_delta.get("function", {})
                            if "arguments" in fn_delta:
                                tool_call_accum[idx]["arguments"] += fn_delta["arguments"]

        # Finalize tool calls
        for idx in sorted(tool_call_accum.keys()):
            tc = tool_call_accum[idx]
            tool_calls.append({
                "id": tc["id"],
                "name": tc["name"],
                "arguments": tc["arguments"],
            })
            yield StreamEvent(
                type="tool_use_done",
                tool_call=tc,
            )

        # Strip <think> blocks from final content
        if "<think>" in full_content:
            full_content = re.sub(
                r"<think>.*?</think>\s*", "", full_content, flags=re.DOTALL
            ).strip()

        yield StreamEvent(
            type="message_complete",
            content=full_content,
            tool_calls=tool_calls,
            usage={},  # OpenAI streaming doesn't always include usage
            model=model_id or "local",
        )

    except httpx.ReadTimeout:
        yield StreamEvent(type="error", error="OpenAI streaming read timeout")
    except Exception as e:
        yield StreamEvent(type="error", error=f"OpenAI streaming error: {str(e)[:300]}")
