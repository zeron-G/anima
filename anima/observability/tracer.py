"""Lightweight execution tracer for the ANIMA cognitive pipeline.

Records spans for each stage of event processing: memory retrieval,
prompt compilation, LLM calls, tool execution, and response handling.
Each event creates a trace (root span) with child spans for each stage.

Traces are stored in memory (last 200) and exposed via the dashboard
for debugging and performance analysis.

Usage:
    tracer = Tracer()

    with tracer.trace("event:USER_MESSAGE") as trace:
        with trace.span("memory_retrieval") as s:
            s.set("episodic_count", 5)
            s.set("tokens", 1200)
            # ... do work ...

        with trace.span("llm_call") as s:
            s.set("model", "claude-opus-4-6")
            s.set("prompt_tokens", 3000)
            # ... do work ...

    # trace automatically records duration and status
    recent = tracer.get_recent(20)
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator
from collections import deque

from anima.utils.logging import get_logger

log = get_logger("tracer")


@dataclass
class Span:
    """A single timed operation within a trace."""

    name: str
    trace_id: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    parent_id: str | None = None
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"  # ok, error
    error_message: str = ""
    children: list[Span] = field(default_factory=list)

    def set(self, key: str, value: Any) -> None:
        """Set an attribute on this span."""
        self.attributes[key] = value

    def end(self, status: str = "ok", error: str = "") -> None:
        """Mark this span as complete."""
        self.end_time = time.time()
        self.status = status
        if error:
            self.error_message = error

    @property
    def duration_ms(self) -> float:
        """Duration in milliseconds."""
        if self.end_time == 0:
            return (time.time() - self.start_time) * 1000
        return (self.end_time - self.start_time) * 1000

    def to_dict(self) -> dict[str, Any]:
        """Serialize for dashboard display."""
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "duration_ms": round(self.duration_ms, 1),
            "status": self.status,
            "error": self.error_message,
            "attributes": self.attributes,
            "children": [c.to_dict() for c in self.children],
        }


class TraceContext:
    """Active trace — context manager that creates child spans."""

    def __init__(self, root: Span) -> None:
        self._root = root
        self._current_span: Span | None = None

    @contextmanager
    def span(self, name: str) -> Generator[Span, None, None]:
        """Create a child span within this trace."""
        child = Span(
            name=name,
            trace_id=self._root.trace_id,
            parent_id=self._root.span_id,
        )
        self._root.children.append(child)
        self._current_span = child
        try:
            yield child
        except Exception as e:
            child.end(status="error", error=str(e)[:200])
            raise
        finally:
            if child.end_time == 0:
                child.end()
            self._current_span = None

    @property
    def root(self) -> Span:
        return self._root


class Tracer:
    """Traces cognitive pipeline execution.

    Thread-safe via deque (atomic append/popleft).
    """

    def __init__(self, max_traces: int = 200) -> None:
        self._traces: deque[Span] = deque(maxlen=max_traces)

    @contextmanager
    def trace(self, name: str) -> Generator[TraceContext, None, None]:
        """Start a new trace (root span).

        Usage:
            with tracer.trace("event:USER_MESSAGE") as t:
                with t.span("memory_retrieval") as s:
                    s.set("count", 5)
        """
        root = Span(name=name, trace_id=uuid.uuid4().hex[:16])
        ctx = TraceContext(root)
        try:
            yield ctx
        except Exception as e:
            root.end(status="error", error=str(e)[:200])
            raise
        finally:
            if root.end_time == 0:
                root.end()
            self._traces.append(root)

    def get_recent(self, n: int = 20) -> list[dict[str, Any]]:
        """Get the N most recent traces as serialized dicts."""
        traces = list(self._traces)
        recent = traces[-n:] if len(traces) > n else traces
        return [t.to_dict() for t in recent]

    def get_stats(self) -> dict[str, Any]:
        """Get aggregate statistics across recent traces."""
        traces = list(self._traces)
        if not traces:
            return {"total_traces": 0}

        durations = [t.duration_ms for t in traces]
        error_count = sum(1 for t in traces if t.status == "error")

        # Per-span-type averages
        span_durations: dict[str, list[float]] = {}
        for trace in traces:
            for child in trace.children:
                span_durations.setdefault(child.name, []).append(child.duration_ms)

        span_averages = {
            name: round(sum(ds) / len(ds), 1)
            for name, ds in span_durations.items()
        }

        return {
            "total_traces": len(traces),
            "error_rate": round(error_count / len(traces), 3),
            "avg_duration_ms": round(sum(durations) / len(durations), 1),
            "p95_duration_ms": round(sorted(durations)[int(len(durations) * 0.95)], 1) if len(durations) >= 20 else None,
            "span_avg_ms": span_averages,
        }

    def clear(self) -> None:
        """Clear all stored traces."""
        self._traces.clear()


# Global tracer instance — imported by cognitive loop and dashboard
_global_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    """Get or create the global tracer instance."""
    global _global_tracer
    if _global_tracer is None:
        _global_tracer = Tracer()
    return _global_tracer
