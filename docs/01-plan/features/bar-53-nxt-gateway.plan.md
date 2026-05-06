# BAR-53 — NxtGateway 1차 (시세 read-only)

**Phase**: 2 (NXT 통합 + 통합 호가창 + 거래시간 인지)
**선행**: BAR-52 (TradingSession enum + MarketSessionService) ✅
**후행**: BAR-54 (CompositeOrderBook + UI), BAR-55 (SOR v1)

---

## 1. 목표 (Why)

NXT 거래소의 시세 데이터(ticker, orderbook, trade) 를 안정적으로 수신하기 위한 **read-only** Gateway 1차 인스턴스. 주문 라우팅(BAR-55)·통합 호가창(BAR-54) 의 공통 의존성으로 사용된다.

**1차 스코프 = 시세만**. 주문 송수신은 BAR-55 (SOR v1) 에서 다룬다.

---

## 2. 1일 스파이크 (Day 0)

본격 구현 전 1일 검증:

| 항목 | 평가 기준 | 결정 |
|------|-----------|------|
| 키움 OpenAPI NXT 채널 시세 제공 여부 | 공식 문서/지원팀 확인 | ① 가능 → 키움 single-source<br>② 불가 → KOSCOM CHECK 등 벤더 평가 |
| KOSCOM CHECK 비용 / 약관 | 월 비용·통신 사양 확인 | 비용 ≤ 월 50만원이면 fallback 채택 |
| WebSocket vs REST polling | latency P95 ≤ 500ms 목표 | WS 우선, 없으면 1초 polling |
| ai-trade(`backend/legacy_scalping/`) 내 NXT 코드 재사용성 | grep 으로 keyword 검색 | 발견 시 어댑터 우선 |

스파이크 결과는 `docs/02-design/features/bar-53-nxt-gateway.design.md` §0 에 명시.

---

## 3. 기능 요구사항 (FR)

| ID | 요구 |
|----|------|
| FR-01 | `NxtGateway` 추상 인터페이스 (Protocol or ABC) — `subscribe_ticker`, `subscribe_orderbook`, `subscribe_trade`, `unsubscribe`, `health_check`, `is_connected` |
| FR-02 | 시세 데이터 모델: `Tick`, `Quote`, `OrderBookL2`, `Trade` (Pydantic v2, 모든 가격은 `Decimal`) |
| FR-03 | Primary 구현체: `KiwoomNxtGateway` (키움 OpenAPI NXT 채널) — 또는 스파이크 결과에 따라 `KoscomCheckNxtGateway` |
| FR-04 | Fallback 정책: primary 실패(`is_connected=False` ≥ 30초) 시 secondary 자동 전환 |
| FR-05 | `health_check()` — 5분 무수신 시 자동 재연결, 재연결 실패 3회 시 Prometheus alert |
| FR-06 | TradingSession 인지: NXT 가용 세션(NXT_PRE, KRX_PRE, REGULAR, KRX_AFTER, NXT_AFTER) 외 시각에는 자동 unsubscribe |
| FR-07 | Subscriber 등록/해제 패턴: `gateway.on_tick(callback)` — Redis Streams publish 와 직결 가능 |
| FR-08 | `MarketSessionService` 의존성 주입 (현재 세션 문의) |

---

## 4. 비기능 요구사항 (NFR)

| ID | 요구 |
|----|------|
| NFR-01 | latency P95 ≤ 500ms (수신 → callback) |
| NFR-02 | 7일 무중단 수신 (재연결 자동, 24h 운용 누락률 ≤ 0.1%) |
| NFR-03 | 모든 외부 호출 timeout 5초, retry exponential backoff (1s → 32s, max 5회) |
| NFR-04 | Prometheus 메트릭: `nxt_gateway_msg_received_total`, `nxt_gateway_reconnects_total`, `nxt_gateway_lag_seconds` |
| NFR-05 | 단위 테스트 커버리지 ≥ 70%, 통합 테스트는 mock 기반 (실 API 호출 없이) |

---

## 5. 비고려 (Out of Scope)

- ❌ 주문 송수신 (BAR-55 SOR v1)
- ❌ 통합 호가창 UI (BAR-54)
- ❌ NXT 야간 자동매매 로직 (Phase 4 BAR-63 ExitPlan 통합 후)
- ❌ 멀티 사용자 격리 (Phase 6 BAR-71)

---

## 6. DoD (Definition of Done)

- [ ] `backend/core/gateway/nxt.py` 신규 (인터페이스 + Primary + Fallback)
- [ ] `backend/models/market.py` 확장: `Tick`, `Quote`, `Trade` 추가 (OrderBookL2 는 기존 OrderBook 확장)
- [ ] `backend/tests/gateway/test_nxt.py` 신규 — 24+ mock 시나리오:
  - 정상 수신 (ticker/orderbook/trade)
  - primary 실패 → secondary 전환
  - 재연결 (1·2·3회 시도 + 실패 alert)
  - TradingSession 외 시각 자동 unsubscribe
  - timeout / retry exponential backoff
  - Decimal 정확도 보존 (가격 → Pydantic 직렬화)
- [ ] `Makefile` `test-nxt-gateway` 타겟
- [ ] 1일 스파이크 결과 design 문서 §0 명시
- [ ] (운영) 모의 환경 7일 무중단 수신 결과 → BAR-54 진입 게이트
- [ ] gap-detector 매치율 ≥ 90%

---

## 7. 의존성 / 위험

| 위험 | 트리거 | 대응 |
|------|--------|------|
| 키움 NXT 채널 미제공 | 스파이크 1일차 | KOSCOM CHECK 벤더 평가, 일정 +1~2주 |
| 비용 한계 (KOSCOM 월 50만원 초과) | 스파이크 결과 | NXT 직접 API 정식 가입, 또는 Phase 2 일정 재추정 |
| 키움 OpenAPI 세션 불안정 | health_check 5분 무수신 빈발 | 세션 재발급 자동화 + 알림 |
| Decimal/float 변환 누수 | 단위 테스트에서 가격 mismatch | Pydantic v2 모델로 강제, 외부 응답은 모두 `Decimal()` 변환 |

---

## 8. 다음 단계

1. **Day 0**: 1일 스파이크 (키움 NXT vs KOSCOM CHECK)
2. **Day 1**: `/pdca design BAR-53` — 인터페이스·시퀀스 다이어그램 확정
3. **Day 2-3**: `/pdca do BAR-53` — Primary 구현 + 24+ 테스트
4. **Day 4**: `/pdca analyze BAR-53` — gap-detector
5. **Day 5**: `/pdca report BAR-53` — 7일 무중단 수신 결과 첨부

후속: BAR-54 (CompositeOrderBook + UI) — 본 Gateway 의 OrderBookL2 stream 을 KRX 데이터와 병합.
