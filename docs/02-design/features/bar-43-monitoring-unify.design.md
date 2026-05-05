---
tags: [design, feature/bar-43, status/in_progress, phase/0, area/repo]
template: design
version: 1.0
---

# BAR-43 표준 로깅·메트릭 통일 Design Document

> **관련 문서**: [[../../01-plan/features/bar-43-monitoring-unify.plan|Plan]] | [[../../01-plan/MASTER-EXECUTION-PLAN-v1|Master Plan v1]]

> **Summary**: Prometheus 메트릭 인프라 + `/metrics` 엔드포인트 신설. 옵션 D (zero-modification + fallback no-op) 적용. 6+ 테스트 + V1~V6 + D1~D9
>
> **Project**: BarroAiTrade
> **Feature**: BAR-43
> **Phase**: 0
> **Author**: beye (CTO-lead)
> **Date**: 2026-05-06
> **Status**: Draft
> **Planning Doc**: [bar-43-monitoring-unify.plan.md](../../01-plan/features/bar-43-monitoring-unify.plan.md)

---

## 1. Overview

### 1.1 Design Goals

- `backend/core/monitoring/metrics.py` 신규 — Prometheus Counter/Gauge/Histogram 정의 (10 메트릭)
- `prometheus_client` 미설치 시 fallback no-op (FastAPI 부팅 비차단)
- `/metrics` 엔드포인트 — Prometheus exposition format
- legacy 의 표준 `logging.getLogger()` 호출 자동 통합 (zero-modification)
- BAR-40/41/42 회귀 무영향

### 1.2 Design Principles

- **Infrastructure Only**: 본 티켓은 *발행 인프라* 만. 실제 increment 는 후속 BAR-50/66
- **Fallback no-op**: `prometheus_client` 미설치 환경(테스트, 가벼운 deploy)에서도 부팅 가능
- **Zero-modification 유지**: legacy 코드 직접 수정 없음. 표준 `logging.getLogger()` 만 활용
- **Defensive Imports**: `prometheus_client` 의 import 실패가 모든 호출 경로에서 안전하게 처리

---

## 2. Architecture

### 2.1 Module Layout

```
backend/core/monitoring/
├── logger.py                ← 기존 (변경 없음, setup_logging 재사용)
├── metrics.py               ← 🆕 신규 (~120 LOC)
└── ...

backend/api/routes/
├── metrics.py               ← 🆕 신규 (/metrics 엔드포인트)
└── ...

backend/main.py              ← startup 에 setup_logging() + metrics 라우트 등록

backend/tests/monitoring/
├── __init__.py              ← 🆕
└── test_metrics.py          ← 🆕 (6+ 케이스)

backend/requirements.txt     ← prometheus_client>=0.20 추가
Makefile                     ← test-monitoring 타겟 + test 통합
```

### 2.2 메트릭 카탈로그 (10)

| 메트릭 | 타입 | 레이블 | 활용 BAR |
|---|---|---|---|
| `legacy_signal_total` | Counter | `strategy_id`, `signal_type` | BAR-50 |
| `legacy_order_total` | Counter | `side`, `status` | BAR-50/66 |
| `legacy_error_total` | Counter | `module`, `error_type` | BAR-43 (즉시) |
| `legacy_dry_run_total` | Counter | `reason` | BAR-43 (즉시) |
| `core_signal_total` | Counter | `strategy_id`, `signal_type` | BAR-45 |
| `core_order_total` | Counter | `side`, `status` | BAR-55/63 |
| `core_request_duration_seconds` | Histogram | `route`, `method` | BAR-43 (즉시) |
| `core_active_positions` | Gauge | — | BAR-66 |
| `system_kill_switch_active` | Gauge | `reason` | BAR-64 |
| `system_market_session` | Gauge | `session` | BAR-52 |

### 2.3 Fallback no-op 구조

```python
# backend/core/monitoring/metrics.py
try:
    from prometheus_client import (
        Counter as _Counter,
        Gauge as _Gauge,
        Histogram as _Histogram,
        REGISTRY,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
    _PROMETHEUS = True
except ImportError:  # pragma: no cover
    _PROMETHEUS = False
    REGISTRY = None
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    class _NoOpMetric:
        def __init__(self, *args, **kwargs):
            pass
        def labels(self, *args, **kwargs):
            return self
        def inc(self, *args, **kwargs):
            pass
        def set(self, *args, **kwargs):
            pass
        def observe(self, *args, **kwargs):
            pass

    _Counter = _Gauge = _Histogram = _NoOpMetric  # type: ignore

    def generate_latest(*args, **kwargs):
        return b"# prometheus_client not installed\n"
```

### 2.4 메트릭 인스턴스 정의

```python
# 메트릭 객체는 모듈 import 시 1회만 등록 — 중복 등록 방지
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

# ... 8 더
```

### 2.5 `/metrics` 라우트

```python
# backend/api/routes/metrics.py
from fastapi import APIRouter, Response

from backend.core.monitoring.metrics import (
    CONTENT_TYPE_LATEST,
    generate_latest,
)

router = APIRouter(tags=["monitoring"])


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus exposition format.

    TODO(BAR-69): admin-only 인증 추가 (Phase 5 보안 강화).
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

### 2.6 main.py 통합 지점

`backend/main.py` 의 FastAPI 앱 부팅 시점:

```python
from backend.api.routes import metrics as metrics_routes
from backend.core.monitoring.logger import setup_logging
from backend.config.settings import settings

@app.on_event("startup")
async def _startup():
    setup_logging(json_format=settings.log_json)

app.include_router(metrics_routes.router)
```

---

## 3. Implementation Spec

### 3.1 prometheus_client 의존성

`backend/requirements.txt`:
```
prometheus_client>=0.20
```

`backend/.venv/bin/pip install prometheus_client` 으로 로컬 설치 후 실행 검증.

### 3.2 metrics.py 의 export 정책

```python
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
```

### 3.3 테스트 격리 — registry 충돌 회피

```python
# backend/tests/monitoring/test_metrics.py
import importlib

import pytest


@pytest.fixture
def fresh_metrics_module():
    """각 테스트마다 metrics 모듈 재로드 — REGISTRY 중복 등록 방지."""
    import backend.core.monitoring.metrics as m

    importlib.reload(m)
    return m
```

### 3.4 Makefile 갱신

```makefile
test-monitoring: ## BAR-43 모니터링 인프라 단위 테스트
	@echo "[BAR-43] Running pytest backend/tests/monitoring/..."
	@$(PYTHON) -m pytest backend/tests/monitoring/ -v \
		--cov=backend.core.monitoring.metrics \
		--cov=backend.api.routes.metrics \
		--cov-report=term-missing
	@echo "[BAR-43] tests OK"

# test 타겟은 이미 BAR-42 에서 신설 — backend/tests/ 통합
```

---

## 4. 6+ Test Cases

```python
# backend/tests/monitoring/test_metrics.py
from fastapi.testclient import TestClient

import pytest


class TestMetricsModule:
    """C1~C2"""

    def test_c1_metrics_module_imports(self, fresh_metrics_module):
        """C1: metrics.py import 무에러."""
        assert fresh_metrics_module.legacy_signal_total is not None

    def test_c2_counter_inc_callable(self, fresh_metrics_module):
        """C2: Counter `legacy_signal_total` 존재 + .inc() 호출 가능."""
        m = fresh_metrics_module
        m.legacy_signal_total.labels(
            strategy_id="legacy_scalping_consensus", signal_type="f_zone"
        ).inc()


class TestMetricsRoute:
    """C3~C4"""

    @pytest.fixture
    def client(self):
        from fastapi import FastAPI

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

        resp = client.get("/metrics")
        assert "legacy_signal_total" in resp.text


class TestFallbackNoOp:
    """C5"""

    def test_c5_no_op_when_unavailable(self):
        """C5: prometheus_client 미설치 시뮬 → fallback no-op."""
        # _NoOpMetric 의 .labels().inc() 호출 무에러
        from backend.core.monitoring.metrics import _PROMETHEUS

        if _PROMETHEUS:
            pytest.skip("prometheus_client installed — fallback 검증 skip")

        from backend.core.monitoring.metrics import legacy_signal_total

        # no-op
        legacy_signal_total.labels(strategy_id="x", signal_type="y").inc()


class TestLoggerIntegration:
    """C6 (보강)"""

    def test_c6_setup_logging_propagates_to_legacy_loggers(self, capsys):
        """C6: setup_logging() 후 legacy logger 가 통합된 핸들러 사용."""
        import logging

        from backend.core.monitoring.logger import setup_logging

        setup_logging(json_format=True)

        legacy_logger = logging.getLogger("scan")
        legacy_logger.info("BAR-43 test message")
        # capsys 또는 propagate 확인 (구체 검증은 setup_logging 의 핸들러 정책에 따라)
        assert legacy_logger.handlers or logging.getLogger().handlers
```

---

## 5. Verification Scenarios (V1~V6)

| # | 시나리오 | 명령 | 기대 |
|---|---|---|---|
| V1 | `make test-monitoring` 통과 | `make test-monitoring` | exit 0, 6+ passed |
| V2 | 라인 커버리지 ≥ 80% | `pytest --cov` | ≥ 80% |
| V3 | BAR-40 dry-run 회귀 | `make legacy-scalping` | exit 0 |
| V4 | BAR-41/42 pytest 회귀 | `make test-legacy && make test-config` | 19 + 9 passed |
| V5 | `prometheus_client` 미설치 환경 | (시뮬) `import` 후 `_PROMETHEUS == False` 분기 | C5 통과 |
| V6 | `/metrics` 엔드포인트 응답 | TestClient GET `/metrics` | 200 + `text/plain` |

---

## 6. Risk Mitigation Detail

| Risk (Plan §5) | Detection | Action |
|---|---|---|
| legacy 코드 직접 increment 추가 유혹 | 코드 리뷰 시 `legacy_*.inc()` 가 `backend/legacy_scalping/` 안에서 발견 | PR description 에 zero-modification 재명시. 발견 시 wrapper 패턴 권장 |
| `prometheus_client` 미설치 부팅 실패 | C5/V5 실패 | `_NoOpMetric` fallback (§2.3) |
| metric registry 중복 등록 | C1 실패 | `fresh_metrics_module` fixture (§3.3) |
| `/metrics` 보안 부재 | Phase 5 BAR-69 도입 시점까지 | TODO 주석 + Phase 5 게이트 |
| Grafana dashboard JSON 갱신 누락 | (out of scope) | BAR-78 회귀 자동화 시 통합 (별도 PR) |

---

## 7. Out-of-Scope (재확인)

- ❌ legacy 내부 `Counter.inc()` (BAR-50/66)
- ❌ Grafana dashboard JSON 갱신 (BAR-78)
- ❌ OpenTelemetry (BAR-73)
- ❌ Alertmanager rules (BAR-73)
- ❌ `/metrics` admin-only (BAR-69)

---

## 8. Implementation Checklist (D1~D9)

- [ ] D1 — `backend/main.py` 의 startup 흐름 + 기존 `core/monitoring/logger.py` API 재확인
- [ ] D2 — `backend/core/monitoring/metrics.py` 신규 (§2.2~§2.4, fallback no-op)
- [ ] D3 — `backend/api/routes/metrics.py` 신규 (§2.5)
- [ ] D4 — `backend/main.py` startup 통합 (§2.6)
- [ ] D5 — `backend/requirements.txt` 갱신 (`prometheus_client>=0.20`)
- [ ] D6 — `backend/tests/monitoring/__init__.py`, `test_metrics.py` 6+ 케이스
- [ ] D7 — `Makefile` `test-monitoring` 타겟
- [ ] D8 — V1~V6 검증 + BAR-40/41/42 회귀 확인
- [ ] D9 — PR (라벨: `area:repo` `phase:0` `priority:p0` `ai-generated`)

---

## 9. Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-06 | 초기 design — 10 메트릭 카탈로그, fallback no-op 구조, 6+ 테스트, V1~V6, D1~D9 | beye (CTO-lead) |
