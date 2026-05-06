# BAR-55 — SOR v1 Design

**Plan**: `docs/01-plan/features/bar-55-sor-v1.plan.md`
**선행**: BAR-52 / BAR-53 / BAR-54 ✅

---

## §1. 데이터 모델 (`backend/models/order.py` 신규)

```python
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from backend.models.market import Exchange


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class RoutingReason(str, Enum):
    PRICE_FIRST = "price_first"           # 가격 우선
    QTY_FIRST = "qty_first"               # 동가격 잔량 우선
    FORCED = "forced"                     # force_venue
    SESSION_BLOCKED = "session_blocked"   # 세션 가용 외
    NO_LIQUIDITY = "no_liquidity"         # 호가 없음
    LIMIT_UNFILLABLE = "limit_unfillable" # limit 가격 호환 X


class OrderRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    side: OrderSide
    qty: int = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[Decimal] = None
    force_venue: Optional[Exchange] = None
    requested_at: Optional[datetime] = None


class RoutingDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    request: OrderRequest
    venue: Optional[Exchange]   # None 이면 거부
    expected_price: Optional[Decimal]
    expected_qty: int
    reason: RoutingReason

    @property
    def is_routed(self) -> bool:
        return self.venue is not None
```

---

## §2. SmartOrderRouter (`backend/core/execution/router.py` 신규)

```python
class SmartOrderRouter:
    def __init__(self, session_service: MarketSessionService) -> None: ...

    def route(
        self,
        req: OrderRequest,
        book: CompositeOrderBookL2,
        now: Optional[datetime] = None,
    ) -> RoutingDecision: ...
```

### 2.1 알고리즘 단계 (plan §6 의사코드 정제)

```
Step 1. force_venue 분기
   └─ force_venue not None?
        └─ 가용 거래소 외 → SESSION_BLOCKED
        └─ 외 → FORCED (expected_price 는 book 에서 조회)

Step 2. 가격 측 결정
   buy  → target_price = best_ask, levels = asks
   sell → target_price = best_bid, levels = bids

   target_price is None → NO_LIQUIDITY

Step 3. limit 호환성
   order_type == LIMIT?
     buy:  limit_price < target_price → LIMIT_UNFILLABLE
     sell: limit_price > target_price → LIMIT_UNFILLABLE

Step 4. venue_breakdown 조회
   breakdown = book.venue_breakdown(target_price)
   available_breakdown = {v: q for v,q in breakdown.items()
                          if v in session.available_exchanges()}
   not available_breakdown → SESSION_BLOCKED

Step 5. 단일 venue → PRICE_FIRST
   venue = sole key, expected_qty = min(req.qty, available_breakdown[venue])

Step 6. 다중 venue → QTY_FIRST
   venue = max by qty, expected_qty = min(req.qty, available_breakdown[venue])
```

### 2.2 expected_price for FORCED

force_venue 일 때 expected_price 는 해당 거래소의 best (book.bids/asks 에서 첫 매칭 venue 가격). 없으면 None — 본 BAR 에서는 expected_price=None 으로 두고 reason=FORCED 유지 (호출자 책임).

---

## §3. 시퀀스

```
Strategy ──► OrderRequest ──► SmartOrderRouter.route(req, book, now)
                                                    │
                                                    ├── MarketSessionService.get_session
                                                    ├── available_exchanges()
                                                    │
                                                    └── CompositeOrderBookL2
                                                          ├── best_bid / best_ask
                                                          └── venue_breakdown(price)
                                                    │
                                                    ▼
                                              RoutingDecision
                                                    │
                                              (BAR-63 OrderExecutor 통합)
```

---

## §4. 테스트 시나리오 (30+, NFR-04)

| # | Class | Case | Expected |
|---|-------|------|----------|
| 1-2 | `TestPriceFirst` | KRX best ask < NXT, buy | KRX, PRICE_FIRST |
| 3-4 | `TestPriceFirst` | NXT best ask < KRX, buy | NXT, PRICE_FIRST |
| 5-6 | `TestPriceFirst` | KRX best bid > NXT, sell | KRX, PRICE_FIRST |
| 7 | `TestQtyFirst` | 동가격, KRX 100 vs NXT 50 → KRX | KRX, QTY_FIRST |
| 8 | `TestQtyFirst` | 동가격, KRX 30 vs NXT 200 → NXT | NXT, QTY_FIRST |
| 9 | `TestQtyFirst` | 동가격 동잔량 → KRX 또는 NXT (deterministic, 첫 매칭) | 결정적 |
| 10-11 | `TestForceVenue` | force_venue=KRX | KRX, FORCED |
| 12 | `TestForceVenue` | force_venue=NXT | NXT, FORCED |
| 13 | `TestForceVenue` | force_venue=NXT, INTERLUDE 세션 | None, SESSION_BLOCKED |
| 14 | `TestSessionBlock` | NXT only 호가창, KRX_CLOSING_AUCTION 세션, buy → SESSION_BLOCKED |
| 15 | `TestSessionBlock` | KRX only, NXT_AFTER 세션, buy → SESSION_BLOCKED |
| 16 | `TestSessionBlock` | CLOSED 세션, force=KRX → SESSION_BLOCKED |
| 17-18 | `TestLimit` | limit buy, limit_price ≥ best_ask → routed |
| 19 | `TestLimit` | limit buy, limit_price < best_ask → LIMIT_UNFILLABLE |
| 20 | `TestLimit` | limit sell, limit_price ≤ best_bid → LIMIT_UNFILLABLE |
| 21 | `TestLimit` | limit sell, limit_price > best_bid → routed |
| 22 | `TestNoLiquidity` | 빈 호가창, buy → NO_LIQUIDITY |
| 23 | `TestNoLiquidity` | bids 만 있음, buy → NO_LIQUIDITY |
| 24 | `TestNoLiquidity` | asks 만 있음, sell → NO_LIQUIDITY |
| 25 | `TestQtyCap` | req.qty 100, expected venue qty 60 → expected_qty=60 |
| 26 | `TestQtyCap` | req.qty 50, expected venue qty 100 → expected_qty=50 |
| 27 | `TestModel` | OrderRequest qty=0 → ValidationError |
| 28 | `TestModel` | RoutingDecision frozen → 변조 차단 |
| 29 | `TestAccuracy` | **100건 매트릭스** — 무작위 50 + edge 50, expected venue 일치 100% |
| 30 | `TestPerformance` | 100건 route() avg ≤ 5ms |

---

## §5. 디렉터리

| 경로 | 역할 |
|------|------|
| `backend/models/order.py` (신규) | OrderSide / OrderType / RoutingReason / OrderRequest / RoutingDecision |
| `backend/core/execution/__init__.py` (신규) | – |
| `backend/core/execution/router.py` (신규) | SmartOrderRouter |
| `backend/tests/execution/__init__.py` (신규) | – |
| `backend/tests/execution/test_router.py` (신규) | 30 케이스 |
| `Makefile` | `test-router` 타겟 |

---

## §6. 후속

- BAR-63 (Phase 4): ExitPlan + OrderExecutor 가 본 라우터의 RoutingDecision 을 입력으로 사용
- BAR-79 (Phase 6): SOR v2 — split 라우팅 + 슬리피지 모델
- BAR-67~70 (Phase 5): 라우팅 결과 audit log
