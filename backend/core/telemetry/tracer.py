"""BAR-73 — Tracer (OpenTelemetry-호환 단순 SDK).

분산 trace_id 전파 + span 생성. 운영 시 OpenTelemetry SDK 로 교체 (BAR-73b).
"""
from __future__ import annotations

import contextvars
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class TraceContext(BaseModel):
    """trace 메타 — frozen."""

    model_config = ConfigDict(frozen=True)

    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    operation: str
    started_at: datetime
    duration_ms: float = 0.0
    attributes: dict[str, Any] = Field(default_factory=dict)


_CURRENT_TRACE: contextvars.ContextVar[Optional[TraceContext]] = contextvars.ContextVar(
    "trace", default=None
)


class Tracer:
    """단순 in-memory tracer. spans 누적."""

    def __init__(self) -> None:
        self._spans: list[TraceContext] = []

    def start_span(
        self, operation: str, attributes: Optional[dict] = None
    ) -> "_SpanContext":
        return _SpanContext(self, operation, attributes or {})

    @property
    def spans(self) -> list[TraceContext]:
        return list(self._spans)

    def reset(self) -> None:
        self._spans.clear()

    def _record(self, ctx: TraceContext) -> None:
        self._spans.append(ctx)


class _SpanContext:
    def __init__(
        self, tracer: Tracer, operation: str, attributes: dict
    ) -> None:
        self._tracer = tracer
        self._operation = operation
        self._attributes = attributes
        self._start: Optional[float] = None
        self._token: Optional[contextvars.Token] = None
        self._parent: Optional[TraceContext] = None
        self._ctx: Optional[TraceContext] = None

    def __enter__(self) -> TraceContext:
        self._parent = _CURRENT_TRACE.get()
        trace_id = self._parent.trace_id if self._parent else uuid.uuid4().hex
        span_id = uuid.uuid4().hex[:16]
        parent_span = self._parent.span_id if self._parent else None
        self._ctx = TraceContext(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span,
            operation=self._operation,
            started_at=datetime.now(timezone.utc),
            attributes=dict(self._attributes),
        )
        self._token = _CURRENT_TRACE.set(self._ctx)
        self._start = time.perf_counter()
        return self._ctx

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._start is not None and self._ctx is not None:
            duration_ms = (time.perf_counter() - self._start) * 1000
            final = self._ctx.model_copy(update={"duration_ms": duration_ms})
            self._tracer._record(final)
        if self._token is not None:
            _CURRENT_TRACE.reset(self._token)


__all__ = ["Tracer", "TraceContext"]
