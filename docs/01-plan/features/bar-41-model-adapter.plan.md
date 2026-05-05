---
tags: [plan, feature/bar-41, status/in_progress, phase/0, area/repo]
template: plan
version: 1.0
---

# BAR-41 모델 호환 어댑터 Plan

> **Project**: BarroAiTrade
> **Feature**: BAR-41
> **Phase**: 0 (기반 정비) — 두 번째 티켓 (BAR-40 흡수 직후)
> **Master Plan**: [[../MASTER-EXECUTION-PLAN-v1#Phase 0 — 기반 정비 (Week 1–2, 5 티켓: BAR-40~44)]]
> **Author**: beye (CTO-lead)
> **Date**: 2026-05-06
> **Status**: In Progress
> **Gate**: BAR-43 (Logger·Prometheus 통일) 의 선결, BAR-44 (베이스라인) 까지의 의존 체인

---

## 1. Overview

### 1.1 Purpose

BAR-40 으로 흡수된 `backend/legacy_scalping/` 의 시그널 산출물(주로 `ScalpingAnalysis` dataclass + dict) 을 BarroAiTrade 표준 `models/signal.py:EntrySignal` 로 변환하는 **양방향 어댑터** 를 작성한다. 어댑터는 *legacy 코드를 수정하지 않고* (BAR-40 의 zero-modification 원칙 유지) 두 시스템의 시그널 흐름을 통합 가능하게 만든다.

### 1.2 Background

- BAR-40 흡수 직후 `backend/legacy_scalping/` 의 모듈은 import 만 가능. main repo 의 표준 도구 (RiskEngine, OrderExecutor, audit_repo) 와 *시그널 타입이 호환되지 않음*.
- 마스터 플랜 v1 의 Phase 0 두 번째 티켓. DoD: `tests/legacy_scalping/test_adapter.py` 8 케이스 통과.
- BAR-40 분석 §M2 의 약속: `backend/tests/` 부재 해소 — 본 티켓에서 `tests/legacy_scalping/` 디렉터리 시동.

### 1.3 Related Documents

- 마스터 플랜: [[../MASTER-EXECUTION-PLAN-v1]]
- BAR-40 (선결, 완료): [[bar-40-monorepo-absorption.plan|BAR-40 plan]] / [[../../04-report/bar-40-monorepo-absorption.report|BAR-40 report]]
- 표준 모델: `backend/models/signal.py` 의 `EntrySignal` / `ExitSignal`
- legacy 산출물 출처: `backend/legacy_scalping/strategy/scalping_team/coordinator.py` (956 LOC, `ScalpingAnalysis` 결과)

---

## 2. Scope

### 2.1 In Scope

- [ ] `backend/legacy_scalping/_adapter.py` 신규 — 양방향 변환 함수 + Pydantic v2 스키마
- [ ] **dict / dataclass → EntrySignal**: legacy `ScalpingAnalysis` 와 dict 형태의 signal 을 표준 `EntrySignal` 로 변환
- [ ] **EntrySignal → dict**: 역방향 (legacy 모니터링·dashboard 호환용 — 선택)
- [ ] `tests/legacy_scalping/__init__.py` + `tests/legacy_scalping/test_adapter.py` 신규 — **8 케이스**
- [ ] `tests/__init__.py` (root tests 패키지)
- [ ] `tests/conftest.py` (pytest fixture, sample legacy signal/analysis 제공)
- [ ] `backend/requirements.txt` 에 `pytest>=8.0`, `pytest-cov>=5.0` 추가
- [ ] `Makefile` 에 `test-legacy` 타겟 추가 (`pytest backend/tests/legacy_scalping/ -v --cov`)
- [ ] PR description 에 V1~Vn 검증 결과 첨부

### 2.2 Out of Scope

- ❌ legacy 시그널 산출 로직 수정 (zero-modification 유지)
- ❌ Strategy v2 인터페이스 도입 (BAR-45 의 책임)
- ❌ ScalpingConsensusStrategy 메타전략 통합 (BAR-50 의 책임)
- ❌ 어댑터를 통해 실제 OrderExecutor 호출 (BAR-43 Logger·메트릭 통일 후)
- ❌ ai-trade 의 모니터링 dict (Telegram 메시지 등) 호환 — 별도 후속

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | `to_entry_signal(legacy_data: dict \| ScalpingAnalysis) -> EntrySignal` 변환 함수 | High | Pending |
| FR-02 | `to_legacy_dict(signal: EntrySignal) -> dict` 역방향 함수 (선택, monitoring 호환) | Medium | Pending |
| FR-03 | 누락 필드(name·price 등) 시 fallback 정책 명시 (예외 발생 vs default 값) | High | Pending |
| FR-04 | legacy total_score (0~100) → `EntrySignal.score` (0~1) 정규화 | High | Pending |
| FR-05 | legacy timing/zone → `signal_type` 매핑 (5 enum: blue_line/watermelon/crypto_breakout/f_zone/sf_zone) | High | Pending |
| FR-06 | `metadata` 에 원본 ScalpingAnalysis 보존 (역추적 가능) | Medium | Pending |
| FR-07 | Pydantic v2 `model_validate` 로 어댑터 결과 검증 | High | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| 성능 | 단일 변환 ≤ 1ms | `pytest-benchmark` (선택) |
| 테스트 커버리지 | `_adapter.py` 라인 커버리지 ≥ 80% | `pytest --cov=backend.legacy_scalping._adapter` |
| 호환성 | Pydantic v2 (`model_validate`, `model_config`) 사용 | static type check |
| 안전성 | float 사용 금지, 가격은 `Decimal` 권장 (단 `EntrySignal.price: float` 라 단계적 — BAR-45 에서 강화) | grep + 코드 리뷰 |

> **주의**: `EntrySignal.price` 는 현재 `float` 타입이지만, 자금흐름 코드는 *Decimal 의무* (마스터 플랜 §0). 본 티켓 단계에서는 EntrySignal 의 타입을 *변경하지 않음* (BAR-45 Strategy v2 책임). 어댑터 내부 계산만 Decimal 로 처리 후 마지막에 float 캐스팅.

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] `backend/legacy_scalping/_adapter.py` 작성 완료
- [ ] `tests/legacy_scalping/test_adapter.py` **8 케이스** pytest green
- [ ] 라인 커버리지 ≥ 80% (`pytest --cov`)
- [ ] BAR-40 의 dry-run 회귀 무영향 — `make legacy-scalping` 여전히 통과
- [ ] `make test-legacy` 타겟 동작
- [ ] PR 셀프 리뷰 + 머지

### 4.2 8 테스트 케이스 시나리오

| # | 카테고리 | 케이스 |
|---|----------|--------|
| T1 | 정상 변환 | dict 형태 legacy signal (모든 필드 있음) → EntrySignal |
| T2 | 정상 변환 | `ScalpingAnalysis` dataclass → EntrySignal (signal_type 매핑) |
| T3 | 정상 변환 | total_score=85 → score=0.85 정규화 |
| T4 | Fallback | name 누락 → symbol 을 name 으로 사용 |
| T5 | Fallback | price 누락 → ValueError 발생 (price 는 필수) |
| T6 | 거부 | legacy_data=None → TypeError |
| T7 | 거부 | total_score=120 (범위 초과) → ValueError |
| T8 | 경계 | total_score=0 → score=0.0 (정상 변환) |

### 4.3 Quality Criteria

- [ ] `_adapter.py` ≤ 200 LOC
- [ ] 모든 함수에 type hint 와 docstring
- [ ] Pydantic v2 model_config 사용 (`extra="forbid"` 권장)
- [ ] Zero lint errors

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| legacy `ScalpingAnalysis` 스키마 추적 누락 | Medium | Medium | design 단계에서 `coordinator.py` 의 dataclass 정의 그대로 인용. 변경 시 어댑터 회귀 |
| signal_type 매핑 부적절 | High (전략 ID 오인식) | Medium | scalping_team 결과는 5 enum 어디에도 정확히 안 맞을 수 있음 → 임시 `"scalping_consensus"` enum 추가 또는 `metadata.legacy_timing` 보존 |
| `EntrySignal.price: float` 와 Decimal 의무 충돌 | Medium | High | 본 티켓 단계에서 모델 변경 금지. 어댑터 내부만 Decimal 처리 후 마지막 float 캐스팅. BAR-45 에서 모델 자체를 Decimal 로 강화 |
| 회귀 — BAR-40 의 V1 `make legacy-scalping` 깨짐 | High | Low | 어댑터는 import-only 시 동작 안 함. dry-run 가드(DRY_RUN=1) 영향 없음 |
| pytest 미설치 (system python3) | Medium | Medium | `backend/requirements.txt` 추가 + Dockerfile.backend 재빌드 또는 `pip install -e .[dev]` 시점 명시 |
| 8 케이스 외 edge case 누락 (예: 음수 score) | Low | Medium | analyze 단계에서 gap-detector 가 미커버 case 발견 시 iterate 또는 후속 티켓 |

---

## 6. Architecture Considerations

### 6.1 Project Level
- **Enterprise**

### 6.2 어댑터 위치 결정

| 옵션 | 장점 | 단점 | 채택 |
|---|---|---|:---:|
| A. `backend/legacy_scalping/_adapter.py` | legacy 와 가까이 보관, namespace 일치 | main 시스템에서 import 시 legacy 의존 발생 | ⭐ |
| B. `backend/core/adapters/legacy_scalping.py` | core 에 격리 | 이름 어색, 두 디렉터리에 걸친 의존 | — |

→ **A 채택**. 단 `backend.legacy_scalping._adapter` 의 *underscore prefix* 로 *팀 내부용 * 표시. 외부 import 는 `backend.legacy_scalping import to_entry_signal` 으로 노출 (legacy_scalping/__init__.py 에 명시 re-export).

### 6.3 Pydantic v2 사용 결정

```python
# backend/legacy_scalping/_adapter.py
from pydantic import BaseModel, ConfigDict, ValidationError

class LegacySignalSchema(BaseModel):
    """ai-trade 시그널의 정규화 스키마"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str  # symbol
    name: str | None = None
    price: float  # 단계적 — 향후 Decimal
    total_score: float  # 0~100
    timing: str | None = None
    zone: str | None = None
    market_type: Literal["stock", "crypto"] = "stock"
    strategy_id: str = "legacy_scalping_consensus"
    timestamp: datetime | None = None
    raw: dict | None = None  # 원본 보존
```

### 6.4 signal_type 매핑 정책

| legacy timing/zone 패턴 | EntrySignal.signal_type |
|---|---|
| timing="즉시" + zone 무관 | `"f_zone"` (1차 매핑 — F존이 가장 즉시성 강함) |
| timing="대기" / "눌림목대기" | `"sf_zone"` (대기·되돌림 패턴) |
| zone="watermelon" 또는 watermelon agent score > threshold | `"watermelon"` |
| MarketType="crypto" | `"crypto_breakout"` |
| 그 외 모호 | `"blue_line"` (기본값) + `metadata.legacy_timing` 보존 |

**결정 보류**: 매핑이 부정확할 가능성 (위험 매트릭스의 두 번째 항목). design 단계에서 *legacy 코드의 실제 timing/zone 출현 빈도* 를 grep 으로 측정 후 가중치 조정. 첫 do 에서는 위 매핑으로 시작, gap-detector 결과 < 90% 시 iterate.

---

## 7. Convention Prerequisites

### 7.1 기존 컨벤션

- ✅ `docs/01-plan/features/{bar-XX}-{slug}.plan.md` (BAR-17/23/28/29/40 선례)
- ✅ Pydantic v2, asyncio, type hint 의무
- ✅ 한국어 주석/docstring
- ❌ `tests/` 디렉터리 *부재* — **본 티켓에서 시동**
- ❌ `pytest.ini` / `pyproject.toml` 의 `[tool.pytest.ini_options]` *부재*

### 7.2 본 티켓에서 정의할 컨벤션

| 항목 | 결정 |
|---|---|
| pytest 디렉터리 위치 | `backend/tests/` (main repo backend 안에 격리). worktree 루트의 `tests/` 가 아님 |
| pytest 실행 명령 | `pytest backend/tests/ -v --cov=backend.legacy_scalping` 또는 `make test-legacy` |
| 테스트 파일 명명 | `test_<module>.py` |
| Fixture 위치 | `backend/tests/conftest.py` (root) + `backend/tests/legacy_scalping/conftest.py` (선택) |
| pytest.ini | `pyproject.toml` 의 `[tool.pytest.ini_options]` (별도 파일 X) — 본 티켓에서 추가 |

---

## 8. 작업 단계 (Implementation Outline)

> 본 plan 승인 후 design 문서에서 상세화. 여기는 개략적 단계.

1. **D1 사전 점검**: `backend/legacy_scalping/strategy/scalping_team/coordinator.py` 에서 `ScalpingAnalysis` dataclass 정의 위치 + 필드 목록 grep. dict 형태 signal 출현 위치도 색출.
2. **D2 `LegacySignalSchema` Pydantic 모델 작성**: `backend/legacy_scalping/_adapter.py` 신규.
3. **D3 `to_entry_signal()` 변환 함수**: 매핑 정책 §6.4 적용. 실패 시 `ValidationError` / `TypeError` / `ValueError` 명시 raise.
4. **D4 `to_legacy_dict()` 역변환** (선택, FR-02): MVP 는 패스 가능, design 에서 우선순위 결정.
5. **D5 `tests/__init__.py`, `tests/conftest.py`, `tests/legacy_scalping/__init__.py` 신규**.
6. **D6 `tests/legacy_scalping/test_adapter.py` 8 케이스 작성** (T1~T8).
7. **D7 `backend/requirements.txt` + `pyproject.toml` 갱신** (pytest, pytest-cov 추가).
8. **D8 `Makefile` `test-legacy` 타겟**.
9. **D9 V1~V5 검증 시나리오 실행** (design §5 에서 정의 예정).
10. **D10 PR 생성**.

---

## 9. Next Steps

1. [ ] Design 문서 작성 (`/pdca design BAR-41`) — `docs/02-design/features/bar-41-model-adapter.design.md`
2. [ ] 본인 리뷰 + 승인
3. [ ] Do 단계 진입 (`/pdca do BAR-41`)
4. [ ] Analyze (gap-detector + pytest)
5. [ ] Report (≥ 90% 도달 시)

---

## 10. 비고

- **자금흐름 영역**: 본 티켓은 시그널 변환만 다루지만, 후속 OrderExecutor 통합 시 자동 *`area:money` 라벨* 부착 의무. 본 티켓 자체는 `area:repo` + `phase:0` + `priority:p0`.
- **AI 생성 코드**: 어댑터 코드 일부가 AI 생성일 수 있으므로 PR 라벨 `ai-generated` 부착 권장 (BAR-70 본 게이트는 미도입이지만 사전 표식).
- **BAR-51 번호 충돌**: 마스터 플랜 v2 발행 전이므로 본 plan 에서는 BAR-51 언급 회피.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-06 | 초기 plan — Phase 0 두 번째 티켓, 8 테스트 케이스 정의 | beye (CTO-lead) |
