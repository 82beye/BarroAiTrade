"""BAR-71 — 멀티 사용자 격리 + 사용량 메트릭.

TenantContext: contextvar 기반 user_id 전파.
UsageMetricsRecorder: 사용자별 호출 횟수 / 응답 시간 누적.
"""
from __future__ import annotations

import contextvars
import time
from collections import defaultdict
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


_USER_ID_CTX: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "user_id", default=None
)


class TenantContext:
    """contextvar 기반 user_id 전파 — RLS app.user_id 와 연동."""

    @staticmethod
    def set_user(user_id: str) -> contextvars.Token:
        return _USER_ID_CTX.set(user_id)

    @staticmethod
    def reset(token: contextvars.Token) -> None:
        _USER_ID_CTX.reset(token)

    @staticmethod
    def current_user() -> Optional[str]:
        return _USER_ID_CTX.get()

    @staticmethod
    def require_user() -> str:
        user = _USER_ID_CTX.get()
        if user is None:
            raise PermissionError("no tenant context — user_id required")
        return user


class UsageMetrics(BaseModel):
    """사용자별 누적 메트릭 (frozen 스냅샷)."""

    model_config = ConfigDict(frozen=True)

    user_id: str = Field(min_length=1)
    api_calls: int = 0
    total_latency_ms: float = 0.0
    last_seen: Optional[datetime] = None


class UsageMetricsRecorder:
    """in-memory 누적기 — 운영 진입 시 Postgres usage_metrics 테이블로 이관."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = defaultdict(int)
        self._latency: dict[str, float] = defaultdict(float)
        self._last_seen: dict[str, datetime] = {}
        self._lock = Lock()

    def record(self, user_id: str, latency_ms: float) -> None:
        if not user_id:
            raise ValueError("user_id required")
        if latency_ms < 0:
            raise ValueError("latency_ms must be ≥ 0")
        with self._lock:
            self._counts[user_id] += 1
            self._latency[user_id] += latency_ms
            self._last_seen[user_id] = datetime.now(timezone.utc)

    def get(self, user_id: str) -> UsageMetrics:
        with self._lock:
            return UsageMetrics(
                user_id=user_id,
                api_calls=self._counts.get(user_id, 0),
                total_latency_ms=self._latency.get(user_id, 0.0),
                last_seen=self._last_seen.get(user_id),
            )

    def reset(self, user_id: Optional[str] = None) -> None:
        with self._lock:
            if user_id is None:
                self._counts.clear()
                self._latency.clear()
                self._last_seen.clear()
                return
            self._counts.pop(user_id, None)
            self._latency.pop(user_id, None)
            self._last_seen.pop(user_id, None)

    def time_call(self, user_id: str):
        """context manager — `with recorder.time_call(uid):` 로 자동 누적."""
        return _TimedCall(self, user_id)


class _TimedCall:
    def __init__(self, recorder: UsageMetricsRecorder, user_id: str) -> None:
        self._rec = recorder
        self._uid = user_id
        self._start: Optional[float] = None

    def __enter__(self) -> "_TimedCall":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args) -> None:
        if self._start is not None:
            elapsed_ms = (time.perf_counter() - self._start) * 1000
            self._rec.record(self._uid, elapsed_ms)


__all__ = [
    "TenantContext",
    "UsageMetrics",
    "UsageMetricsRecorder",
]
