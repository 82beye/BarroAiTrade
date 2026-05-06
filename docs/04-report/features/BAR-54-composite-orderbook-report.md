# BAR-54 CompositeOrderBook — Completion Report

**Phase**: 2 (NXT 통합 + 통합 호가창 + 거래시간 인지)
**Ticket**: BAR-54 (54a backend) — KRX+NXT L2 호가 병합
**Status**: ✅ COMPLETED (backend)
**Date**: 2026-05-06

---

## 1. Outcomes

KRX 본장과 NXT 대체거래소의 L2 호가를 단일 가격축에 병합하는 **CompositeOrderBookService** 가 도입되었다. 동일 가격에서의 잔량 합산 + 거래소별 breakdown 보존으로 SOR(BAR-55) 의 라우팅 결정 입력이 마련되었다.

### 1.1 신규 추상화

| 구성 | 위치 | 역할 |
|------|------|------|
| `CompositeLevel` | `backend/models/market.py` | 단일 가격의 잔량 + breakdown |
| `CompositeOrderBookL2` | 동상 | bids/asks/venues + 4개 property + venue_breakdown |
| `CompositeOrderBookService` | `backend/core/gateway/composite_orderbook.py` | merge() 알고리즘 |

### 1.2 알고리즘 핵심

| 단계 | 동작 |
|------|------|
| 1 | 단일 입력(None) 가드 — 다른 한쪽만으로 변환 |
| 2 | 양측 입력 — `_accumulate()` 로 가격별 dict 누적 |
| 3 | breakdown 보존 — 동일 가격에 KRX/NXT 양쪽 있으면 둘 다 키로 유지 |
| 4 | 정렬 — bids 내림차순 / asks 오름차순 |
| 5 | venues frozenset — 입력에 포함된 거래소 |

### 1.3 Decimal 정확도 정책

- 가격 비교·산술 모두 `Decimal` (mid_price, spread)
- frozen=True — 외부 변조 차단
- 음수/0/교차 호가 등 비정상 입력은 **그대로 보존** (검증은 BAR-55 SOR 또는 호출자 책임)

---

## 2. Validation

### 2.1 Tests

```
make test-composite-orderbook
─────────────────────────────────────────────
22 passed in 0.08s
```

| 클래스 | 케이스 |
|--------|:------:|
| `TestMergeBasic` | 4 |
| `TestPriceAggregation` | 3 |
| `TestSorting` | 2 |
| `TestBest` | 3 |
| `TestMidSpread` | 2 |
| `TestVenueBreakdown` | 3 |
| `TestEdge` | 3 |
| `TestDecimal` | 1 |
| `TestPerformance` | 1 |
| **합계** | **22 PASSED** |

### 2.2 회귀

전체 `pytest backend/tests/` — **213 passed, 1 skipped, 0 failed** (191 → 213, +22).

### 2.3 Gap Analysis (PR #75 머지)

- 매치율 **100%** (10/10) — PASS
- iterator 트리거 불필요

상세: `docs/04-report/analyze/BAR-54-gap-analysis.md`

---

## 3. PR Trail

| Stage | PR | 상태 |
|-------|----|:----:|
| plan | #72 | ✅ MERGED |
| design | #73 | ✅ MERGED |
| do | #74 | ✅ MERGED (22 tests) |
| analyze | #75 | ✅ MERGED (100%) |
| report | (this) | 진행 중 |

---

## 4. Phase 2 Progress

| BAR | Title | Status |
|-----|-------|:------:|
| BAR-52 | Exchange/TradingSession enum + MarketSessionService | ✅ DONE |
| BAR-53 | NxtGateway 1차 (시세 read-only) | ✅ DONE |
| BAR-54 (54a) | CompositeOrderBookService backend | ✅ DONE |
| BAR-54b | OrderbookComposite frontend tsx + Storybook + Playwright | deferred (운영 노드 환경) |
| BAR-55 | SOR v1 (가격/잔량 라우팅) | NEXT |
| BAR-53.5 | 실 키움/KOSCOM NXT 어댑터 | deferred (운영 OpenAPI 키 발급 후) |

---

## 5. Lessons & Decisions

1. **BAR 분할 (54a/54b) 선언적 처리**: 본 worktree 환경에서 frontend 빌드·E2E 검증 불가 → design 단계에서 명세만 고정하고 backend 만 정식 do. plan/design 문서에 분리 정책 §0 명시.
2. **stateless 서비스**: CompositeOrderBookService 는 캐시 없이 매 호출 새로 머지. 상태(in-memory cache) 는 BAR-72 Redis 전환 시점에 어댑터 분리.
3. **검증 정책 분리**: 음수 잔량·교차 호가 등 비즈니스 위반은 본 서비스가 검증 X — SOR(BAR-55) 또는 호출자가 정책 결정. 머지 서비스는 데이터 무결성만 책임.
4. **Pydantic v2 frozen + Decimal**: 모든 시세 모델 일관성 — Tick(BAR-53), CompositeLevel(BAR-54). 자금흐름 정확도 (area:money) 정책 충족.

---

## 6. Next Action

`/pdca plan BAR-55` — SOR v1. 본 서비스의 best_bid·best_ask + venue_breakdown 으로 가격·잔량 비교 라우팅 결정. 100건 모의 주문 라우팅 100% 정확 = Phase 2 종료 게이트.
