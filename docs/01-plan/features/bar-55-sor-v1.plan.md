# BAR-55 — SOR v1 (Smart Order Router, 가격·잔량 라우팅)

**Phase**: 2 (NXT 통합 + 통합 호가창 + 거래시간 인지) — **종료 게이트**
**선행**: BAR-52 (MarketSessionService) ✅ / BAR-53 (NxtGateway 1차) ✅ / BAR-54 (CompositeOrderBookService) ✅

---

## 1. 목표 (Why)

CompositeOrderBookL2 입력으로 **주문 1건을 KRX vs NXT 중 어디로 보낼지 결정** 하는 라우터. 가격 우선, 동가격이면 잔량 우선. 사용자는 강제 거래소 모드(force_venue)로 우회 가능.

**1차 스코프**: 결정 엔진 + 모의 주문 라우팅 (실 OrderExecutor 통합 X — 본 라우터는 결정만 반환). 실 주문 송수신은 BAR-63 ExitPlan 통합 + BAR-67 보안 시동 후.

---

## 2. 기능 요구사항 (FR)

| ID | 요구 |
|----|------|
| FR-01 | `OrderRequest` 모델 — symbol, side(buy/sell), qty, force_venue(Optional[Exchange]), order_type(market/limit), limit_price(Optional[Decimal]) |
| FR-02 | `RoutingDecision` 모델 — venue, expected_price, expected_qty, reason (가격우선·잔량우선·강제·세션차단) |
| FR-03 | `SmartOrderRouter.route(req, composite_book, session) -> RoutingDecision` |
| FR-04 | 가격 우선: buy → ask 낮은 venue, sell → bid 높은 venue |
| FR-05 | 동가격이면 잔량 우선 (해당 가격의 venue_breakdown 비교) |
| FR-06 | 잔량 부족 시 split 결정 (1차에선 단일 venue 만 — split 은 SOR v2 BAR-79) |
| FR-07 | force_venue 입력 시 가격·잔량 무시하고 강제 라우팅 (단, 세션 가용 검사) |
| FR-08 | TradingSession 검사 — 가용 거래소 외에 라우팅 시도 시 reason="session_blocked" 로 거부 |
| FR-09 | limit 주문 — limit_price 가 best_ask/bid 와 호환 안 되면 reason="limit_unfillable" |

---

## 3. 비기능 요구사항 (NFR)

| ID | 요구 |
|----|------|
| NFR-01 | route() 1회 latency P95 ≤ 1ms (10단계 호가, 단일 스레드) |
| NFR-02 | Decimal 정확도 손실 0건 |
| NFR-03 | 100건 모의 라우팅 정확도 100% (시나리오별 expected vs actual venue 비교) |
| NFR-04 | 단위 테스트 커버리지 ≥ 80% |

---

## 4. 비고려 (Out of Scope)

- ❌ split 라우팅 (다중 venue 분배) — SOR v2 BAR-79
- ❌ 실 OrderExecutor 통합 (BAR-63)
- ❌ 슬리피지·수수료·세금 모델링 — BAR-51 백테스터 v2
- ❌ 라우팅 결과 audit log — Phase 5 보안 (BAR-68)

---

## 5. DoD — Phase 2 종료 게이트

- [ ] `backend/models/order.py` (신규): OrderRequest, RoutingDecision, RoutingReason enum
- [ ] `backend/core/execution/router.py` (신규): SmartOrderRouter
- [ ] `backend/tests/execution/test_router.py` 신규 — **30+ 시나리오 + 100건 정확도 매트릭스**:
  - 가격 우선 (KRX low ask, NXT low ask)
  - 동가격 잔량 우선
  - force_venue (KRX/NXT)
  - 세션 차단 (CLOSED, INTERLUDE, KRX_CLOSING_AUCTION 일 때 NXT 강제 → 거부)
  - limit 주문 호환성
  - 빈 호가창 / 단일 거래소 호가창
  - 잔량 부족 (1차에선 expected_qty 가 가용 잔량으로 캡)
  - 100건 매트릭스: 시나리오 generator 로 무작위 50건 + edge 50건 → expected venue 일치
- [ ] `Makefile` `test-router` 타겟
- [ ] 회귀 0 fail
- [ ] gap-detector 매치율 ≥ 90%
- [ ] **`docs/04-report/PHASE-2-nxt-integration-report.md` 작성** — Phase 2 4 BAR 통합 보고

---

## 6. 알고리즘 의사코드

```
def route(req, book, session):
    # 1. 세션 가드
    if force_venue:
        if force_venue not in session.available_exchanges():
            return RoutingDecision(blocked, "session_blocked")
        return RoutingDecision(force_venue, "forced")

    # 2. 가격 우선
    if side == buy:
        target_price = book.best_ask
        target_levels = book.asks
    else:
        target_price = book.best_bid
        target_levels = book.bids

    if target_price is None:
        return RoutingDecision(blocked, "no_liquidity")

    breakdown = book.venue_breakdown(target_price)

    # 3. limit 호환성
    if req.order_type == "limit":
        if (side == buy and req.limit_price < target_price) or \
           (side == sell and req.limit_price > target_price):
            return RoutingDecision(blocked, "limit_unfillable")

    # 4. 단일 venue
    if len(breakdown) == 1:
        venue = list(breakdown.keys())[0]
        if venue not in session.available_exchanges():
            return RoutingDecision(blocked, "session_blocked")
        return RoutingDecision(venue, target_price, breakdown[venue], "price_first")

    # 5. 동가격 잔량 우선
    available = {v: q for v, q in breakdown.items() if v in session.available_exchanges()}
    if not available:
        return RoutingDecision(blocked, "session_blocked")
    venue = max(available, key=available.get)
    return RoutingDecision(venue, target_price, available[venue], "qty_first")
```

---

## 7. 의존성 / 위험

| 위험 | 트리거 | 대응 |
|------|--------|------|
| 가격 정밀도 mismatch | float 사용 | Decimal 강제 |
| 세션 변동 | route() 와 actual order 사이 시각 변동 | 본 라우터는 결정 시점 세션만 검사. 실 OrderExecutor (BAR-63) 가 재검사 |
| qty 부족 | 1차에선 split 미지원 | expected_qty 캡 + reason="partial_fill_warning" |

---

## 8. 다음 단계

1. `/pdca design BAR-55` — 모델·시그니처 확정
2. `/pdca do BAR-55` — 30+ 테스트
3. `/pdca analyze BAR-55` — gap-detector
4. `/pdca report BAR-55` — 완료 리포트
5. **`PHASE-2-nxt-integration-report.md`** — 4 BAR 통합 보고서 (Phase 2 종료)
6. Phase 3 진입 준비
