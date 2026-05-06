# BAR-54 — CompositeOrderBook + UI Design

**Plan**: `docs/01-plan/features/bar-54-composite-orderbook.plan.md`
**선행**: BAR-52 (MarketSessionService) ✅ / BAR-53 (NxtGateway 1차) ✅

---

## §0. 분리 정책 확정

| 트랙 | BAR | 본 사이클 |
|------|-----|:---:|
| backend (모델·서비스·테스트) | **BAR-54a** | 정식 do |
| frontend (`orderbook-composite.tsx`) | **BAR-54b** | 명세만 (§4) |

---

## §1. 데이터 모델

### 1.1 `CompositeOrderBookL2` (`backend/models/market.py` 확장)

```python
class CompositeLevel(BaseModel):
    model_config = ConfigDict(frozen=True)
    price: Decimal
    total_qty: int
    breakdown: dict[Exchange, int]   # 예: {Exchange.KRX: 100, Exchange.NXT: 50}


class CompositeOrderBookL2(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    ts: datetime
    bids: list[CompositeLevel]   # 가격 내림차순
    asks: list[CompositeLevel]   # 가격 오름차순
    venues: frozenset[Exchange]  # 머지에 참여한 거래소

    @property
    def best_bid(self) -> Optional[Decimal]: ...
    @property
    def best_ask(self) -> Optional[Decimal]: ...
    @property
    def mid_price(self) -> Optional[Decimal]: ...
    @property
    def spread(self) -> Optional[Decimal]: ...

    def venue_breakdown(self, price: Decimal) -> dict[Exchange, int]: ...
```

`mid_price` = `(best_bid + best_ask) / 2` (Decimal). `spread` = `best_ask - best_bid`.

### 1.2 BAR-53 의 `OrderBookL2` 와의 관계

`OrderBookL2` 는 단일 거래소 L2 호가, `CompositeOrderBookL2` 는 다중 거래소 합산 + breakdown 보존. 별개 타입으로 유지.

---

## §2. CompositeOrderBookService

```python
class CompositeOrderBookService:
    """KRX + NXT 호가를 단일 가격축에 병합."""

    def merge(
        self,
        krx: Optional[OrderBookL2],
        nxt: Optional[OrderBookL2],
        symbol: str,
        ts: Optional[datetime] = None,
    ) -> CompositeOrderBookL2: ...
```

### 2.1 머지 알고리즘

1. **단일 입력 가드** (FR-07): krx / nxt 중 하나 None 이면 다른 한쪽만으로 즉시 변환 (`_to_composite(book, venue)`).
2. **양쪽 입력**:
   - bids: KRX bids + NXT bids → 가격별 dict 누적 → 가격 내림차순 정렬
   - asks: KRX asks + NXT asks → 가격별 dict 누적 → 가격 오름차순 정렬
3. **breakdown 보존**: 동일 가격에 양쪽 잔량 → `{KRX: q1, NXT: q2}` 둘 다 기록
4. **단일 거래소 가격**: 한쪽만 있으면 `{KRX: q1}` 1개 키만
5. **venues**: 입력에 포함된 거래소 frozenset

### 2.2 best/mid/spread 계산

- `best_bid` = bids 첫 가격, `best_ask` = asks 첫 가격
- 모두 Decimal 산술 (float 금지)

### 2.3 venue_breakdown(price)

- bids · asks 양쪽 검색 → 매칭 가격 발견 시 `level.breakdown` 반환
- 미발견 시 빈 dict

---

## §3. 시퀀스

```
NxtGatewayManager.on_orderbook ─┐
                                ├──► CompositeOrderBookService.merge() ──► CompositeOrderBookL2 ──► (BAR-55 SOR / UI)
KiwoomGateway.on_orderbook ─────┘
```

- 양 stream 의 최신 OrderBookL2 를 in-memory 캐시(`Dict[symbol, (krx_book, nxt_book)]`) → on_orderbook 콜백마다 merge 재계산
- 본 BAR 에서는 캐시 어댑터를 노출하지 않고 순수 서비스만 제공 (캐시는 BAR-72 Redis 캐시 또는 호출자 책임)

---

## §4. Frontend (BAR-54b 명세, 본 BAR 에서 구현 X)

### 4.1 컴포넌트 시그니처

```tsx
// frontend/components/orderbook-composite.tsx
type Props = {
  data: CompositeOrderBookL2;
  onPriceClick?: (price: Decimal, breakdown: Record<Exchange, number>) => void;
};

export function OrderbookComposite({ data, onPriceClick }: Props) { ... }
```

### 4.2 표현 규칙

- 가격 행 색상: KRX 단독 = 진청, NXT 단독 = 청록, 둘 다 = 그라데이션
- 잔량 막대: `total_qty` 비례 폭, 막대 내부 KRX:NXT 비율 색상 분할
- best_bid / best_ask = 굵은 테두리 + 강조
- hover: tooltip = `Exchange.KRX: 100, Exchange.NXT: 50`
- TanStack Query polling 1s (BAR-72 에서 WebSocket 으로 전환)

### 4.3 후속 BAR 이행 단위

`frontend/components/orderbook-composite.tsx` 신규 + Storybook 스토리 + Playwright E2E 1 시나리오 = BAR-54b 단위.

---

## §5. 테스트 시나리오 (20+, NFR-03/04)

| # | Class | Case |
|---|-------|------|
| 1-3 | `TestMergeBasic` | KRX-only / NXT-only / 양쪽 |
| 4 | `TestMergeBasic` | 양쪽 None → 빈 CompositeOrderBookL2 |
| 5-6 | `TestPriceAggregation` | 동일 가격 잔량 합산 / breakdown 보존 |
| 7 | `TestPriceAggregation` | 단일 거래소 가격 → breakdown 1 키 |
| 8 | `TestSorting` | bids 내림차순 |
| 9 | `TestSorting` | asks 오름차순 |
| 10-11 | `TestBest` | best_bid / best_ask |
| 12 | `TestBest` | best_bid 없음(빈 bids) → None |
| 13 | `TestMidSpread` | mid_price (Decimal) |
| 14 | `TestMidSpread` | spread (Decimal) |
| 15 | `TestVenueBreakdown` | bids 측 가격 검색 |
| 16 | `TestVenueBreakdown` | asks 측 가격 검색 |
| 17 | `TestVenueBreakdown` | 미발견 → 빈 dict |
| 18 | `TestEdge` | 빈 OrderBookL2(bids=[], asks=[]) |
| 19 | `TestEdge` | KRX bid > NXT ask 교차 호가 (그대로 머지, 검증 없음) |
| 20 | `TestEdge` | 음수 잔량 입력 → 그대로 보존 (검증 시 별도 정책) |
| 21 | `TestDecimal` | Decimal 소수점 정확도 (0.5 + 0.25) |
| 22 | `TestPerformance` | 100건 머지 (10 단계) → 평균 ≤ 5ms |

---

## §6. 디렉터리

| 경로 | 역할 |
|------|------|
| `backend/models/market.py` (확장) | CompositeLevel, CompositeOrderBookL2 |
| `backend/core/gateway/composite_orderbook.py` (신규) | CompositeOrderBookService |
| `backend/tests/gateway/test_composite_orderbook.py` (신규) | 22 케이스 |
| `Makefile` | `test-composite-orderbook` 타겟 |

---

## §7. 후속

- **BAR-54b** : `frontend/components/orderbook-composite.tsx` + Storybook + Playwright (운영 노드 환경)
- **BAR-55** : SOR v1 — 본 서비스의 best_bid/best_ask + venue_breakdown 을 라우팅 결정 입력으로 사용
- **BAR-72** : 1s polling → WebSocket 전환 (Phase 6)
