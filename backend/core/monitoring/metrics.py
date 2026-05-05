"""
BAR-43: Prometheus 메트릭 인프라

10 메트릭 (legacy_* 4 + core_* 4 + system_* 2) 발행 객체를 정의한다.
실제 increment 호출은 후속 BAR (BAR-50/55/63/64/66) 의 책임 — 본 모듈은
*infrastructure 시동* 만 담당한다 (zero-modification 유지).

prometheus_client 미설치 환경에서는 _NoOpMetric 으로 fallback 하여
FastAPI 부팅을 비차단한다.

References:
- Plan: docs/01-plan/features/bar-43-monitoring-unify.plan.md
- Design: docs/02-design/features/bar-43-monitoring-unify.design.md §2.2
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        REGISTRY,
        Counter as _Counter,
        Gauge as _Gauge,
        Histogram as _Histogram,
        generate_latest as _generate_latest,
    )

    _PROMETHEUS = True
except ImportError:  # pragma: no cover — fallback when prometheus_client 미설치
    _PROMETHEUS = False
    REGISTRY = None  # type: ignore
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    class _NoOpMetric:
        """fallback 더미 — prometheus_client 미설치 시 모든 호출 silent no-op."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def labels(self, *args: Any, **kwargs: Any) -> "_NoOpMetric":
            return self

        def inc(self, *args: Any, **kwargs: Any) -> None:
            pass

        def set(self, *args: Any, **kwargs: Any) -> None:
            pass

        def observe(self, *args: Any, **kwargs: Any) -> None:
            pass

    _Counter = _Gauge = _Histogram = _NoOpMetric  # type: ignore

    def _generate_latest(*args: Any, **kwargs: Any) -> bytes:
        return b"# prometheus_client not installed\n"


def generate_latest(*args: Any, **kwargs: Any) -> bytes:
    """Prometheus exposition format 생성 (또는 fallback stub)."""
    return _generate_latest(*args, **kwargs)


# === Legacy 메트릭 (BAR-43 시동, BAR-50/66 increment) ===
legacy_signal_total = _Counter(
    "legacy_signal_total",
    "ai-trade legacy 시그널 발행 카운트",
    ["strategy_id", "signal_type"],
)

legacy_order_total = _Counter(
    "legacy_order_total",
    "ai-trade legacy 주문 발행 카운트",
    ["side", "status"],
)

legacy_error_total = _Counter(
    "legacy_error_total",
    "ai-trade legacy 모듈 에러 카운트",
    ["module", "error_type"],
)

legacy_dry_run_total = _Counter(
    "legacy_dry_run_total",
    "BAR-40 dry-run 진입 카운트 (DRY_RUN=1 시 sys.exit)",
    ["reason"],
)

# === Core 메트릭 (BAR-45/55/63 increment 예정) ===
core_signal_total = _Counter(
    "core_signal_total",
    "BarroAiTrade core 전략 시그널 발행 카운트",
    ["strategy_id", "signal_type"],
)

core_order_total = _Counter(
    "core_order_total",
    "core 주문 발행 카운트",
    ["side", "status"],
)

core_request_duration_seconds = _Histogram(
    "core_request_duration_seconds",
    "FastAPI 라우트 요청 처리 시간 (초)",
    ["route", "method"],
)

core_active_positions = _Gauge(
    "core_active_positions",
    "현재 보유 포지션 수 (BAR-66 RiskEngine 비중 관리에서 갱신)",
)

# === System 메트릭 (BAR-52/64 increment 예정) ===
system_kill_switch_active = _Gauge(
    "system_kill_switch_active",
    "Kill Switch 활성 여부 (1=활성, 0=비활성)",
    ["reason"],
)

system_market_session = _Gauge(
    "system_market_session",
    "현재 거래 세션 (1=활성). NXT_PRE/REGULAR/KRX_AFTER/NXT_AFTER 등",
    ["session"],
)


__all__ = [
    # 메트릭 인스턴스
    "legacy_signal_total",
    "legacy_order_total",
    "legacy_error_total",
    "legacy_dry_run_total",
    "core_signal_total",
    "core_order_total",
    "core_request_duration_seconds",
    "core_active_positions",
    "system_kill_switch_active",
    "system_market_session",
    # 도구
    "REGISTRY",
    "generate_latest",
    "CONTENT_TYPE_LATEST",
    "_PROMETHEUS",
]
