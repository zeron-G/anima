"""Tool orchestration — executes tools and manages the tool-use loop.

Extracted from the old monolithic AgenticLoop (H-24 refactor).
Responsibilities:
  - Tool schema generation for LLM
  - Dynamic tool selection based on event type
  - Parallel tool execution with result formatting
  - Streaming LLM tool-use flow
  - Max turns control
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from anima.tools.executor import ToolExecutor
from anima.tools.registry import ToolRegistry
from anima.utils.logging import get_logger

log = get_logger("tool_orchestrator")

MAX_TURNS = 50  # Maximum tool-use loop iterations

# ------------------------------------------------------------------ #
#  Tool subsets for dynamic selection                                  #
# ------------------------------------------------------------------ #
# Each event type only sees tools relevant to its task, reducing token
# usage and preventing the LLM from invoking inappropriate tools.

_SELF_THINKING_TOOLS = frozenset({
    "read_file", "system_info", "search",
    "glob_search", "grep_search", "save_note", "get_datetime",
    "update_feelings", "list_jobs",
    "read_email", "env_search", "list_directory",
    "update_user_profile",
})

# Evolution cycle gets read/analysis tools PLUS evolution-specific tools
_EVOLUTION_CYCLE_TOOLS = frozenset({
    "read_file", "system_info", "search",
    "glob_search", "grep_search", "save_note", "get_datetime",
    "list_directory", "env_search", "shell",
    "evolution_propose", "evolution_status",
    "evolution_add_goal", "evolution_list_goals", "evolution_record_lesson",
})

# ── Three-axis tool subsets ──
_HUMAN_AXIS_TOOLS = frozenset({
    "read_file", "update_user_profile", "update_feelings",
    "save_note", "get_datetime", "update_personality",
})

_SELF_AXIS_TOOLS = frozenset({
    "read_file", "save_note", "get_datetime",
    "update_feelings", "update_personality", "mark_golden_reply",
    "glob_search", "update_user_profile",
})

_WORLD_AXIS_TOOLS = frozenset({
    "read_file", "system_info", "get_datetime",
    "read_email", "env_search", "list_directory",
    "glob_search", "grep_search", "save_note", "update_feelings",
})

_STARTUP_TOOLS = frozenset({
    "system_info", "get_datetime", "read_file", "shell",
})

_IDLE_TASK_TOOLS = frozenset({
    "shell", "read_file", "write_file", "system_info", "search",
    "glob_search", "grep_search", "save_note", "get_datetime",
    "update_feelings", "audit_run", "issue_manage", "list_jobs",
    "read_email", "env_search", "list_directory", "edit_file",
    "github",
})

_SCHEDULED_TASK_TOOLS = frozenset({
    "shell", "read_file", "write_file", "system_info", "search",
    "glob_search", "grep_search", "save_note", "get_datetime",
    "update_feelings", "list_jobs", "read_email", "send_email",
    "env_search", "list_directory", "edit_file", "github",
    "web_fetch", "google",
})

# Heavy tools that should only appear for user messages
# (expensive or externally visible)
_USER_ONLY_TOOLS = frozenset({
    "spawn_agent", "check_agent", "wait_agent", "list_agents",
    "claude_code", "self_repair", "delegate_task",
    "remote_exec", "remote_write_file",
    "schedule_job", "cancel_job", "enable_job",
    "evolution_propose", "evolution_status", "evolution_add_goal",
    "evolution_list_goals", "evolution_record_lesson",
    "web_fetch", "google", "send_email",
})

# Keywords that hint at tool relevance for user messages.
# If none match, all tools are provided (safe default).
_MESSAGE_TOOL_HINTS: list[tuple[frozenset[str], list[str]]] = [
    # (keywords_in_message, tool_names_to_include)
    (frozenset({"email", "mail", "inbox"}), ["read_email", "send_email"]),
    (frozenset({"schedule", "cron", "job", "timer"}), ["schedule_job", "list_jobs", "cancel_job", "enable_job"]),
    (frozenset({"github", "issue", "pr", "pull request", "commit"}), ["github"]),
    (frozenset({"agent", "spawn", "background"}), ["spawn_agent", "check_agent", "wait_agent", "list_agents"]),
    (frozenset({"remote", "laptop", "node", "delegate"}), ["remote_exec", "remote_write_file", "delegate_task"]),
    (frozenset({"evolution", "evolve", "improve", "upgrade"}),
     ["evolution_propose", "evolution_status", "evolution_add_goal", "evolution_list_goals", "evolution_record_lesson"]),
    (frozenset({"search", "find", "google", "web"}), ["web_fetch", "google", "glob_search", "grep_search"]),
    (frozenset({"audit", "code quality", "lint"}), ["audit_run", "audit_status", "issue_manage"]),
]

# ------------------------------------------------------------------ #
#  Category-based tool groups for intent-driven selection              #
# ------------------------------------------------------------------ #
# Tool categories for intent-based selection
_TOOL_CATEGORIES = {
    "CORE": frozenset({"shell", "read_file", "write_file", "system_info", "get_datetime", "search", "glob_search"}),
    "CODE": frozenset({"edit_file", "shell", "read_file", "write_file", "search", "glob_search", "claude_code"}),
    "NETWORK": frozenset({"remote_exec", "remote_write", "delegate_task"}),
    "COMMS": frozenset({"send_email", "read_email", "github"}),
    "AGENTS": frozenset({"spawn_agent", "spawn_internal", "spawn_claude_code", "check_agent", "wait_agent"}),
    "MEMORY": frozenset({"update_feelings", "update_user_profile", "save_note", "env_search"}),
    "SYSTEM": frozenset({"system_info", "list_jobs", "add_job", "remove_job", "audit_run", "issue_manage"}),
    "EVOLUTION": frozenset({"evolution_propose"}),
}

# Keywords that trigger inclusion of specific categories
_INTENT_KEYWORDS: dict[str, list[str]] = {
    "CODE": ["代码", "code", "bug", "fix", "修改", "编辑", "文件", "file", "edit", "refactor", "实现", "implement"],
    "NETWORK": ["laptop", "节点", "远程", "remote", "node", "分布式", "distributed"],
    "COMMS": ["邮件", "email", "github", "issue", "pr", "pull request"],
    "AGENTS": ["agent", "子任务", "并行", "parallel", "complex", "复杂"],
    "EVOLUTION": ["进化", "evolution", "improve", "优化", "升级"],
    "SYSTEM": ["scheduled", "cron", "audit", "审计", "issue"],
}


class ToolOrchestrator:
    """Manages tool execution within the agentic loop.

    Owns:
      - Which tools are visible to the LLM (dynamic selection)
      - Executing tool calls in parallel
      - Formatting tool results back into message blocks
      - The streaming LLM call wrapper
      - The human-readable tools description for prompt compilation
    """

    def __init__(self, executor: ToolExecutor, registry: ToolRegistry) -> None:
        self._executor = executor
        self._registry = registry

    # ------------------------------------------------------------------ #
    #  Tool schema generation (dynamic selection)                         #
    # ------------------------------------------------------------------ #

    def get_tool_schemas(
        self,
        event_type: str = "USER_MESSAGE",
        message: str = "",
    ) -> list[dict]:
        """Get tool schemas filtered by event type.

        Dynamic tool selection reduces token usage and LLM confusion
        by hiding irrelevant tools.  Falls back to all tools when the
        event type is unrecognized (safe default).
        """
        all_tools = self._registry.list_tools()

        if event_type == "SELF_THINKING":
            if "EVOLUTION CYCLE" in message or "evolution_propose" in message:
                tools = [t for t in all_tools if t.name in _EVOLUTION_CYCLE_TOOLS]
            elif "Human Axis" in message:
                tools = [t for t in all_tools if t.name in _HUMAN_AXIS_TOOLS]
            elif "Self Axis" in message or "Personality Reflect" in message or "Curate Examples" in message:
                tools = [t for t in all_tools if t.name in _SELF_AXIS_TOOLS]
            elif "World Axis" in message or "Late Night" in message:
                tools = [t for t in all_tools if t.name in _WORLD_AXIS_TOOLS]
            else:
                tools = [t for t in all_tools if t.name in _SELF_THINKING_TOOLS]
        elif event_type == "STARTUP":
            tools = [t for t in all_tools if t.name in _STARTUP_TOOLS]
        elif event_type == "IDLE_TASK":
            tools = [t for t in all_tools if t.name in _IDLE_TASK_TOOLS]
        elif event_type == "SCHEDULED_TASK":
            tools = [t for t in all_tools if t.name in _SCHEDULED_TASK_TOOLS]
        elif event_type == "USER_MESSAGE":
            tools = self._filter_for_user_message(all_tools, message)
        else:
            # TASK_DELEGATE, EVOLUTION, FOLLOW_UP, etc. — full toolset
            tools = list(all_tools)

        return [
            {
                "name": s.name,
                "description": s.description,
                "input_schema": s.parameters or {"type": "object", "properties": {}},
            }
            for s in tools
        ]

    def get_all_tool_schemas(self) -> list[dict]:
        """Get schemas for ALL registered tools (no filtering).

        Used by legacy code paths that do their own selection.
        """
        return [
            {
                "name": s.name,
                "description": s.description,
                "input_schema": s.parameters or {"type": "object", "properties": {}},
            }
            for s in self._registry.list_tools()
        ]

    def _filter_for_user_message(
        self,
        all_tools: list,
        message: str,
    ) -> list:
        """Filter tools based on user message intent using category matching.

        Strategy:
          1. Always include CORE and MEMORY categories
          2. Match intent keywords to add specialized categories
          3. Also check legacy _MESSAGE_TOOL_HINTS for fine-grained matches
          4. Fall back to all tools if nothing matched (safe default)
        """
        if not message:
            return list(all_tools)

        msg_lower = message.lower()

        # Always include CORE and MEMORY tools
        included_categories = {"CORE", "MEMORY"}

        # Match intent keywords to categories
        for category, keywords in _INTENT_KEYWORDS.items():
            if any(kw in msg_lower for kw in keywords):
                included_categories.add(category)

        # Build the included tool names set from categories
        included_names: set[str] = set()
        for cat in included_categories:
            included_names.update(_TOOL_CATEGORIES.get(cat, set()))

        # Also check legacy keyword hints for fine-grained tool inclusion
        any_hint_matched = False
        for keywords, tool_names in _MESSAGE_TOOL_HINTS:
            if any(kw in msg_lower for kw in keywords):
                included_names.update(tool_names)
                any_hint_matched = True

        # If only default categories matched and no hints fired,
        # fall back to full toolset (safe default)
        if included_categories == {"CORE", "MEMORY"} and not any_hint_matched:
            return list(all_tools)

        # Also include tools that are NOT in _USER_ONLY_TOOLS (baseline)
        for t in all_tools:
            if t.name not in _USER_ONLY_TOOLS:
                included_names.add(t.name)

        filtered = [t for t in all_tools if t.name in included_names]

        # Log the selection
        log.debug(
            "Tool selection: %d/%d tools for message (categories: %s)",
            len(filtered), len(all_tools), included_categories,
        )

        return filtered if filtered else all_tools  # Fallback to all if nothing matched

    # ------------------------------------------------------------------ #
    #  Human-readable tools description (for system prompt)              #
    # ------------------------------------------------------------------ #

    def build_tools_description(self, event_type: str = "USER_MESSAGE", message: str = "") -> str:
        """Build a human-readable Markdown description of available tools.

        Used by PromptCompiler to embed tool documentation in the
        system prompt, giving the LLM context about what's available
        beyond the function-calling schema.

        Only describes tools that get_tool_schemas() would return for
        the given event_type and message, keeping prompt and schemas aligned.
        """
        schemas = self.get_tool_schemas(event_type, message)
        available_names = {s["name"] for s in schemas}
        lines: list[str] = []
        for spec in self._registry.list_tools():
            if spec.name not in available_names:
                continue
            params = spec.parameters.get("properties", {})
            required = spec.parameters.get("required", [])
            pp: list[str] = []
            for pn, pi in params.items():
                r = " (required)" if pn in required else ""
                pp.append(
                    f"    - `{pn}` ({pi.get('type', 'any')}{r}): "
                    f"{pi.get('description', '')}"
                )
            ps = "\n".join(pp) if pp else "    (no parameters)"
            lines.append(f"**{spec.name}** -- {spec.description}\n{ps}")
        return "\n\n".join(lines) if lines else "(no tools)"

    # ------------------------------------------------------------------ #
    #  Tool execution                                                     #
    # ------------------------------------------------------------------ #

    async def execute_tools(
        self,
        tool_calls: list[dict],
        status_callback: Callable[[dict], Any] | None = None,
    ) -> list[dict]:
        """Execute tool calls in parallel and return formatted results.

        Parameters
        ----------
        tool_calls:
            List of tool call dicts from the LLM response.
            Each has: name, arguments (str or dict), id.
        status_callback:
            Optional callback for status updates (stage, detail, tool).

        Returns
        -------
        List of tool_result dicts ready to be appended as a user
        message in the conversation: [{"type": "tool_result",
        "tool_use_id": ..., "content": ...}, ...]
        """

        async def _exec_one(tc: dict) -> dict:
            name = tc["name"]
            try:
                args = (
                    json.loads(tc["arguments"])
                    if isinstance(tc["arguments"], str)
                    else tc["arguments"]
                )
            except (json.JSONDecodeError, TypeError):
                args = {}

            if status_callback:
                try:
                    _raw = json.dumps(args, ensure_ascii=False)[:60]
                    detail = f"{name}({_raw})"
                    status_callback({
                        "stage": "executing",
                        "detail": detail,
                        "tool": name,
                    })
                except Exception:
                    pass  # Status display must never crash tool execution

            result = await self._executor.execute(name, args)
            result_text = self.format_result(name, result)

            if result.get("success"):
                log.info("Tool %s succeeded", name)
            else:
                err = result.get("error", "unknown error")
                # Include key arg in the log for easier debugging
                args_hint = ""
                if name == "shell" and "command" in args:
                    args_hint = f" | cmd={args['command'][:80]!r}"
                elif args:
                    first_key = next(iter(args))
                    args_hint = f" | {first_key}={str(args[first_key])[:60]!r}"
                log.warning("Tool %s failed: %s%s", name, err, args_hint)

            if status_callback:
                try:
                    ok = "ok" if result.get("success") else "failed"
                    status_callback({
                        "stage": "tool_done",
                        "detail": f"{name}: {ok}",
                        "tool": name,
                    })
                except Exception:
                    pass  # Status display must never crash tool execution

            return {
                "type": "tool_result",
                "tool_use_id": tc.get("id", name),
                "content": result_text,
            }

        results = list(await asyncio.gather(*[_exec_one(tc) for tc in tool_calls]))
        return results

    # ------------------------------------------------------------------ #
    #  Result formatting                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def format_result(tool_name: str, result: dict) -> str:
        """Format a raw tool result dict into a string for the LLM.

        Handles shell output (stdout/stderr/returncode), dicts (JSON),
        plain strings, and error cases.
        """
        if not result.get("success"):
            return f"Error: {result.get('error', 'unknown')}"

        raw = result.get("result")

        if isinstance(raw, dict):
            parts: list[str] = []
            if raw.get("stdout"):
                parts.append(raw["stdout"])
            if raw.get("stderr"):
                parts.append(f"[stderr] {raw['stderr']}")
            if raw.get("returncode") is not None and raw["returncode"] != 0:
                parts.append(f"[exit code: {raw['returncode']}]")
            if parts:
                return "\n".join(parts)
            return json.dumps(raw, ensure_ascii=False, indent=2)

        if isinstance(raw, str):
            return raw

        return str(raw) if raw is not None else "(no output)"

    # ------------------------------------------------------------------ #
    #  Assistant block building                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def build_assistant_blocks(
        text: str,
        tool_calls: list[dict],
        raw_resp: dict | None = None,
    ) -> list[dict]:
        """Build the assistant message content blocks for a tool-use turn.

        The Anthropic Messages API expects the assistant message to contain
        a list of content blocks: text blocks and tool_use blocks.  This
        method constructs that list from the LLM response.

        Parameters
        ----------
        text:
            Any text content the LLM returned alongside tool calls.
        tool_calls:
            Tool call dicts from the LLM response.
        raw_resp:
            The full raw response dict (reserved for future use).

        Returns
        -------
        List of content blocks (text + tool_use).
        """
        blocks: list[dict] = []

        if text:
            blocks.append({"type": "text", "text": text})

        for tc in tool_calls:
            try:
                inp = (
                    json.loads(tc["arguments"])
                    if isinstance(tc["arguments"], str)
                    else tc["arguments"]
                )
            except (json.JSONDecodeError, TypeError):
                inp = {}
            blocks.append({
                "type": "tool_use",
                "id": tc.get("id", tc["name"]),
                "name": tc["name"],
                "input": inp,
            })

        return blocks

    # ------------------------------------------------------------------ #
    #  Streaming LLM call                                                 #
    # ------------------------------------------------------------------ #

    async def stream_llm_call(
        self,
        llm_router,
        messages: list[dict],
        tools: list[dict],
        tier: int,
        stream_callback: Callable[[str, str], None] | None = None,
    ) -> dict | None:
        """Execute a streaming LLM call, pushing text chunks via callback.

        Returns the same response dict format as LLMRouter.call_with_tools()
        for compatibility with the agentic loop (tool execution, etc.).

        Parameters
        ----------
        llm_router:
            The LLMRouter instance to call.
        messages:
            Conversation messages for the LLM.
        tools:
            Tool schemas (from get_tool_schemas).
        tier:
            Model tier (1=high quality, 2=cost effective).
        stream_callback:
            Callback receiving (chunk: str, event_type: str).
            event_type is "text" for content or "status" for tool starts.

        Returns
        -------
        Response dict with "content", "tool_calls", "usage" keys,
        or None on failure.
        """

        content = ""
        tool_calls: list[dict] = []
        usage: dict = {}
        got_response = False

        async for event in llm_router.call_with_tools_stream(messages, tools, tier):
            if event.type == "text_delta":
                content += event.text
                if stream_callback:
                    stream_callback(event.text, "text")

            elif event.type == "tool_use_start":
                if stream_callback:
                    tool_name = event.tool_call.get("name", "?")
                    stream_callback(f"\n\U0001f527 {tool_name}...", "status")

            elif event.type == "tool_use_done":
                tool_calls.append(event.tool_call)

            elif event.type == "message_complete":
                content = event.content
                tool_calls = event.tool_calls
                usage = event.usage
                got_response = True

            elif event.type == "error":
                log.warning("Streaming error: %s", event.error)
                return None

        if not got_response and not content and not tool_calls:
            return None

        # End the streaming line
        if stream_callback and content and not tool_calls:
            stream_callback("\n", "text")

        return {
            "content": content,
            "tool_calls": tool_calls,
            "usage": usage,
        }

    # ------------------------------------------------------------------ #
    #  Full tool-use loop                                                 #
    # ------------------------------------------------------------------ #

    async def run_tool_loop(
        self,
        llm_router,
        messages: list[dict],
        tools: list[dict],
        tier: int,
        *,
        max_turns: int = MAX_TURNS,
        stream_callback: Callable[[str, str], None] | None = None,
        status_callback: Callable[[dict], Any] | None = None,
        use_streaming: bool = False,
    ) -> dict:
        """Run the multi-turn tool-use loop until the LLM finishes.

        This is the core agentic loop: call LLM, execute tools, feed
        results back, repeat until the LLM returns a final text response
        or we hit max_turns.

        Parameters
        ----------
        llm_router:
            The LLMRouter instance.
        messages:
            Initial conversation messages (will be mutated in-place
            as tool turns are appended).
        tools:
            Tool schemas for the LLM.
        tier:
            Model tier for routing.
        max_turns:
            Maximum number of tool-use iterations.
        stream_callback:
            For streaming text chunks to the client.
        status_callback:
            For status updates during tool execution.
        use_streaming:
            Whether to use streaming for the LLM call.

        Returns
        -------
        Dict with:
          - "content": final text response (str)
          - "tool_calls_made": total number of tool calls executed (int)
          - "turns": number of loop iterations (int)
          - "error": error message if loop failed (str | None)
        """
        from anima.utils.invariants import require
        require(llm_router is not None, "llm_router is None in run_tool_loop")
        require(messages, "messages list is empty in run_tool_loop")

        total_tool_calls = 0
        turns = 0

        for turns in range(1, max_turns + 1):
            # Call LLM (streaming or regular)
            if use_streaming and stream_callback:
                resp = await self.stream_llm_call(
                    llm_router, messages, tools, tier,
                    stream_callback=stream_callback,
                )
            else:
                resp = await llm_router.call_with_tools(
                    messages=messages, tools=tools, tier=tier,
                )

            if resp is None:
                return {
                    "content": "",
                    "tool_calls_made": total_tool_calls,
                    "turns": turns,
                    "error": "LLM call failed",
                }

            content = resp.get("content", "")
            tool_calls = resp.get("tool_calls", [])

            if not tool_calls:
                # LLM is done — return final content
                return {
                    "content": content,
                    "tool_calls_made": total_tool_calls,
                    "turns": turns,
                    "error": None,
                }

            # Build assistant message with tool_use blocks
            assistant_blocks = self.build_assistant_blocks(
                content, tool_calls, resp,
            )
            messages.append({"role": "assistant", "content": assistant_blocks})

            # Execute tools in parallel
            tool_results = await self.execute_tools(
                tool_calls,
                status_callback=status_callback,
            )
            messages.append({"role": "user", "content": tool_results})

            total_tool_calls += len(tool_calls)

        # Exhausted max_turns
        log.warning(
            "Tool loop hit max turns (%d), %d tool calls made",
            max_turns, total_tool_calls,
        )
        return {
            "content": "",
            "tool_calls_made": total_tool_calls,
            "turns": turns,
            "error": f"Max turns ({max_turns}) exceeded",
        }
