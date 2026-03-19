"""Rule engine — independent of LLM, produces same Decision format."""

from __future__ import annotations

from anima.config import agent_name
from anima.models.decision import Decision, ActionType
from anima.models.event import EventType
from anima.utils.logging import get_logger

log = get_logger("rule_engine")


class RuleEngine:
    """Evaluates context with deterministic rules.

    Returns Decision in the same format as LLM, so Act stage doesn't care
    about the source.
    """

    CPU_ALERT_THRESHOLD = 90
    DISK_ALERT_THRESHOLD = 95

    def evaluate(self, context: dict) -> Decision:
        """Evaluate context and return a Decision.

        Built-in rules:
        - File change → save_note (record change)
        - CPU > CPU_ALERT_THRESHOLD for sustained period → respond (notify user)
        - Disk > DISK_ALERT_THRESHOLD → respond (warning)
        - User greeting → respond (direct reply)
        """
        event_type = context.get("event_type", "")
        payload = context.get("event_payload", {})
        system_state = context.get("system_state", {})

        # Rule: file change → NOOP (changes are already logged; save_note created too much noise)
        if event_type == EventType.FILE_CHANGE.name or event_type == "FILE_CHANGE":
            return Decision(
                action=ActionType.NOOP,
                reasoning="File change noted (no save_note needed, already in logs)",
                source="rule_engine",
            )

        # Rule: system alert — high CPU
        if event_type == EventType.SYSTEM_ALERT.name or event_type == "SYSTEM_ALERT":
            cpu = system_state.get("cpu_percent", 0)
            disk = system_state.get("disk_percent", 0)

            if disk >= self.DISK_ALERT_THRESHOLD:
                return Decision(
                    action=ActionType.RESPOND,
                    reasoning="Disk usage critically high",
                    content=f"⚠ 磁盘使用率达到 {disk:.1f}%，请注意清理空间。",
                    source="rule_engine",
                )

            if cpu >= self.CPU_ALERT_THRESHOLD:
                return Decision(
                    action=ActionType.RESPOND,
                    reasoning="CPU usage critically high",
                    content=f"⚠ CPU 使用率达到 {cpu:.1f}%，系统可能较慢。",
                    source="rule_engine",
                )

        # Rule: user message — simple greeting detection
        if event_type == EventType.USER_MESSAGE.name or event_type == "USER_MESSAGE":
            text = payload.get("text", "").strip().lower()
            greetings = {"hi", "hello", "hey", "你好", "嗨", "早", "晚上好", "早上好"}
            if text.strip() in greetings:
                name = agent_name()
                return Decision(
                    action=ActionType.RESPOND,
                    reasoning="User greeting detected",
                    content=f"啾啾主人～ 我是 {name.upper()}，正在持续运行中呢！有什么我能帮你的吗？",
                    source="rule_engine",
                )

        # Default: no action needed
        return Decision(
            action=ActionType.NOOP,
            reasoning="No rule matched, no action needed",
            source="rule_engine",
        )
