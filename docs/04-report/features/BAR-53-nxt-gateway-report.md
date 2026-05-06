# BAR-53 NxtGateway 1차 — Completion Report

**Phase**: 2 (NXT 통합 + 통합 호가창 + 거래시간 인지)
**Ticket**: BAR-53 — NxtGateway 1차 (시세 read-only)
**Status**: ✅ COMPLETED
**Date**: 2026-05-06

---

## 1. Outcomes

NXT 시세 데이터(ticker, orderbook, trade) 의 표준 인터페이스 + 매니저 + Mock 구현체가 도입되었다. 실 키움/KOSCOM 어댑터는 **BAR-53.5** (운영 OpenAPI 키 발급 후) 로 분리되며, 본 매니저에 동일 Protocol 로 plug-in 한다.

### 1.1 신규 추상화

| 구성 | 위치 | 역할 |
|------|------|------|
| `INxtGateway` Protocol | `backend/core/gateway/nxt.py` | 11 메서드 표준 시그니처 |
| `NxtGatewayManager` | 동상 | primary+fallback orchestrator + 세션 가드 + 헬스 |
| `MockNxtGateway` | 동상 | in-memory dev/test 구현체 |
| `NXT_AVAILABLE_SESSIONS` | 동상 | NXT 가용 세션 frozenset (5개) |
| `Tick` / `Quote` / `OrderBookL2` / `Trade` | `backend/models/market.py` | Pydantic v2 frozen + Decimal |
| `HealthStatus` / `GatewayStatus` | 동상 | OK/DEGRADED/DOWN |

### 1.2 정책

| 정책 | 임계값 | 효과 |
|------|--------|------|
| primary disconnect 누적 | 30 s | fallback 자동 전환 |
| 메시지 lag | 5 min | 재연결 트리거 |
| 재연결 실패 | 3 회 | DEGRADED → fallback 도 실패 시 DOWN |
| TradingSession 가용 외 | – | subscribe pending → flush_pending() 적용 |
| fallback 운용 중 healthy | – | status = DEGRADED (운영 가시성) |

### 1.3 데이터 모델 — 자금흐름 정확도

- 모든 가격 필드 `Decimal` 강제 (Tick.last_price, OrderBookL2.bids/asks, Trade.price, Quote.bid/ask)
- 모델 frozen=True — 외부에서 변조 불가
- venue: `Exchange` enum (KRX/NXT/COMPOSITE)

---

## 2. Validation

### 2.1 Tests

```
make test-nxt-gateway
─────────────────────────────────────────────
25 passed in 0.11s
```

| 클래스 | 케이스 |
|--------|:------:|
| `TestMockGateway` | 5 |
| `TestModelDecimal` / `TestModelImmutable` | 3 |
| `TestManagerSubscribe` | 3 |
| `TestManagerSessionGate` | 5 |
| `TestManagerHealth` | 2 |
| `TestManagerFailover` | 3 |
| `TestManagerLifecycle` | 2 |
| `TestAvailableSessions` | 2 |
| **합계** | **25 PASSED** |

### 2.2 회귀

전체 `pytest backend/tests/` — **191 passed, 1 skipped, 0 failed**.

### 2.3 Gap Analysis (PR #70 머지)

- 매치율 **95%** (9.5/10) — PASS
- iterator 트리거 불필요

상세: `docs/04-report/analyze/BAR-53-gap-analysis.md`

---

## 3. PR Trail

| Stage | PR | 상태 |
|-------|----|:----:|
| plan | #67 | ✅ MERGED |
| design | #68 | ✅ MERGED |
| do | #69 | ✅ MERGED (25 tests) |
| analyze | #70 | ✅ MERGED (95%) |
| report | (this) | 진행 중 |

---

## 4. Phase 2 Progress

| BAR | Title | Status |
|-----|-------|:------:|
| BAR-52 | Exchange/TradingSession enum + MarketSessionService | ✅ DONE |
| BAR-53 | NxtGateway 1차 (시세 read-only) | ✅ DONE |
| BAR-54 | CompositeOrderBook + UI | NEXT |
| BAR-55 | SOR v1 (가격/잔량 라우팅) | pending |
| BAR-53.5 | 실 키움/KOSCOM NXT 어댑터 | deferred (운영 OpenAPI 키 발급 후) |

---

## 5. Lessons & Decisions

1. **Protocol > ABC**: `typing.Protocol` 사용 → 런타임 isinstance 체크 없이 duck-typing 으로 어댑터 plug-in 가능. BAR-53.5 진입 비용 최소화.
2. **fallback healthy = DEGRADED 표시 정책**: 운영자에게 "현재 fallback 으로 운용 중" 사실을 OK 가 아닌 DEGRADED 로 가시화. 알림 임계와 일치.
3. **세션 가드 = Manager 책임**: 개별 게이트웨이가 세션 인지하지 않고 매니저가 `subscribe_*` 진입점에서 일괄 가드. 어댑터 단순화.
4. **pytest-asyncio mode=auto**: `pyproject.toml` 에 추가하여 모든 async 테스트 자동 인식. 후속 BAR (BAR-57 뉴스 수집 등) 의 비동기 테스트도 동일 정책 적용.
5. **실 어댑터 분리(BAR-53.5)**: 본 worktree 환경에서 외부 API 호출 불가하므로 인터페이스·정책·테스트만 굳힘. 운영 환경에서 IKiwoomNxtGateway / KoscomCheckNxtGateway 추가 시 본 매니저에 그대로 plug-in.

---

## 6. Next Action

`/pdca plan BAR-54` — CompositeOrderBook + UI. 본 게이트웨이의 `OrderBookL2` stream 을 KRX 호가와 병합한 통합 호가창 + 잔량 가격별 색상 구분 UI.
