---
tags: [design, feature/bar-41, status/in_progress, phase/0, area/repo]
template: design
version: 1.0
---

# BAR-41 모델 호환 어댑터 Design Document

> **관련 문서**: [[../../01-plan/features/bar-41-model-adapter.plan|Plan]] | [[../../01-plan/MASTER-EXECUTION-PLAN-v1|Master Plan v1]]
>
> **Summary**: ai-trade `ScalpingAnalysis` dataclass 와 dict 형태 시그널을 표준 `EntrySignal` 로 변환하는 양방향 어댑터의 상세 설계 — 매핑 표·정규화 정책·8 테스트 시나리오·검증 V1~V6
>
> **Project**: BarroAiTrade
> **Feature**: BAR-41
> **Phase**: 0 (기반 정비)
> **Author**: beye (CTO-lead)
> **Date**: 2026-05-06
> **Status**: Draft
> **Planning Doc**: [bar-41-model-adapter.plan.md](../../01-plan/features/bar-41-model-adapter.plan.md)

---

## 1. Overview

### 1.1 Design Goals

- legacy `ScalpingAnalysis` (16 필드) 와 main repo `EntrySignal` (11 필드) 간 *손실 최소* 변환
- 변환 실패 시 명확한 예외 (TypeError/ValueError/ValidationError) — silent default 회피
- 100% Pydantic v2 검증 — `model_validate` 통과 후에만 `EntrySignal` 반환
- legacy 코드 무수정 (BAR-40 zero-modification 원칙 유지)
- `tests/legacy_scalping/` 디렉터리 시동 — BAR-40 §M2 약속 이행

### 1.2 Design Principles

- **Single Conversion Direction Primary**: legacy → EntrySignal 이 본 티켓 핵심. 역방향(`to_legacy_dict`) 은 후순위.
- **Fail Fast**: 누락·범위 초과·타입 오류 발견 즉시 raise. RiskEngine 까지 잘못된 데이터 전파 차단.
- **Original Preservation**: `metadata` 에 원본 `ScalpingAnalysis` dataclass 직렬화 보존 (역추적·디버깅).
- **Decimal Awareness**: 어댑터 내부 산술은 `Decimal`. `EntrySignal.price: float` 호환성 유지를 위해 출력만 `float(quantize(...))` 캐스팅.

---

## 2. Architecture

### 2.1 흐름 다이어그램

```
[legacy_scalping coordinator]
   ↓ produces
   ScalpingAnalysis(code, name, total_score, timing, ...)  or  dict
                                  ↓
   ┌───────────────────────────────────────────────────────────┐
   │  backend.legacy_scalping._adapter                          │
   │                                                            │
   │  1) Normalize → LegacySignalSchema (Pydantic v2)           │
   │     - dataclass → asdict() → schema                        │
   │     - dict → schema (extra="forbid")                       │
   │                                                            │
   │  2) Map → EntrySignal fields                               │
   │     - code → symbol                                        │
   │     - name → name (fallback: symbol)                       │
   │     - price (snapshot or optimal_entry_price) → price      │
   │     - timing → signal_type (정책표 §3.3)                   │
   │     - total_score / 100 → score                            │
   │     - top_reasons.join → reason                            │
   │     - market_type=STOCK / strategy_id=...                  │
   │     - metadata = {legacy_timing, consensus_level,          │
   │                    tp_pct, sl_pct, hold_minutes, ...}      │
   │                                                            │
   │  3) Validate → EntrySignal.model_validate(...)             │
   └───────────────────────────────────────────────────────────┘
                                  ↓ returns
   EntrySignal(symbol, name, price, signal_type, score, ...)
                                  ↓ consumed by
   [main repo: RiskEngine, OrderExecutor, audit_repo, ...]
```

### 2.2 Module Layout

```
backend/legacy_scalping/
├── __init__.py              ← re-export: from ._adapter import to_entry_signal
├── _adapter.py              ← 신규 (≤ 200 LOC)
└── ... (기존 미러)

backend/tests/                ← 신규 디렉터리 (BAR-40 §M2 시동)
├── __init__.py              ← 신규
├── conftest.py              ← 신규 (sample fixture)
└── legacy_scalping/
    ├── __init__.py          ← 신규
    └── test_adapter.py      ← 신규 (8 케이스)

pyproject.toml               ← 신규 또는 갱신 ([tool.pytest.ini_options])
backend/requirements.txt     ← pytest>=8.0, pytest-cov>=5.0 추가
Makefile                     ← test-legacy 타겟 추가
```

### 2.3 Dependencies

| 도구 | 용도 |
|---|---|
| `pydantic>=2.7` (이미 있음) | LegacySignalSchema, EntrySignal 검증 |
| `pytest>=8.0` (신규) | 8 케이스 단위 테스트 |
| `pytest-cov>=5.0` (신규) | 커버리지 ≥ 80% 측정 |
| `decimal` (stdlib) | 가격·점수 정규화 |
| `dataclasses.asdict` (stdlib) | ScalpingAnalysis → dict 변환 |

---

## 3. Implementation Spec

### 3.1 LegacySignalSchema (정규화)

```python
# backend/legacy_scalping/_adapter.py
from datetime import datetime
from typing import Any, Literal
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class LegacySignalSchema(BaseModel):
    """ai-trade 시그널의 정규화 중간 표현"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(..., min_length=1)
    name: str | None = None
    price: float = Field(..., gt=0)
    total_score: float = Field(..., ge=0, le=100)
    timing: str = ""
    consensus_level: str = ""
    market_type: Literal["stock", "crypto"] = "stock"
    strategy_id: str = "legacy_scalping_consensus"
    timestamp: datetime | None = None

    # 옵션 필드 (metadata 보존용)
    confidence: float | None = None
    optimal_entry_price: float | None = None
    scalp_tp_pct: float | None = None
    scalp_sl_pct: float | None = None
    hold_minutes: int | None = None
    surge_type: str | None = None
    intraday_atr: float | None = None
    rank: int | None = None
    top_reasons: list[str] = Field(default_factory=list)
    raw: dict | None = None
```

### 3.2 ScalpingAnalysis 매핑 표

| ScalpingAnalysis 필드 | LegacySignalSchema | EntrySignal | 비고 |
|---|---|---|---|
| `code` | `code` | `symbol` | 필수 |
| `name` | `name` | `name` (fallback: symbol) | 누락 시 fallback |
| `snapshot.price` 또는 `optimal_entry_price` | `price` | `price` | snapshot 우선, 둘 다 부재 → ValueError |
| `total_score` | `total_score` (0~100) | `score` (`/ 100`, 0~1) | 정규화 |
| `timing` | `timing` | `signal_type` (정책표 §3.3 매핑) | 5 enum 매핑 |
| `consensus_level` | `consensus_level` | `metadata.consensus_level` | 보존 |
| `confidence` | `confidence` | `metadata.confidence` | 보존 |
| `optimal_entry_price` | `optimal_entry_price` | `metadata.optimal_entry_price` | 보존 |
| `scalp_tp_pct` | `scalp_tp_pct` | `metadata.tp_pct` | 보존 (BAR-63 ExitPlan 시 활용) |
| `scalp_sl_pct` | `scalp_sl_pct` | `metadata.sl_pct` | 보존 |
| `hold_minutes` | `hold_minutes` | `metadata.hold_minutes` | 보존 |
| `agent_signals` (dict) | `raw` | `metadata.agent_signals` | dict-cast 후 보존 |
| `top_reasons` (list) | `top_reasons` | `reason` (`"; ".join(top_reasons)` 또는 첫 3개) | 결합 |
| `surge_type` | `surge_type` | `metadata.surge_type` | 보존 |
| `intraday_atr` | `intraday_atr` | `metadata.intraday_atr` | 보존 |
| `rank` | `rank` | `metadata.rank` | 보존 |
| (정적) | `market_type="stock"` | `MarketType.STOCK` | 고정 (crypto 분기 후속 BAR) |
| (정적) | `strategy_id="legacy_scalping_consensus"` | `strategy_id` | 고정 |
| (정적) `datetime.now(UTC)` | `timestamp` | `timestamp` | 변환 시각 |
| (정적) | — | `risk_approved=False` | RiskEngine 미통과 |

### 3.3 timing → signal_type 매핑 정책

```python
TIMING_TO_SIGNAL_TYPE = {
    "즉시": "f_zone",          # 즉시 진입 가능 → F존
    "대기": "sf_zone",          # 대기/조정 → SF존
    "눌림목대기": "blue_line",  # 눌림목 회복 → 블루라인
    "관망": "blue_line",        # 관망 신호도 blue_line (보수적 기본값)
}

# market_type=crypto 시 우선 적용
def _resolve_signal_type(timing: str, market_type: str) -> str:
    if market_type == "crypto":
        return "crypto_breakout"
    return TIMING_TO_SIGNAL_TYPE.get(timing, "blue_line")
```

**미매칭 timing 처리**: 위 dict 에 없는 timing 은 `"blue_line"` 기본값. `metadata.legacy_timing` 에 원본 보존하여 후속 분석 가능.

### 3.4 핵심 함수 시그니처

```python
def to_entry_signal(
    legacy_data: "ScalpingAnalysis | dict[str, Any]",
    *,
    fallback_market_type: MarketType = MarketType.STOCK,
) -> EntrySignal:
    """legacy ScalpingAnalysis 또는 dict 시그널을 EntrySignal 로 변환.

    Raises:
        TypeError: legacy_data 가 None 또는 지원되지 않는 타입
        ValueError: price 누락 또는 total_score 범위 초과
        pydantic.ValidationError: schema 검증 실패
    """


def to_legacy_dict(signal: EntrySignal) -> dict[str, Any]:
    """EntrySignal 을 legacy 모니터링 호환 dict 로 역변환 (옵션, FR-02).

    BAR-41 do 단계에서 *최소 구현* (top-level keys만 미러). 정밀 호환은
    legacy 모니터링 통합 시점(후속 BAR)에서 강화.
    """
```

### 3.5 예외 처리 정책

| 케이스 | 예외 | 사유 |
|---|---|---|
| `legacy_data is None` | `TypeError("legacy_data must not be None")` | 호출자 버그 |
| `legacy_data` 가 dict/dataclass 가 아님 | `TypeError("unsupported legacy_data type: ...")` | 타입 미지원 |
| `code` 빈 문자열 | `ValidationError` (Pydantic min_length=1) | 필수 필드 |
| `price` 누락 (snapshot 없고 optimal_entry_price=0) | `ValueError("price not derivable from legacy_data")` | 자금흐름 안전 |
| `total_score < 0` 또는 `> 100` | `ValidationError` (Pydantic ge/le) | 정규화 안전 |
| `EntrySignal.model_validate` 실패 | `pydantic.ValidationError` 그대로 전파 | 표준 검증 |

### 3.6 Decimal 산술 정책

- `total_score / 100` 정규화 시 `Decimal(str(total_score)) / Decimal(100)` 후 `float()` 캐스팅
- `price` 는 `float` 그대로 (EntrySignal 모델 한계 — BAR-45 에서 강화)
- `tp_pct`, `sl_pct` 는 `Decimal` 보존 후 metadata 에 `str(decimal_value)` 로 저장 (json 직렬화 호환)

```python
from decimal import Decimal, ROUND_HALF_UP

def _normalize_score(total_score: float) -> float:
    return float(
        (Decimal(str(total_score)) / Decimal(100)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
    )
```

### 3.7 pyproject.toml 추가 사항

```toml
[tool.pytest.ini_options]
testpaths = ["backend/tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --tb=short"

[tool.coverage.run]
source = ["backend/legacy_scalping"]
omit = ["backend/legacy_scalping/main.py", "backend/legacy_scalping/scripts/*"]
```

### 3.8 Makefile 신규 타겟

```makefile
test-legacy: ## BAR-41 어댑터 + 후속 legacy_scalping 단위 테스트
	@echo "[BAR-41] Running pytest backend/tests/legacy_scalping/..."
	@$(PYTHON) -m pytest backend/tests/legacy_scalping/ -v --cov=backend.legacy_scalping --cov-report=term-missing
	@echo "[BAR-41] tests OK"
```

---

## 4. 8 Test Cases (Implementation Spec)

```python
# backend/tests/legacy_scalping/test_adapter.py
import pytest
from datetime import datetime
from pydantic import ValidationError
from backend.legacy_scalping._adapter import to_entry_signal
from backend.legacy_scalping.strategy.scalping_team.base_agent import (
    ScalpingAnalysis, StockSnapshot
)


class TestToEntrySignal:
    """BAR-41 어댑터 8 케이스 (Plan §4.2)"""

    # ── 정상 변환 (T1~T3) ──
    def test_t1_dict_form_full_fields(self):
        """T1: dict 형태 legacy signal (모든 필드) → EntrySignal"""

    def test_t2_scalping_analysis_dataclass(self):
        """T2: ScalpingAnalysis dataclass → EntrySignal (timing → signal_type 매핑)"""

    def test_t3_score_normalization(self):
        """T3: total_score=85 → score=0.85"""

    # ── Fallback (T4~T5) ──
    def test_t4_name_fallback_to_symbol(self):
        """T4: name 누락 → symbol 을 name 으로 사용"""

    def test_t5_price_missing_raises(self):
        """T5: price 도출 불가 (snapshot 없고 optimal=0) → ValueError"""

    # ── 거부 (T6~T7) ──
    def test_t6_none_input_raises_typeerror(self):
        """T6: legacy_data=None → TypeError"""

    def test_t7_score_out_of_range(self):
        """T7: total_score=120 → ValidationError"""

    # ── 경계 (T8) ──
    def test_t8_score_zero_boundary(self):
        """T8: total_score=0 → score=0.0 (정상)"""
```

각 테스트는 sample fixture (`backend/tests/conftest.py`) 의 `sample_scalping_analysis()` 또는 `sample_legacy_dict()` 를 사용.

### 4.1 Fixture 골격

```python
# backend/tests/conftest.py
import pytest
from backend.legacy_scalping.strategy.scalping_team.base_agent import (
    ScalpingAnalysis, StockSnapshot
)


@pytest.fixture
def sample_stock_snapshot():
    return StockSnapshot(
        code="005930", name="삼성전자",
        price=72000.0, open=71500.0, high=72500.0, low=71000.0,
        prev_close=71200.0, volume=15_000_000, change_pct=1.12,
        trade_value=1_080_000_000_000.0, volume_ratio=1.5,
        category="강세주", score=85.0,
    )


@pytest.fixture
def sample_scalping_analysis(sample_stock_snapshot):
    return ScalpingAnalysis(
        code="005930", name="삼성전자", rank=1,
        total_score=85.0, confidence=0.78,
        timing="즉시", consensus_level="다수합의",
        optimal_entry_price=72000.0,
        scalp_tp_pct=3.0, scalp_sl_pct=-3.0, hold_minutes=15,
        top_reasons=["VWAP 돌파", "거래량 폭증", "골든타임"],
        surge_type="intraday", intraday_atr=850.0,
        snapshot=sample_stock_snapshot,
    )


@pytest.fixture
def sample_legacy_dict():
    return {
        "code": "005930", "name": "삼성전자",
        "price": 72000.0,
        "total_score": 85.0,
        "timing": "즉시", "consensus_level": "다수합의",
        "top_reasons": ["VWAP 돌파"],
    }
```

---

## 5. Verification Scenarios (V1 ~ V6)

| # | 시나리오 | 명령 | 기대 결과 |
|---|---|---|---|
| V1 | pytest 8 케이스 통과 | `make test-legacy` | exit 0, 8 passed |
| V2 | 라인 커버리지 ≥ 80% | `pytest --cov` | `_adapter.py` ≥ 80% |
| V3 | BAR-40 dry-run 회귀 무영향 | `make legacy-scalping` | exit 0 (변동 없음) |
| V4 | 어댑터 import 시 외부 호출 0건 | `python3 -c "import backend.legacy_scalping._adapter"` | telegram/kiwoom/order grep 빈 출력 |
| V5 | EntrySignal validate 통과 | T1, T2 결과의 `EntrySignal.model_validate(...)` | 무에러 |
| V6 | Decimal 산술 (score 정규화) 정확 | T3 결과 `score == 0.85` (float 비교 ±1e-4) | 통과 |

---

## 6. Risk Mitigation Detail

| Risk (from Plan §5) | Detection | Action |
|---|---|---|
| ScalpingAnalysis 스키마 추적 누락 | T2 실패 | base_agent.py:57 의 dataclass 정의를 design §3.2 매핑 표와 일치 검증 |
| signal_type 매핑 부적절 | gap-detector 결과에서 metadata.legacy_timing 분포 확인 시 미스매치 | iterate 또는 후속 BAR (BAR-50 ScalpingConsensusStrategy) 에서 정밀 매핑 |
| EntrySignal.price=float vs Decimal | 본 design §3.6 의 `Decimal → float()` 캐스팅 정책으로 회피 | BAR-45 Strategy v2 에서 모델 자체 Decimal 화 |
| 회귀 (BAR-40 dry-run) | V3 실패 | dry-run 가드는 main.py 진입 시 sys.exit(0) — adapter import 영향 없음 검증 |
| pytest 미설치 | `make test-legacy` 시 ImportError | requirements.txt 갱신 + Dockerfile 재빌드 또는 사용자 venv 갱신 안내 |
| 8 케이스 외 edge case | gap-detector 가 추가 케이스 권고 | iterate 또는 후속 PR |

---

## 7. Out-of-Scope (재확인)

- ❌ Strategy v2 인터페이스 (BAR-45)
- ❌ ScalpingConsensusStrategy 메타전략 (BAR-50)
- ❌ EntrySignal 모델 Decimal 화 (BAR-45)
- ❌ Logger·Prometheus 통합 (BAR-43)
- ❌ legacy 모니터링(Telegram, Notion) 호환 정밀 dict (후속 BAR)

---

## 8. Implementation Checklist (Do phase 가이드)

> 본 design 승인 후 `/pdca do BAR-41` 으로 아래 체크리스트 실행.

- [ ] D1 — `backend/legacy_scalping/strategy/scalping_team/base_agent.py:57` 의 `ScalpingAnalysis` 정의 재확인
- [ ] D2 — `backend/legacy_scalping/_adapter.py` 작성 (§3.1, §3.4, §3.6)
- [ ] D3 — `backend/legacy_scalping/__init__.py` 에 `from ._adapter import to_entry_signal` re-export
- [ ] D4 — `backend/tests/__init__.py`, `backend/tests/legacy_scalping/__init__.py`
- [ ] D5 — `backend/tests/conftest.py` (§4.1 fixture)
- [ ] D6 — `backend/tests/legacy_scalping/test_adapter.py` 8 케이스 (§4)
- [ ] D7 — `backend/requirements.txt` 갱신 (pytest, pytest-cov)
- [ ] D8 — `pyproject.toml` `[tool.pytest.ini_options]` 추가 (없으면 신규)
- [ ] D9 — `Makefile` `test-legacy` 타겟 (§3.8)
- [ ] D10 — V1~V6 검증 시나리오 실행
- [ ] D11 — PR 생성 (라벨: `area:repo` `phase:0` `priority:p0` `ai-generated`)

---

## 9. Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-06 | 초기 design — Plan 1.0 의 §8 작업단계를 11 단계로 상세화, 매핑 표·8 테스트 시나리오·6 검증 정의 | beye (CTO-lead) |
