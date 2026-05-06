# BAR-54 — CompositeOrderBook + UI

**Phase**: 2 (NXT 통합 + 통합 호가창 + 거래시간 인지)
**선행**: BAR-52 (MarketSessionService) ✅ / BAR-53 (NxtGateway 1차) ✅
**후행**: BAR-55 (SOR v1)

---

## 1. 목표 (Why)

KRX 본장과 NXT 대체거래소의 L2 호가를 단일 가격축 위에 병합한 **CompositeOrderBook** 을 도입한다. SOR(BAR-55) 의 가격·잔량 라우팅 결정 입력으로 사용되며, 사용자에게는 UI 에서 거래소별 잔량 색상 구분으로 표출한다.

---

## 2. 스코프 분리 — 1.5 BAR 형태

| 서브 BAR | 트랙 | 산출물 | 본 worktree 검증 가능? |
|----------|------|--------|:-:|
| BAR-54a | backend | `CompositeOrderBookService` + 머지 로직 + tests | ✅ |
| BAR-54b | frontend | `frontend/components/orderbook-composite.tsx` + 잔량 색상 구분 + venue_breakdown 툴팁 | ⚠️ 컴포넌트 작성만, 실 빌드는 운영 환경 |

본 BAR-54 사이클의 do 단계에서는 **BAR-54a 만 정식 구현·테스트**하고, frontend 는 design 문서에 컴포넌트 명세 + 시그니처만 고정한다. UI 정식 머지는 후속 BAR-54b (운영 노드 환경) 로 분리.

---

## 3. 기능 요구사항 (FR)

### 3.1 backend (BAR-54a)

| ID | 요구 |
|----|------|
| FR-01 | `CompositeOrderBookService.merge(krx: OrderBookL2, nxt: OrderBookL2) -> CompositeOrderBookL2` |
| FR-02 | 동일 가격 호가는 **잔량 합산** 후 venue_breakdown 보존 (가격→{krx_qty, nxt_qty}) |
| FR-03 | bids 가격 내림차순 / asks 가격 오름차순 정렬 보장 |
| FR-04 | best_bid / best_ask / mid_price / spread 계산 헬퍼 (Decimal 정확도 유지) |
| FR-05 | TradingSession 인지: KRX 미가용(예: NXT_PRE/NXT_AFTER) 시 NXT 만 입력으로 받아 그대로 반환, NXT 미가용(예: KRX_CLOSING_AUCTION) 시 KRX 만 반환 |
| FR-06 | `venue_breakdown(price: Decimal) -> dict[Exchange, int]` 메서드 — UI 툴팁용 |
| FR-07 | 입력 OrderBookL2 가 None 이면 빈 측 무시 (None 안전) |
| FR-08 | 빈 OrderBookL2 (bids=[], asks=[]) 도 안전 처리 |

### 3.2 frontend (BAR-54b, 본 BAR 에서는 명세만)

| ID | 요구 |
|----|------|
| FE-01 | `OrderbookComposite` React 컴포넌트 (props: `compositeOrderbook`, `onPriceClick`) |
| FE-02 | 가격별 KRX/NXT 잔량 색상 구분 (KRX 진청, NXT 청록, 합산 막대) |
| FE-03 | hover 시 venue_breakdown 툴팁 |
| FE-04 | best_bid/best_ask 강조 표시 |
| FE-05 | TanStack Query 로 1초 polling (초기 구현, BAR-72 에서 WS 전환) |

---

## 4. 비기능 요구사항 (NFR)

| ID | 요구 |
|----|------|
| NFR-01 | merge 1회 latency P95 ≤ 5ms (10단계 호가, 단일 스레드) |
| NFR-02 | Decimal 정확도 손실 0건 (가격 비교는 모두 Decimal) |
| NFR-03 | 단위 테스트 커버리지 ≥ 80% (backend) |
| NFR-04 | 음수/0 잔량/중복 가격 등 엣지케이스 100% 처리 |

---

## 5. 비고려 (Out of Scope)

- ❌ frontend 정식 빌드/배포 (BAR-54b 분리)
- ❌ WebSocket 실시간 갱신 (BAR-72)
- ❌ 주문 실행 (BAR-55)
- ❌ 외부 거래소(미국/홍콩) 통합 (Phase 6 BAR-76)

---

## 6. DoD

- [ ] `backend/models/market.py` 확장: `CompositeOrderBookL2` (Pydantic v2 frozen)
- [ ] `backend/core/gateway/composite_orderbook.py` (신규): `CompositeOrderBookService`
- [ ] `backend/tests/gateway/test_composite_orderbook.py` 신규 — 20+ 케이스:
  - 동일 가격 잔량 합산
  - venue_breakdown 보존
  - bids 내림차순 / asks 오름차순
  - best_bid/ask/mid/spread 정확도
  - 단일 거래소 입력 (None) 안전 처리
  - 빈 호가창 처리
  - Decimal 정확도 (소수점 둘째 자리)
  - 100건 머지 성능 P95 ≤ 5ms
- [ ] `Makefile` `test-composite-orderbook` 타겟
- [ ] 회귀 테스트 — backend 전체 `pytest backend/tests/` 0 fail
- [ ] gap-detector 매치율 ≥ 90%
- [ ] frontend 컴포넌트 명세는 design 문서 §UI 섹션에 시그니처+props 만 고정 (구현은 BAR-54b)

---

## 7. 의존성 / 위험

| 위험 | 트리거 | 대응 |
|------|--------|------|
| 동일 가격 다른 venue 잔량 합산 시 정확도 손실 | float 사용 | Decimal 강제 (FR-04) |
| 호가 단계 차이 (KRX 1원, NXT 0.01원 등) | 가격 정밀도 mismatch | merge 시 가격 그대로 보존 (소수점 자릿수 다른 호가는 별도 라인) |
| 한쪽만 입력될 때 무한 루프 / NPE | None / 빈 OrderBook | FR-07/FR-08 명시적 가드 |

---

## 8. 다음 단계

1. `/pdca design BAR-54` — CompositeOrderBookL2 모델 + 서비스 시그니처 + UI 명세 확정
2. `/pdca do BAR-54` — backend 구현 + 20+ tests
3. `/pdca analyze BAR-54` — gap-detector
4. `/pdca report BAR-54` — Phase 2 진척 (3/4)
5. 후속: BAR-54b (frontend 정식 머지, 운영 노드 환경) → BAR-55 (SOR v1)
