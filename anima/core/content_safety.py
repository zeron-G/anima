"""Content safety filtering stage — keyword + optional API moderation.

Inserted between EmotionPerception and MemoryRetrieval in the pipeline.
Checks user messages against blocked/warning keyword patterns.
Optionally calls external moderation API.
"""

from __future__ import annotations

import re
from typing import Any

from anima.config import get
from anima.core.pipeline import PipelineContext, PipelineStage
from anima.models.event import EventType
from anima.utils.logging import get_logger

log = get_logger("content_safety")


class ContentSafetyStage(PipelineStage):
    """Content safety filtering — blocks harmful content before LLM processing."""
    name = "content_safety"

    def __init__(self) -> None:
        cfg = get("content_safety", {})
        self._enabled = cfg.get("enabled", True)
        self._blocked: list[re.Pattern] = []
        self._warning: list[re.Pattern] = []
        for pattern in cfg.get("blocked_keywords", []):
            try:
                self._blocked.append(re.compile(pattern, re.IGNORECASE))
            except re.error:
                log.warning("Invalid blocked pattern: %s", pattern)
        for pattern in cfg.get("warning_keywords", []):
            try:
                self._warning.append(re.compile(pattern, re.IGNORECASE))
            except re.error:
                pass
        self._filter_output = cfg.get("filter_output", True)

    async def process(self, pctx: PipelineContext) -> PipelineContext:
        if not self._enabled or not pctx.user_message:
            return pctx

        # Only filter user messages (not internal events)
        if pctx.event.type != EventType.USER_MESSAGE:
            return pctx

        text = pctx.user_message

        # Check blocked patterns
        for pattern in self._blocked:
            if pattern.search(text):
                log.warning("Content blocked: pattern=%s", pattern.pattern)
                pctx.content = "抱歉，这个请求我没法处理。"
                pctx.handled = True
                # Deliver refusal via output callback
                ctx = pctx.cognitive_ctx
                if ctx.output_callback:
                    try:
                        ctx.output_callback(pctx.content)
                    except Exception:
                        pass
                return pctx

        # Check warning patterns (log only, don't block)
        for pattern in self._warning:
            if pattern.search(text):
                log.info("Content warning: pattern=%s", pattern.pattern)

        return pctx
