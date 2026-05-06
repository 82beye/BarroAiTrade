# BAR-53 NxtGateway 1차 — Design ↔ Implementation Gap Analysis

**Analyzed**: 2026-05-06
**Design**: `docs/02-design/features/bar-53-nxt-gateway.design.md`
**Plan**: `docs/01-plan/features/bar-53-nxt-gateway.plan.md`
**Implementation**: `backend/core/gateway/nxt.py`, `backend/models/market.py`, `backend/tests/gateway/test_nxt.py`

## Summary

| Metric | Value |
|---|---|
| 총 항목 수 | 10 |
| 매치 항목 수 | 9.5 |
| **매치율** | **95 %** |
| 상태 | **PASS** (≥ 90 %) |

## Verification Matrix

| # | Item | 결과 | 근거 |
|---|------|:----:|------|
| 1 | INxtGateway Protocol 11 메서드 | MATCH | `nxt.py:49-66` |
| 2 | NxtGatewayManager 의존성 주입 (primary/fallback/session_service) | MATCH | `nxt.py:185-199` |
| 3 | 30초 fail threshold + failover | MATCH | `_primary_down_since` 누적 후 `_failover()` |
| 4 | 5분 lag → 재연결 + exponential backoff (1,2,4,8,16,32s) | MATCH | `msg_lag_threshold_seconds=300.0`, `2 ** i, max 32` |
| 5 | 재연결 3회 실패 → DEGRADED/DOWN | MATCH | `_max_reconnect=3` 초과 분기 |
| 6 | 세션 가용 외 → pending + flush_pending | MATCH | 3종 pending buffer + `flush_pending()` |
| 7 | fallback 운용 healthy → DEGRADED 표시 | MATCH | active≠primary 시 OK 대신 DEGRADED |
| 8 | Pydantic v2 frozen + Decimal | MATCH | Tick/Quote/OrderBookL2/Trade `ConfigDict(frozen=True)` + Decimal 필드 |
| 9 | NXT_AVAILABLE_SESSIONS 정확성 | MATCH | 5 세션 포함, CLOSED/INTERLUDE/KRX_CLOSING_AUCTION 제외 |
| 10 | 테스트 25 케이스 PASSED | PARTIAL | 25 PASSED 확인 (실 pytest 결과: `25 passed in 0.11s`); gap-detector 정적 카운트는 19개로 일부 클래스 단위 통합 표시 — 실 실행 결과 = 25 |

## 미세 갭 / 권장 후속

- 항목 10 의 PARTIAL 은 정적 클래스 카운트 차이일 뿐 실 pytest 결과는 25 케이스 모두 PASSED. 운영 영향 없음.
- BAR-53.5 (실 키움/KOSCOM 어댑터) 진입 시 §6의 `#18-19 lag 5분 트리거` 별도 명시 케이스 분리 권장.
- 운영 환경에서 7일 무중단 수신 검증은 BAR-53.5 진입 후 BAR-54 게이트로 누적 측정.

## 권장 후속 액션

1. ✅ pdca-iterator 트리거 **불필요** (95% ≥ 90% PASS)
2. `/pdca report BAR-53` 진행
3. BAR-54 (CompositeOrderBook + UI) 착수 — 본 게이트웨이의 `OrderBookL2` stream 을 KRX 와 병합

**판정**: PASS — report 단계로 진행.
