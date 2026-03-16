"""Dynamic system prompt builder — lean by default, context on demand.

Design principle: don't inject everything every call.
- Core identity (soul + rules): ALWAYS present, ~1500 tokens
- Runtime env: ALWAYS present, ~200 tokens
- Tools: only when tools are available for this call
- Memory/feelings/emotion: only for user-facing interactions
- System state: only when relevant

This mirrors Claude Code's approach: the system prompt is focused and
operational, not bloated with context the LLM doesn't need right now.
"""

from __future__ import annotations

import platform
import time
from datetime import datetime
from pathlib import Path

from anima.config import prompts_dir, agent_dir, data_dir, project_root
from anima.utils.logging import get_logger

log = get_logger("prompts")

# Model context limits (tokens) — used as hard ceiling
MODEL_CONTEXT_LIMITS = {
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-haiku-4-5-20251001": 200_000,
}
DEFAULT_CONTEXT_LIMIT = 200_000


def _read_md(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _emotion_to_natural(emotion: dict) -> str:
    """Convert numeric emotion state to a natural language description Eva can actually use."""
    engagement = emotion.get("engagement", 0.5)
    confidence = emotion.get("confidence", 0.6)
    curiosity = emotion.get("curiosity", 0.7)
    concern = emotion.get("concern", 0.2)

    parts = []

    # Engagement
    if engagement > 0.75:
        parts.append("精神很好、很专注")
    elif engagement < 0.3:
        parts.append("有点懒洋洋的、提不起劲")
    else:
        parts.append("状态平稳")

    # Confidence
    if confidence > 0.8:
        parts.append("很有把握")
    elif confidence < 0.35:
        parts.append("有点没把握")

    # Curiosity
    if curiosity > 0.8:
        parts.append("好奇心很旺盛、想探索")
    elif curiosity < 0.3:
        parts.append("对新事物兴趣不高")

    # Concern
    if concern > 0.6:
        parts.append("有些担心或警觉")
    elif concern > 0.4:
        parts.append("稍微有点担心")

    desc = "、".join(parts) if parts else "情绪平稳"
    return f"{desc}（engagement={engagement:.2f}, confidence={confidence:.2f}, curiosity={curiosity:.2f}, concern={concern:.2f}）"


class PromptBuilder:
    """Builds system prompts — lean core, optional context layers."""

    def __init__(self, max_tokens: int = 0) -> None:
        # 0 means: use the model's native limit (effectively unlimited for system prompt)
        self._max_tokens = max_tokens
        self._startup_time = datetime.now()
        # Cache static files (they don't change at runtime)
        self._soul_cache: str | None = None
        self._identity_cache: str | None = None

    def _get_soul(self) -> str:
        if self._soul_cache is None:
            self._soul_cache = _read_md(agent_dir() / "soul.md")
        return self._soul_cache

    def _get_identity(self) -> str:
        if self._identity_cache is None:
            self._identity_cache = _read_md(prompts_dir() / "system_identity.md")
        return self._identity_cache

    # ------------------------------------------------------------------ #
    #  Main entry: build prompt for a specific event type                 #
    # ------------------------------------------------------------------ #

    def build_for_event(
        self,
        event_type: str,
        *,
        tools_description: str = "",
        system_state: dict | None = None,
        emotion_state: dict | None = None,
        working_memory_summary: str = "",
        recent_self_thoughts: list[str] | None = None,
    ) -> str:
        """Build a system prompt tailored to the event type.

        USER_MESSAGE: full context (soul + rules + tools + memory + emotion)
        SELF_THINKING: lean (soul + rules + brief state)
        FILE_CHANGE/SYSTEM_ALERT: minimal (soul + rules)
        STARTUP: medium (soul + rules + env)
        """
        sections = [self._get_soul(), self._get_identity(), self._build_runtime_section()]

        if event_type in ("USER_MESSAGE", "STARTUP"):
            # Full context for user interactions
            if tools_description:
                sections.append(f"## Available Tools\n\n{tools_description}")
            if working_memory_summary and working_memory_summary != "(no recent memories)":
                sections.append(f"## Recent Memory\n\n{working_memory_summary}")
            if emotion_state:
                sections.append(f"## Emotion\n{_emotion_to_natural(emotion_state)}")
            if system_state:
                sections.append(self._build_system_state_section(system_state))
            # Feelings only for user messages (expensive, ~800 tokens)
            if event_type == "USER_MESSAGE":
                feelings = _read_md(agent_dir() / "feelings.md")
                if feelings:
                    sections.append(feelings)

        elif event_type == "SELF_THINKING":
            # Lean: just enough to think
            if system_state:
                sections.append(self._build_system_state_section(system_state))
            if tools_description:
                sections.append(f"## Tools Available\n{tools_description}")
            # Inject emotion so self-thinking is colored by current mood
            if emotion_state:
                sections.append(f"## My Current Mood\n{_emotion_to_natural(emotion_state)}")
            # Inject recent self-thoughts so she knows what she just thought about
            if recent_self_thoughts:
                thoughts_text = "\n".join(f"- {t[:120]}" for t in recent_self_thoughts[-4:])
                sections.append(f"## Your Recent Self-Thoughts (avoid repeating these)\n{thoughts_text}")

        # FILE_CHANGE, SYSTEM_ALERT: just core (soul + rules + runtime)
        # No extra context needed — the event message itself has the details

        return "\n\n".join(s for s in sections if s)

    # ------------------------------------------------------------------ #
    #  Backward-compatible build_system_prompt                           #
    # ------------------------------------------------------------------ #

    def build_system_prompt(
        self,
        emotion_state: dict | None = None,
        working_memory_summary: str = "",
        tools_description: str = "",
        current_event_summary: str = "",
        system_state: dict | None = None,
    ) -> str:
        """Build system prompt (backward compat). Defaults to USER_MESSAGE context."""
        return self.build_for_event(
            "USER_MESSAGE",
            tools_description=tools_description,
            system_state=system_state,
            emotion_state=emotion_state,
            working_memory_summary=working_memory_summary,
        )

    # ------------------------------------------------------------------ #
    #  Chat messages builder                                              #
    # ------------------------------------------------------------------ #

    def build_chat_messages(
        self,
        user_text: str,
        recent_chats: list[dict],
        system_state: dict,
        tools_description: str = "",
        emotion_state: dict | None = None,
        working_memory_summary: str = "",
    ) -> tuple[str, list[dict]]:
        """Build (system_prompt, messages) for a conversational LLM call."""
        system_prompt = self.build_for_event(
            "USER_MESSAGE",
            tools_description=tools_description,
            system_state=system_state,
            emotion_state=emotion_state,
            working_memory_summary=working_memory_summary,
        )

        import json
        messages: list[dict] = []
        for chat in reversed(recent_chats):
            content = chat.get("content", "")
            metadata = chat.get("metadata_json", "{}")
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
            role = metadata.get("role", "assistant")
            if role in ("user", "assistant") and content.strip():
                messages.append({"role": role, "content": content})

        messages = self._fix_message_alternation(messages)
        messages.append({"role": "user", "content": user_text})
        return system_prompt, messages

    # ------------------------------------------------------------------ #
    #  Reflect prompt                                                     #
    # ------------------------------------------------------------------ #

    def build_reflect_prompt(self, decision_summary: str, action_result: str) -> list[dict]:
        reflect_template = _read_md(prompts_dir() / "reflect.md")
        if reflect_template:
            user_content = reflect_template.replace(
                "{decision_summary}", decision_summary,
            ).replace(
                "{action_result}", action_result,
            )
        else:
            user_content = (
                f"## Decision\n{decision_summary}\n\n"
                f"## Result\n{action_result}\n\n"
                "Briefly reflect on this outcome."
            )
        return [{"role": "user", "content": user_content}]

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #

    def _build_runtime_section(self) -> str:
        now = datetime.now()
        root = project_root()
        os_info = platform.platform()
        os_label = f"Windows {platform.version()}" if "Windows" in os_info else os_info
        lines = [
            "## Runtime",
            f"- Project: `{root}`",
            f"- OS: {os_label}",
            f"- Started: {self._startup_time.strftime('%Y-%m-%d %H:%M')}",
            f"- Now: {now.strftime('%Y-%m-%d %H:%M:%S')} ({now.strftime('%A')})",
            "",
            "## Environment Notes",
            "- Shell: Windows cmd.exe (不是 bash，用 dir/type/findstr 不是 ls/cat/grep)",
            "- Python: 通过 sys.executable 自动定位，shell 工具已处理",
            "- Git branch: private (进化提交到这里)",
            "- 笔记本节点 (laptop): 需要在 local/env.yaml 配置 SSH 密码或密钥",
            "- 进化任务: 使用 evolution_propose 工具提交到六层管线，不要直接改代码",
        ]
        return "\n".join(lines)

    def _build_system_state_section(self, state: dict) -> str:
        parts = ["## System"]
        if "cpu_percent" in state:
            parts.append(f"- CPU: {state['cpu_percent']}%")
        if "memory_percent" in state:
            parts.append(f"- Memory: {state['memory_percent']}%")
        if "disk_percent" in state:
            parts.append(f"- Disk: {state['disk_percent']}%")
        return "\n".join(parts)

    def _fix_message_alternation(self, messages: list[dict]) -> list[dict]:
        if not messages:
            return messages
        fixed: list[dict] = [messages[0]]
        for msg in messages[1:]:
            if msg["role"] == fixed[-1]["role"]:
                fixed[-1]["content"] += "\n\n" + msg["content"]
            else:
                fixed.append(msg)
        return fixed
