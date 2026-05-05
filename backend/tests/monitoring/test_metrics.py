"""
BAR-43 메트릭 인프라 단위 테스트 (Plan §4.2 / Design §4).

C1~C6 핵심 + TestSetupLogging 보강.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def fresh_metrics_module():
    """metrics 모듈 import — 모듈 import 시 1회만 등록 (Singleton 패턴).

    REGISTRY 중복 등록을 회피하기 위해 reload 하지 않는다. 메트릭 객체는
    프로세스 수명 동안 동일 인스턴스를 공유한다.
    """
    import backend.core.monitoring.metrics as m

    return m


class TestMetricsModule:
    """C1~C2 — 메트릭 모듈 import 및 호출 가능성."""

    def test_c1_metrics_module_imports(self, fresh_metrics_module):
        """C1: metrics.py import 무에러 + 10 메트릭 모두 노출."""
        m = fresh_metrics_module
        assert m.legacy_signal_total is not None
        assert m.legacy_order_total is not None
        assert m.legacy_error_total is not None
        assert m.legacy_dry_run_total is not None
        assert m.core_signal_total is not None
        assert m.core_order_total is not None
        assert m.core_request_duration_seconds is not None
        assert m.core_active_positions is not None
        assert m.system_kill_switch_active is not None
        assert m.system_market_session is not None

    def test_c2_counter_inc_callable(self, fresh_metrics_module):
        """C2: legacy_signal_total Counter `.labels().inc()` 호출 가능."""
        m = fresh_metrics_module
        # raise 없이 호출되면 통과
        m.legacy_signal_total.labels(
            strategy_id="legacy_scalping_consensus", signal_type="f_zone"
        ).inc()

    def test_gauge_set_callable(self, fresh_metrics_module):
        """Gauge .set() 호출 가능 (보강)."""
        m = fresh_metrics_module
        m.core_active_positions.set(3)

    def test_histogram_observe_callable(self, fresh_metrics_module):
        """Histogram .observe() 호출 가능 (보강)."""
        m = fresh_metrics_module
        m.core_request_duration_seconds.labels(route="/api/signals", method="GET").observe(0.05)


class TestMetricsRoute:
    """C3~C4 — /metrics 엔드포인트."""

    @pytest.fixture
    def client(self):
        from backend.api.routes import metrics as metrics_routes

        app = FastAPI()
        app.include_router(metrics_routes.router)
        return TestClient(app)

    def test_c3_metrics_endpoint_200(self, client):
        """C3: /metrics GET 200 + Prometheus Content-Type."""
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]

    def test_c4_metrics_response_contains_legacy_signal_total(self, client):
        """C4: /metrics 응답에 메트릭명 포함 (prometheus_client 설치 시)."""
        from backend.core.monitoring.metrics import _PROMETHEUS

        if not _PROMETHEUS:
            pytest.skip("prometheus_client not installed — fallback returns stub")

        # 메트릭이 노출되려면 한 번 이상 라벨링되어야 함 (prometheus 스펙)
        from backend.core.monitoring.metrics import legacy_signal_total

        legacy_signal_total.labels(strategy_id="x", signal_type="y").inc()

        resp = client.get("/metrics")
        assert "legacy_signal_total" in resp.text


class TestFallbackNoOp:
    """C5 — prometheus_client 미설치 fallback 검증."""

    def test_c5_no_op_when_unavailable(self, fresh_metrics_module):
        """C5: prometheus_client 가 없을 때 _NoOpMetric 으로 fallback.

        본 환경에서는 prometheus_client 가 설치되어 있을 수 있으나, fallback
        타입의 호출 안전성은 _NoOpMetric 정의 자체로 보장된다 (인터페이스 호환).
        """
        m = fresh_metrics_module
        if m._PROMETHEUS:
            pytest.skip("prometheus_client installed — fallback 검증 skip")

        # _NoOpMetric 의 .labels().inc() 호출 무에러
        m.legacy_signal_total.labels(strategy_id="x", signal_type="y").inc()


class TestSetupLogging:
    """C6 (보강) — setup_logging 통합."""

    def test_c6_setup_logging_legacy_logger_propagation(self):
        """C6: setup_logging() 후 legacy logger 가 root handler 통해 출력 가능."""
        from backend.core.monitoring.logger import setup_logging

        setup_logging(json_format=True)

        legacy_logger = logging.getLogger("scan")
        # raise 없이 동작하면 통과 (구체 핸들러 검증은 setup_logging 정책 변동 가능)
        legacy_logger.info("BAR-43 propagation test")
        # root logger 에 핸들러가 등록되어 있어야 함
        assert len(logging.getLogger().handlers) >= 1
