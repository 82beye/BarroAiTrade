---
tags: [plan, feature/bar-43, status/in_progress, phase/0, area/repo]
template: plan
version: 1.0
---

# BAR-43 표준 로깅·메트릭 통일 Plan

> **Project**: BarroAiTrade
> **Feature**: BAR-43
> **Phase**: 0 — 네 번째 티켓
> **Master Plan**: [[../MASTER-EXECUTION-PLAN-v1#Phase 0 — 기반 정비 (Week 1–2, 5 티켓: BAR-40~44)]]
> **Author**: beye (CTO-lead)
> **Date**: 2026-05-06
> **Status**: In Progress
> **Gate**: BAR-44 (Phase 0 종료 게이트) 의 마지막 선결

---

## 1. Overview

### 1.1 Purpose

`backend/legacy_scalping/` 의 로깅이 main repo `core/monitoring/logger` 의 **JSON 구조화 + 파일 로테이션 인프라**를 통과하도록 *비침투적* 통합한다. 동시에 **Prometheus 메트릭 인프라**(`backend/core/monitoring/metrics.py` 신규 + `/metrics` 엔드포인트) 를 시동해 후속 BAR 가 `legacy_*` counter 를 즉시 발행할 수 있는 토대를 마련한다.

### 1.2 Background

- **현재 상태**: `core/monitoring/logger.py` 의 `setup_logging()` 이 JSON 포매터 + 파일 로테이션 제공. legacy 의 `logging.getLogger('scan')` 등 호출은 *Python 표준* — `setup_logging()` 만 호출하면 자동 통합 가능 (zero-modification 만족).
- **메트릭 부재**: `prometheus_client` 미사용. Grafana 대시보드는 docker-compose 에 정의되어 있으나 `/metrics` 엔드포인트 부재.
- **마스터 플랜 BAR-43 명세**: legacy 에 `core/monitoring/logger` import + `legacy_*` Prometheus counter, Grafana 가시화.
- **옵션 D 채택** (zero-modification 유지): 본 BAR-43 은 *infrastructure 시동만* — 메트릭 정의·등록·`/metrics` 엔드포인트. 실제 `legacy_*.inc()` 호출은 *후속 BAR-50 ScalpingConsensusStrategy* 또는 *BAR-66 RiskEngine 정책* 시점에 비침투 wrapper 로 추가.

### 1.3 Related Documents

- 마스터 플랜: [[../MASTER-EXECUTION-PLAN-v1]]
- BAR-40 / BAR-41 / BAR-42: 모두 ✅
- 기존 logger: `backend/core/monitoring/logger.py`
- 기존 사용처: `backend/legacy_scalping/main.py` (`getLogger('scan'/'account'/'analysis'/...)`)
- 후속 BAR-44 베이스라인: 본 BAR-43 의 메트릭 인프라 활용

---

## 2. Scope

### 2.1 In Scope

- [ ] `backend/core/monitoring/metrics.py` 신규 — Prometheus Counter/Gauge/Histogram 정의 (`legacy_signal_count`, `legacy_order_count`, `legacy_error_count`, `legacy_dry_run_count`, `core_signal_count`, `core_order_count` 등 8~12 메트릭)
- [ ] `prometheus_client>=0.20` 의존성 추가 (`backend/requirements.txt`)
- [ ] FastAPI 라우트 `/metrics` (Prometheus exposition format) — `backend/api/routes/metrics.py` 신규
- [ ] `setup_logging()` 호출 시점 명시 — `backend/main.py` 의 startup 또는 `backend/legacy_scalping/main.py` 진입점
- [ ] legacy_scalping 의 표준 `logging.getLogger()` 호출이 *자동* 통합되는지 검증 (zero-modification 만족)
- [ ] `tests/monitoring/test_metrics.py` 신규 — 메트릭 정의·등록·노출 단위 테스트 (5+ 케이스)
- [ ] `Makefile` 에 `test-monitoring` 또는 `test` 통합
- [ ] BAR-40/41/42 회귀 무영향 (V1~V3)

### 2.2 Out of Scope

- ❌ legacy 코드 *내부* 에 `Counter.inc()` 추가 — 후속 BAR-50/66 비침투 wrapper 로
- ❌ Grafana 대시보드 JSON 갱신 (별도 ops 작업, BAR-78 회귀 자동화 시점 통합)
- ❌ Distributed tracing (OpenTelemetry — Phase 6 BAR-73)
- ❌ Alertmanager rules (Phase 6 BAR-73 의 `monitoring/alerts.yaml`)

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | `metrics.py` 에 Prometheus 메트릭 객체 정의 (Counter/Gauge/Histogram, 8~12 메트릭) | High |
| FR-02 | `/metrics` 엔드포인트 — Prometheus exposition format text | High |
| FR-03 | 표준 `logging.getLogger()` 호출이 `setup_logging()` 후 자동 통합 | High |
| FR-04 | `prometheus_client` 의존성 미설치 시 `metrics.py` 가 *fallback no-op* 으로 동작 (FastAPI 부팅 비차단) | Medium |
| FR-05 | 메트릭 명명 컨벤션: `<scope>_<metric_name>_<unit_suffix>` (예: `legacy_signal_total`, `legacy_request_duration_seconds`) | High |
| FR-06 | docstring + 메트릭 레이블 (`module`, `signal_type` 등) 정의 | Medium |

### 3.2 Non-Functional Requirements

| Category | 기준 |
|---|---|
| 성능 | `/metrics` 응답 ≤ 100ms |
| 호환성 | BAR-40/41/42 회귀 무영향 (V1~V3) |
| 안전성 | metrics.py import 시 외부 호출 0건 (V4 동등) |
| 커버리지 | `metrics.py`, `routes/metrics.py` ≥ 80% |

---

## 4. Success Criteria

### 4.1 DoD

- [ ] `metrics.py` + `/metrics` 라우트 동작
- [ ] 5+ 테스트 통과
- [ ] `make legacy-scalping`, `make test-legacy`, `make test-config` 회귀 무영향
- [ ] 라인 커버리지 ≥ 80%
- [ ] `prometheus_client` 미설치 환경에서 `Settings()` + FastAPI 부팅 무에러 (fallback)
- [ ] PR 셀프 리뷰 + 머지

### 4.2 5+ 테스트 케이스

| # | 케이스 |
|---|--------|
| C1 | `metrics.py` import 무에러 |
| C2 | 메트릭 객체 (Counter `legacy_signal_total`) 존재 + `.inc()` 호출 가능 |
| C3 | `/metrics` GET 응답 200 + `text/plain; version=0.0.4` Content-Type |
| C4 | `/metrics` 응답에 `legacy_signal_total` 메트릭명 포함 |
| C5 | `prometheus_client` 미설치 환경 시뮬 → fallback no-op (Counter 호출 무에러) |
| C6 | (보강) `setup_logging()` 호출 후 legacy logger (`getLogger('scan')`) 가 JSON 포매터 사용 |

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| legacy 코드에 직접 increment 추가 유혹 | High (zero-modification 위반) | Medium | 본 티켓 §2.2 명시. 후속 BAR-50/66 비침투 wrapper |
| `prometheus_client` 미설치 시 부팅 실패 | High | Low | FR-04 fallback no-op |
| Grafana 가시화 — dashboard JSON 갱신 누락 | Medium | High | §2.2 out of scope. BAR-78 회귀 자동화 시 통합 |
| metrics 레지스트리 중복 등록 (테스트 격리 실패) | Medium | High | `CollectorRegistry()` 인스턴스를 fixture 로 분리 |
| `/metrics` 보안 (인증 없음) | Medium | Medium | Phase 5 BAR-69 RLS·인증 도입 후 `/metrics` 도 admin-only — 본 티켓에서는 placeholder TODO 주석 |

---

## 6. Architecture Considerations

### 6.1 Project Level — Enterprise

### 6.2 메트릭 카탈로그 (8~12)

```
# === Legacy (BAR-43 시동, 후속 BAR-50/66 increment) ===
legacy_signal_total              Counter   labels=(strategy_id, signal_type)
legacy_order_total               Counter   labels=(side, status)
legacy_error_total               Counter   labels=(module, error_type)
legacy_dry_run_total             Counter   labels=(reason)

# === Core (BAR-43 시동) ===
core_signal_total                Counter   labels=(strategy_id, signal_type)
core_order_total                 Counter   labels=(side, status)
core_request_duration_seconds    Histogram labels=(route, method)
core_active_positions            Gauge

# === System (BAR-43 시동) ===
system_kill_switch_active        Gauge     labels=(reason)        # BAR-64 활용
system_market_session            Gauge     labels=(session)       # BAR-52 활용
```

### 6.3 fallback no-op 패턴

```python
# backend/core/monitoring/metrics.py
try:
    from prometheus_client import Counter, Gauge, Histogram, REGISTRY
    _PROMETHEUS = True
except ImportError:
    _PROMETHEUS = False

    class _NoOpMetric:
        """fallback 더미 — prometheus_client 미설치 시 사용"""
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

    Counter = Gauge = Histogram = _NoOpMetric  # type: ignore
    REGISTRY = None
```

### 6.4 `/metrics` 엔드포인트

```python
# backend/api/routes/metrics.py
from fastapi import APIRouter, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

router = APIRouter()

@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus exposition format. TODO(BAR-69): admin-only."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

---

## 7. Convention Prerequisites

- ✅ `core/monitoring/logger.py` 존재 — 재사용
- ✅ pytest 인프라 (BAR-41 시동) — `tests/monitoring/` 추가
- ❌ `prometheus_client` 미설치 → 본 티켓에서 추가
- ❌ `tests/monitoring/` 디렉터리 부재 → 본 티켓에서 시동

---

## 8. Implementation Outline

> Design 에서 상세화.

1. **D1 사전 점검**: `setup_logging()` 호출 시점 + legacy logger 동작 확인
2. **D2 `metrics.py` 신규** — fallback no-op + 메트릭 카탈로그
3. **D3 `routes/metrics.py` 신규** — `/metrics` 엔드포인트
4. **D4 `backend/main.py` 또는 동등** 에서 `setup_logging()` 호출 및 `/metrics` 라우트 등록
5. **D5 `prometheus_client>=0.20` requirements 추가**
6. **D6 `tests/monitoring/__init__.py`, `test_metrics.py` 5+ 케이스**
7. **D7 Makefile `test-monitoring` 또는 `test` 통합**
8. **D8 V1~V6 검증**
9. **D9 PR 생성** (라벨: `area:repo` `phase:0` `priority:p0`)

---

## 9. Next Steps

1. [ ] Design 문서
2. [ ] Do 구현
3. [ ] Analyze (gap-detector or 직접)
4. [ ] Report
5. [ ] BAR-44 진입 — Phase 0 종료 게이트

---

## 10. 비고

- legacy 코드 zero-modification 유지. 직접 `Counter.inc()` 호출 추가 금지.
- `/metrics` 보안은 Phase 5 BAR-69 위임 — 본 티켓 TODO 주석.
- Grafana dashboard JSON 갱신은 BAR-78 회귀 자동화 시 통합.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-06 | 초기 plan — Phase 0 네 번째 티켓, 옵션 D (infrastructure 시동), 메트릭 카탈로그 8+ | beye (CTO-lead) |
