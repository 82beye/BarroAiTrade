# BAR-55 SOR v1 — Completion Report

**Phase**: 2 (NXT 통합 + 통합 호가창 + 거래시간 인지) — **종료 게이트**
**Ticket**: BAR-55 — SmartOrderRouter
**Status**: ✅ COMPLETED
**Date**: 2026-05-06

---

## 1. Outcomes

CompositeOrderBookL2 입력으로 KRX vs NXT 라우팅을 결정하는 **SmartOrderRouter** 가 도입되었다. 본 라우터는 결정만 반환(RoutingDecision); 실 주문 송수신은 BAR-63 ExitPlan + OrderExecutor 통합 시점에 plug-in.

### 1.1 신규 추상화

| 구성 | 위치 | 역할 |
|------|------|------|
| `OrderRequest` | `backend/models/order.py` | symbol/side/qty/order_type/limit_price/force_venue |
| `RoutingDecision` | 동상 | venue/expected_price/expected_qty/reason + is_routed |
| `RoutingReason` | 동상 | 6 사유 (PRICE_FIRST, QTY_FIRST, FORCED, SESSION_BLOCKED, NO_LIQUIDITY, LIMIT_UNFILLABLE) |
| `SmartOrderRouter` | `backend/core/execution/router.py` | 6단 라우팅 알고리즘 |

### 1.2 알고리즘 (6단)

```
1. force_venue 분기  (가용 검사 → FORCED 또는 SESSION_BLOCKED)
2. target_price 결정 (buy → best_ask, sell → best_bid)
3. NO_LIQUIDITY 검사
4. limit 호환성     (LIMIT_UNFILLABLE)
5. venue_breakdown ∩ 가용 거래소 → SESSION_BLOCKED 가능
6. 단일=PRICE_FIRST / 다중=QTY_FIRST (KRX deterministic 우선)
```

`expected_qty = min(req.qty, available_breakdown[venue])` — 1차에서는 split 없음.

---

## 2. Validation

### 2.1 Tests

```
make test-router
─────────────────────────────────────────────
27 passed in 0.05s
```

| 클래스 | 케이스 |
|--------|:------:|
| `TestPriceFirst` | 4 |
| `TestQtyFirst` | 3 |
| `TestForceVenue` | 3 |
| `TestSessionBlock` | 3 |
| `TestLimit` | 5 |
| `TestNoLiquidity` | 3 |
| `TestQtyCap` | 2 |
| `TestModel` | 2 |
| `TestAccuracy` (100건) | 1 |
| `TestPerformance` | 1 |
| **합계** | **27 PASSED** |

### 2.2 100건 정확도 매트릭스

- 무작위 50건 (PriceFirst 명확 케이스) + edge 50건 (QtyFirst 동가격 잔량)
- **Accuracy: 100.0%** (NFR-03 요구 100% 충족)

### 2.3 회귀

전체 `pytest backend/tests/` — **240 passed, 1 skipped, 0 failed** (213 → 240).

### 2.4 Gap Analysis (PR #80 머지)

- 매치율 **100%** (10/10) — PASS
- iterator 트리거 불필요

상세: `docs/04-report/analyze/BAR-55-gap-analysis.md`

---

## 3. PR Trail

| Stage | PR | 상태 |
|-------|----|:----:|
| plan | #77 | ✅ MERGED |
| design | #78 | ✅ MERGED |
| do | #79 | ✅ MERGED (27 tests) |
| analyze | #80 | ✅ MERGED (100%) |
| report | (this) | 진행 중 |

---

## 4. Phase 2 Closure

- BAR-52 ✅ MarketSessionService
- BAR-53 ✅ NxtGateway 1차
- BAR-54 ✅ CompositeOrderBookService (54a backend)
- BAR-55 ✅ SmartOrderRouter (Phase 2 종료 게이트 통과)
- 별도 통합 보고: `docs/04-report/PHASE-2-nxt-integration-report.md`

---

## 5. Lessons & Decisions

1. **Stateless router**: SmartOrderRouter 는 시각·book 입력만으로 결정. 호출 간 상태 없음 → 테스트 단순.
2. **결정 vs 실행 분리**: 본 BAR 는 RoutingDecision 만 반환. OrderExecutor 통합은 BAR-63 (ExitPlan 일급화 + Phase 4 자동매매 엔진).
3. **KRX deterministic 우선**: 동가격·동잔량 시 `max` key tuple `(qty, krx_priority)` 로 결정. 비결정성 회피.
4. **100건 매트릭스**: Plan/Design 의 NFR-03 "100% 정확도" 가 단순 보증이 아니라 자동화된 매트릭스로 매 PR 회귀.

---

## 6. Next Action

- `docs/04-report/PHASE-2-nxt-integration-report.md` 작성 (Phase 2 4 BAR + 분기된 BAR-53.5/54b 정리).
- `/pdca plan BAR-56` — Phase 3 진입 (DB 마이그레이션 SQLite → Postgres + pgvector).
