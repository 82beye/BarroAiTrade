# BAR-52 Design ↔ Implementation Gap Analysis

**Analyzed**: 2026-05-06
**Design**: `docs/02-design/features/bar-52-market-session.design.md`
**Plan**: `docs/01-plan/features/bar-52-market-session.plan.md`
**Implementation**: `backend/models/market.py`, `backend/core/market_session/`, `backend/models/strategy.py`, `backend/tests/market_session/test_service.py`

## Summary

| Metric | Value |
|---|---|
| 총 항목 수 | 9 |
| 매치 항목 수 | 9 |
| **매치율** | **100 %** |
| 상태 | **PASS** (≥ 90 %) |

## Verification Matrix

| # | Item | Design Source | Implementation | 결과 |
|---|------|--------------|----------------|:---:|
| 1 | Exchange enum (KRX, NXT, COMPOSITE) | design §1.1 / plan FR-01 | `backend/models/market.py` Exchange enum 3개 | MATCH |
| 2 | TradingSession enum 8개 | design §1.1 / plan FR-02 | `backend/models/market.py` TradingSession enum 8개, 값 일치 | MATCH |
| 3 | MarketSessionService 6개 메서드 (`__init__`, `add_holiday`, `remove_holiday`, `is_holiday`, `get_session`, `available_exchanges`, `available_orders`) | design §1.2 | `backend/core/market_session/service.py` 6개 + 보너스 `holidays` property | MATCH |
| 4 | KST timezone 상수 노출 | design §1.2 | `service.py:21` 정의, `__init__.py:7` re-export | MATCH |
| 5 | 시간표 분기 (8 세션 임계점 8:00/8:30/9:00/15:20/15:30/15:40/18:00/20:00 + 우선순위 정책) | design §1.2 / plan §5.1, §5.2 | `service.py:87-107` 동일 | MATCH |
| 6 | 가용 거래소 매트릭스 8 세션 | design §1.2 / plan §4.2 | `service.py:25-34` `_AVAILABLE_EXCHANGES` | MATCH |
| 7 | 가용 주문 매트릭스 (market/limit/after_hours) | design §1.2 | `service.py:115-126` — CLOSED/INTERLUDE all-False, KRX_CLOSING_AUCTION limit-only, KRX/NXT_AFTER limit+after_hours, REGULAR market+limit | MATCH |
| 8 | AnalysisContext.trading_session 정식 type (Optional[TradingSession]) | design §1.3 / plan FR-07 | `backend/models/strategy.py` 직접 import + `Optional[TradingSession]` (placeholder 해소) | MATCH |
| 9 | 24+ 테스트 시나리오 → 43 PASSED | design §2 / plan §4.1 DoD | `tests/market_session/test_service.py` 43 tests, 98% coverage | MATCH |

## 누락 / 불일치 항목

**없음.**

### 미세 메모 (불일치 아님)

- 디자인 §1.3 은 `TYPE_CHECKING` 가드 + 문자열 forward ref 패턴을 예시로 제시했으나, 실제 구현은 직접 import 한 `Optional[TradingSession]` 으로 더 단순함. Pydantic v2 환경에서 동등 동작, placeholder/`Any` 제거 — 디자인 목적(정식 type 해소) 만족.
- `service.py` 는 디자인 명세 외 `remove_holiday`, `holidays` property 추가 노출 (회귀·보안 영향 없음).

## 권장 후속 액션

매치율 100% (≥ 90% PASS) 이므로 **iterator 트리거 불필요**.

1. `/pdca report BAR-52` — 완료 리포트 작성 단계로 진행.
2. BAR-53 (NxtGateway 1차) 착수 시 본 서비스의 `get_session()`/`available_exchanges()` 를 dependency 로 주입.
3. BAR-54 (CompositeOrderBook) UI 의 시간외 표시 색상 가드를 `available_orders().after_hours` 와 연결.

**판정**: PASS — pdca-iterator 호출 없이 report 단계로 진행.
