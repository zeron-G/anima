"""Supervisor-side NLP fallback for robot-dog commands."""

from __future__ import annotations

import json
import re
from typing import Any

from anima.llm.providers.router import completion
from anima.robotics.models import PIDOG_COMMANDS
from anima.utils.logging import get_logger

log = get_logger("robotics.nlp_supervisor")

_DIRECT_RULES: list[tuple[str, str]] = [
    ("emergency stop", "emergency_stop"),
    ("hard stop", "emergency_stop"),
    ("stand up", "stand"),
    ("get up", "stand"),
    ("rise up", "stand"),
    ("sit down", "sit"),
    ("take a seat", "sit"),
    ("lie down", "lie"),
    ("lay down", "lie"),
    ("walk forward", "walk_forward"),
    ("move forward", "walk_forward"),
    ("go forward", "walk_forward"),
    ("walk backward", "walk_backward"),
    ("move backward", "walk_backward"),
    ("go backward", "walk_backward"),
    ("back up", "walk_backward"),
    ("turn left", "turn_left"),
    ("turn right", "turn_right"),
    ("look left", "look_left"),
    ("look right", "look_right"),
    ("look forward", "look_forward"),
    ("look up", "look_up"),
    ("look down", "look_down"),
    ("center head", "center_head"),
    ("center your head", "center_head"),
    ("wag tail", "wag_tail"),
    ("wag your tail", "wag_tail"),
    ("shake head", "shake_head"),
    ("stretch", "stretch"),
    ("do push ups", "push_up"),
    ("do pushups", "push_up"),
    ("push up", "push_up"),
    ("bark", "bark"),
    ("howl", "howl"),
    ("be happy", "be_happy"),
    ("act happy", "be_happy"),
    ("be curious", "be_curious"),
    ("be alert", "be_alert"),
    ("be tired", "be_tired"),
    ("sleep mode", "sleep_mode"),
    ("go to sleep", "sleep_mode"),
    ("wake up", "wake_mode"),
    ("resume", "resume"),
    ("stop", "stop"),
    ("halt", "stop"),
    ("freeze", "stop"),
    ("status", "status"),
    ("report status", "status"),
]

_CODEX_MODEL_ALIASES = {
    "5.3codex": "codex/gpt-5.3-codex",
    "5.3-codex": "codex/gpt-5.3-codex",
    "gpt-5.3-codex": "codex/gpt-5.3-codex",
}


def match_pidog_command_text(text: str) -> dict[str, Any] | None:
    """Cheap deterministic parse for common bilingual robot commands."""
    normalized = _normalize_text(text)
    if not normalized:
        return None

    for phrase, command in _DIRECT_RULES:
        if _matches_phrase(normalized, phrase):
            return {
                "command": command,
                "params": {},
                "confidence": 1.0,
                "reason": f"Matched deterministic phrase '{phrase}'",
            }
    return None


class RobotNlpSupervisor:
    """Resolve free-form robot instructions through a higher-level LLM."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = dict(config or {})
        self.enabled = bool(cfg.get("enabled", False))
        self.model = _normalize_model_name(str(cfg.get("model", "")).strip())
        self.max_tokens = int(cfg.get("max_tokens", 400))
        self.min_confidence = float(cfg.get("min_confidence", 0.55))

    async def plan(
        self,
        text: str,
        *,
        node_id: str = "",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any] | None:
        if not self.enabled or not self.model:
            return None

        user_text = str(text or "").strip()
        if not user_text:
            return None

        allowed = [cmd for cmd in (capabilities or PIDOG_COMMANDS) if cmd in PIDOG_COMMANDS]
        if not allowed:
            allowed = list(PIDOG_COMMANDS)

        messages = [
            {
                "role": "system",
                "content": (
                    "You map robot-dog natural-language instructions to exactly one allowed command.\n"
                    "Return JSON only with keys: command, params, confidence, reason.\n"
                    "Rules:\n"
                    "- command must be one of the allowed commands.\n"
                    "- params must be an object.\n"
                    "- confidence must be between 0 and 1.\n"
                    "- If the request is ambiguous, unsafe, or unsupported, set command to an empty string.\n"
                    "- Prefer the safest single command that moves the robot toward the user's intent."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "node_id": node_id,
                        "instruction": user_text,
                        "allowed_commands": allowed,
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        try:
            response = await completion(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=0.1,
            )
        except Exception as exc:
            log.warning("Supervisor LLM planning failed for %s: %s", node_id or "robot", exc)
            return None

        payload = _extract_json_object(str(response.get("content", "")))
        if not isinstance(payload, dict):
            return None

        command = str(payload.get("command") or "").strip()
        if command not in allowed:
            return None

        params = payload.get("params")
        if not isinstance(params, dict):
            params = {}

        confidence = _clamp_confidence(payload.get("confidence", 0.0))
        reason = str(payload.get("reason") or "").strip()
        if not command:
            return None

        return {
            "command": command,
            "params": params,
            "confidence": confidence,
            "reason": reason,
            "model": str(response.get("model", self.model)),
        }


def _normalize_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
    normalized = re.sub(r"[_/\-]+", " ", normalized)
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff\s]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _matches_phrase(text: str, phrase: str) -> bool:
    pattern = r"\b" + r"\s+".join(re.escape(part) for part in phrase.split()) + r"\b"
    return re.search(pattern, text) is not None


def _normalize_model_name(raw: str) -> str:
    model = raw.strip()
    if not model:
        return ""
    alias_key = model.lower().replace(" ", "")
    if alias_key in _CODEX_MODEL_ALIASES:
        return _CODEX_MODEL_ALIASES[alias_key]
    if model.startswith("gpt-") and "codex" in model and not model.startswith("codex/"):
        return f"codex/{model}"
    return model


def _extract_json_object(text: str) -> dict[str, Any] | None:
    body = str(text or "").strip()
    if not body:
        return None
    try:
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    start = body.find("{")
    end = body.rfind("}")
    if start == -1 or end <= start:
        return None

    try:
        parsed = json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        return 0.0
    return max(0.0, min(1.0, number))
