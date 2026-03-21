"""Message processing pipeline — composable stages for the cognitive loop.

Each stage receives a PipelineContext and returns it (possibly modified).
Setting ctx.handled = True short-circuits the remaining stages.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from anima.utils.logging import get_logger

if TYPE_CHECKING:
    from anima.core.context import CognitiveContext
    from anima.models.event import Event

log = get_logger("pipeline")


@dataclass
class PipelineContext:
    """Mutable context passed through pipeline stages."""
    event: Event
    cognitive_ctx: CognitiveContext  # alias: ctx
    trace: Any = None               # TraceContext from the tracer

    # Populated by stages
    decision: Any = None           # RoutingDecision from EventRoutingStage
    user_message: str = ""         # Extracted message text
    memory_context: Any = None     # From MemoryRetrievalStage
    system_prompt: str = ""        # From PromptCompilationStage
    conv_messages: list = field(default_factory=list)
    messages: list = field(default_factory=list)  # Final LLM messages
    tools: list = field(default_factory=list)       # Tool schemas
    content: str = ""              # LLM response content
    tool_calls_made: int = 0
    loop_error: str | None = None

    handled: bool = False          # Set True to stop pipeline


class PipelineStage:
    """Base class for pipeline stages."""
    name: str = "unnamed"

    async def process(self, ctx: PipelineContext) -> PipelineContext:
        raise NotImplementedError


class Pipeline:
    """Ordered sequence of processing stages."""

    def __init__(self, stages: list[PipelineStage]) -> None:
        self._stages = stages

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        for stage in self._stages:
            try:
                ctx = await stage.process(ctx)
                if ctx.handled:
                    log.debug("Pipeline short-circuited at stage: %s", stage.name)
                    break
            except Exception as e:
                log.error("Pipeline stage %s failed: %s", stage.name, e, exc_info=True)
                ctx.loop_error = str(e)
                break
        return ctx
