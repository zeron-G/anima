"""Conversation compression — periodic + Compaction Flush modes.

Two trigger modes:
1. **Periodic**: every ``summary_interval`` messages, compress old messages
   into a rolling summary.
2. **Compaction Flush** (OpenClaw-inspired): when the buffer approaches the
   token budget, proactively compress the oldest turns.

LLM cost control:
- Uses Tier 2 (cheap model) for summaries.
- Checks budget before every LLM call.
- Degrades to rule-based truncation if budget is exhausted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from anima.utils.logging import get_logger

if TYPE_CHECKING:
    from anima.llm.router import LLMRouter

log = get_logger("summarizer")


class ConversationSummarizer:
    """Conversation compression with periodic + Compaction Flush modes.

    Parameters
    ----------
    llm_router:
        An ``LLMRouter`` instance (has ``.call(messages, tier)`` and
        ``.check_budget()``).
    summary_interval:
        Trigger periodic compression every *N* messages.
    keep_recent:
        Number of most-recent messages to keep verbatim after compression.
    """

    def __init__(
        self,
        llm_router: LLMRouter,
        summary_interval: int = 20,
        keep_recent: int = 10,
        save_path: Path | str | None = None,
    ) -> None:
        self._llm = llm_router
        self._summary: str = ""
        self._raw_buffer: list[dict] = []
        self._interval = summary_interval
        self._keep_recent = keep_recent
        self._counter = 0
        self._save_path: Path | None = Path(save_path) if save_path else None

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    async def add_message(
        self,
        role: str,
        content: str,
        is_self_thought: bool = False,
    ) -> None:
        """Add a message and trigger periodic compression if needed."""
        self._raw_buffer.append({
            "role": role,
            "content": content,
            "is_self_thought": is_self_thought,
        })
        self._counter += 1

        if self._counter >= self._interval:
            log.info(
                "Periodic compression triggered (counter=%d, buffer=%d)",
                self._counter, len(self._raw_buffer),
            )
            await self._compress()
            self._counter = 0

    def check_overflow(self, conversation_budget_tokens: int) -> bool:
        """Return ``True`` if buffer tokens exceed 85 % of *budget*."""
        estimated = self._estimate_buffer_tokens()
        threshold = int(conversation_budget_tokens * 0.85)
        overflow = estimated > threshold
        if overflow:
            log.warning(
                "Buffer overflow detected: %d tokens > %d (85%% of %d)",
                estimated, threshold, conversation_budget_tokens,
            )
        return overflow

    async def compaction_flush(self) -> None:
        """Emergency compression — keep fewer messages, compress aggressively."""
        keep = max(5, self._keep_recent // 2)
        log.info("Compaction flush: keeping %d messages", keep)
        await self._compress(force_keep=keep)

    def get_context(self) -> list[dict]:
        """Return ``[summary_system_msg] + raw_buffer`` for prompt injection.

        If a summary exists it is prepended as a system message so the LLM
        has a condensed view of the older conversation.
        """
        # M-02 fix: exclude self-thoughts from user-facing conversation context
        filtered_buffer = [m for m in self._raw_buffer if not m.get("is_self_thought")]
        messages: list[dict] = []
        if self._summary:
            messages.append({
                "role": "system",
                "content": f"[对话历史摘要]\n{self._summary}",
            })
        messages.extend(filtered_buffer)
        return messages

    def set_save_path(self, path: Path | str) -> None:
        """Set the file path for persisting the summary."""
        self._save_path = Path(path)

    def restore_from_file(self) -> None:
        """Load summary and recent buffer from disk on startup."""
        if not self._save_path or not self._save_path.exists():
            return
        try:
            data = json.loads(self._save_path.read_text(encoding="utf-8"))
            self._summary = data.get("summary", "")
            raw = data.get("raw_buffer", [])
            self._raw_buffer = raw[-self._keep_recent:]
            log.info(
                "Loaded conversation summary from %s: %d chars, %d msgs",
                self._save_path, len(self._summary), len(self._raw_buffer),
            )
        except Exception as e:
            log.warning("Failed to load summary from %s: %s", self._save_path, e)

    def restore_from_db(self, recent_memories: list[dict]) -> None:
        """Restore buffer from SQLite chat memories on startup.

        Each entry is expected to have at least ``role`` and ``content`` keys.
        """
        self._raw_buffer = []
        for mem in recent_memories:
            self._raw_buffer.append({
                "role": mem.get("role", "assistant"),
                "content": mem.get("content", ""),
                "is_self_thought": mem.get("is_self_thought", False),
            })
        # Keep only the most recent messages to avoid an oversized buffer.
        if len(self._raw_buffer) > self._keep_recent:
            self._raw_buffer = self._raw_buffer[-self._keep_recent:]
        log.info(
            "Restored %d messages from DB into conversation buffer",
            len(self._raw_buffer),
        )

    def restore_from_checkpoint(self, checkpoint: dict) -> None:
        """Restore from an evolution checkpoint dict."""
        self._summary = checkpoint.get("conversation_summary", "")
        raw = checkpoint.get("conversation", [])
        self._raw_buffer = raw[-self._keep_recent:] if raw else []
        log.info(
            "Restored from checkpoint: summary=%d chars, buffer=%d msgs",
            len(self._summary), len(self._raw_buffer),
        )

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _save_to_file(self) -> None:
        """Persist current summary and buffer to disk."""
        if not self._save_path:
            return
        try:
            self._save_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"summary": self._summary, "raw_buffer": self._raw_buffer}
            self._save_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log.debug("Summary saved to %s", self._save_path)
        except Exception as e:
            log.warning("Failed to save summary to %s: %s", self._save_path, e)

    async def _compress(self, force_keep: int | None = None) -> None:
        """Core compression logic.

        1. Split buffer into *to_summarize* + *keep_recent*.
        2. Check LLM budget.
        3. If budget OK: LLM summarise (tier 2) with a Chinese prompt.
        4. If budget exhausted: rule-based truncation (first 80 chars per
           message, last 20 messages).
        5. Update ``self._summary`` (recursive: ``new = LLM(old + new)``).
        """
        keep = force_keep if force_keep is not None else self._keep_recent

        if len(self._raw_buffer) <= keep:
            log.debug(
                "Buffer (%d) <= keep (%d), skipping compression",
                len(self._raw_buffer), keep,
            )
            return

        to_summarize = self._raw_buffer[:-keep]
        kept = self._raw_buffer[-keep:]

        new_summary = await self._llm_summarize(to_summarize)
        if new_summary is not None:
            self._summary = new_summary
        else:
            # Degrade: rule-based truncation
            self._summary = self._rule_truncation(to_summarize)

        self._raw_buffer = kept
        log.info(
            "Compression done: summary=%d chars, buffer=%d msgs",
            len(self._summary), len(self._raw_buffer),
        )
        self._save_to_file()

    async def _llm_summarize(self, messages: list[dict]) -> str | None:
        """Use Tier 2 LLM to produce a recursive summary.

        Returns ``None`` if budget is exhausted or the LLM call fails.
        """
        if not self._llm.check_budget():
            log.warning("LLM budget exhausted, falling back to rule truncation")
            return None

        formatted = self._format_messages_for_prompt(messages)
        prompt_content = (
            f"当前对话摘要:\n{self._summary or '(无)'}\n\n"
            f"新的对话内容:\n{formatted}\n\n"
            "请更新摘要。保留：用户指令、重要决策、关键事实、情感变化。"
            "去除：重复系统检查、闲聊细节、工具调用细节。300字以内。"
        )
        prompt_messages = [{"role": "user", "content": prompt_content}]

        try:
            result = await self._llm.call(prompt_messages, tier=2)
            if result:
                log.debug("LLM summary produced: %d chars", len(result))
                return result
            log.warning("LLM returned empty summary")
            return None
        except Exception:
            log.exception("LLM summarize call failed")
            return None

    def _rule_truncation(self, messages: list[dict]) -> str:
        """Fallback: build a summary from truncated messages.

        Keeps at most 20 messages, each truncated to 80 characters.
        Does NOT include the old summary to avoid unbounded growth.
        """
        parts: list[str] = []
        tail = messages[-20:]
        for msg in tail:
            role = msg.get("role", "?")
            content = msg.get("content", "")[:80]
            if msg.get("is_self_thought"):
                parts.append(f"[{role}/思考] {content}")
            else:
                parts.append(f"[{role}] {content}")
        return "\n".join(parts)

    def _estimate_buffer_tokens(self) -> int:
        """Estimate total tokens in summary + buffer.

        Uses ``len(text) // 3`` — a fast heuristic for Chinese-heavy text.
        """
        total_chars = len(self._summary)
        for msg in self._raw_buffer:
            total_chars += len(msg.get("content", ""))
        return total_chars // 3

    @staticmethod
    def _format_messages_for_prompt(messages: list[dict]) -> str:
        """Format a list of messages into readable text for the LLM prompt."""
        lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if msg.get("is_self_thought"):
                lines.append(f"[{role}/思考]: {content}")
            else:
                lines.append(f"[{role}]: {content}")
        return "\n".join(lines)
