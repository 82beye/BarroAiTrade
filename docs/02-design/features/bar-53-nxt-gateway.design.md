# BAR-53 — NxtGateway 1차 (시세 read-only) Design

**Plan**: `docs/01-plan/features/bar-53-nxt-gateway.plan.md`
**선행**: BAR-52 (MarketSessionService) ✅

---

## §0. Day 0 스파이크 결과 (가정)

> 본 worktree 환경에서는 외부 NXT API 접근이 불가하므로 **추상 인터페이스 + Mock Primary** 로 1차 구현. 실제 키움/KOSCOM 어댑터는 운영 환경에서 BAR-53.5 (별도 후속) 로 분리.

| 결정 | 사유 |
|------|------|
| Primary = `MockNxtGateway` (테스트/dev) + `KiwoomNxtGateway` placeholder | 본 BAR 에서는 인터페이스·정책·테스트만 굳히고, 실 게이트웨이는 후속에서 OpenAPI 키 발급 후 |
| Fallback = `LegacyNxtGateway` placeholder (KOSCOM 또는 ai-trade legacy 어댑터 슬롯) | 인터페이스만 노출, 실제 통신 코드는 후속 |
| Decimal 강제 / Pydantic v2 | 자금흐름·시세 정확도 (area:money 정책 준수) |

운영 진입 전 BAR-53.5 에서 실제 백엔드 어댑터 추가 시 본 인터페이스를 그대로 구현하면 된다.

---

## §1. 시퀀스

```
Strategy / Scanner ──┐
                     │  on_tick / on_orderbook
NxtGatewayManager ◄──┴── Subscriber callbacks
        │
        ├── MarketSessionService (의존성)
        │       └── 가용 세션 외 → 자동 unsubscribe
        │
        ├── PrimaryNxtGateway   ── (실패 30s) ──┐
        │                                       ▼
        └── FallbackNxtGateway  ◄── 자동 전환

  Health Loop (5s 주기):
    ├─ msg_lag < 5min → OK
    ├─ 5min 초과 → reconnect()
    └─ reconnect 실패 3회 → Prometheus alert + status=DEGRADED
```

---

## §2. 인터페이스 (FR-01)

### 2.1 `INxtGateway` Protocol

```python
class INxtGateway(Protocol):
    name: str  # "kiwoom" | "koscom" | "mock"

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def is_connected(self) -> bool: ...
    async def health_check(self) -> HealthStatus: ...

    async def subscribe_ticker(self, symbols: list[str]) -> None: ...
    async def subscribe_orderbook(self, symbols: list[str]) -> None: ...
    async def subscribe_trade(self, symbols: list[str]) -> None: ...
    async def unsubscribe(self, symbols: list[str]) -> None: ...

    def on_tick(self, callback: Callable[[Tick], Awaitable[None]]) -> None: ...
    def on_orderbook(self, callback: Callable[[OrderBookL2], Awaitable[None]]) -> None: ...
    def on_trade(self, callback: Callable[[Trade], Awaitable[None]]) -> None: ...
```

### 2.2 `NxtGatewayManager` (FR-04, FR-05, FR-06)

```python
class NxtGatewayManager:
    def __init__(
        self,
        primary: INxtGateway,
        fallback: INxtGateway | None,
        session_service: MarketSessionService,
        primary_fail_threshold_seconds: float = 30.0,
        health_interval_seconds: float = 5.0,
        max_reconnect_attempts: int = 3,
    ) -> None: ...

    @property
    def active(self) -> INxtGateway: ...   # 현재 사용 중
    @property
    def status(self) -> GatewayStatus: ... # OK | DEGRADED | DOWN

    async def start(self) -> None: ...     # connect + 헬스 루프
    async def stop(self) -> None: ...
    async def subscribe_ticker(self, symbols): ...   # active 로 위임 + 세션 가드
    # … (subscribe_orderbook / subscribe_trade / unsubscribe / on_*)

    async def _health_loop(self) -> None: ...
    async def _failover(self) -> None: ...           # primary→fallback 전환
```

세션 가드: `subscribe_*` 호출 시 `session_service.get_session()` 으로 검사, NXT 가용 세션(NXT_PRE / KRX_PRE / REGULAR / KRX_AFTER / NXT_AFTER) 이 아니면 `pending_subscriptions` 에 보류 → 진입 시 자동 적용.

---

## §3. 데이터 모델 (FR-02)

### 3.1 `backend/models/market.py` 확장

```python
class Tick(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    venue: Exchange
    ts: datetime
    last_price: Decimal
    last_volume: int

class Quote(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    venue: Exchange
    ts: datetime
    bid: Decimal
    ask: Decimal
    bid_qty: int
    ask_qty: int

class OrderBookL2(BaseModel):
    """기존 OrderBook 의 L2 확장 — 호가 단계별."""
    model_config = ConfigDict(frozen=True)
    symbol: str
    venue: Exchange
    ts: datetime
    bids: list[tuple[Decimal, int]]   # (가격, 잔량) 내림차순
    asks: list[tuple[Decimal, int]]   # (가격, 잔량) 오름차순

class Trade(BaseModel):
    model_config = ConfigDict(frozen=True)
    symbol: str
    venue: Exchange
    ts: datetime
    price: Decimal
    qty: int
    side: Literal["buy", "sell"]

class HealthStatus(BaseModel):
    is_healthy: bool
    last_msg_at: datetime | None
    lag_seconds: float | None
    error: str | None = None

class GatewayStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"
```

### 3.2 NXT 가용 세션 셋

```python
NXT_AVAILABLE_SESSIONS: frozenset[TradingSession] = frozenset({
    TradingSession.NXT_PRE,
    TradingSession.KRX_PRE,
    TradingSession.REGULAR,
    TradingSession.KRX_AFTER,
    TradingSession.NXT_AFTER,
})
```

(MarketSessionService.available_exchanges() 를 호출해 동적으로 산출 가능 — 둘 다 노출)

---

## §4. Failover / Health (FR-04, FR-05)

| 이벤트 | 동작 |
|--------|------|
| primary `is_connected()=False` 30 초 누적 | `_failover()` → fallback 으로 active 전환, `nxt_gateway_failovers_total` ++ |
| 5 분 이상 메시지 없음 | `active.connect()` 재시도 (exponential backoff) |
| 재연결 실패 3회 | `status = DEGRADED`, alert 발송, fallback 도 시도 후 둘 다 실패 시 `status = DOWN` |
| primary 가 30 초 연속 OK 회복 | (옵션, 본 BAR 에서는 자동 복귀하지 않음 — 운영 안정성 우선) |

Backoff: 1, 2, 4, 8, 16, 32 초 (max 5회)

---

## §5. Prometheus 메트릭 (NFR-04)

| 메트릭 | 라벨 |
|--------|------|
| `nxt_gateway_msg_received_total` | `gateway`, `kind={ticker,orderbook,trade}` |
| `nxt_gateway_reconnects_total` | `gateway`, `result={success,fail}` |
| `nxt_gateway_failovers_total` | `from_gateway`, `to_gateway` |
| `nxt_gateway_lag_seconds` (Gauge) | `gateway` |
| `nxt_gateway_status` (Gauge, 0/1/2) | – |

---

## §6. 테스트 시나리오 (24+, NFR-05)

| # | Class | Case |
|---|-------|------|
| 1-3 | `TestMockGateway` | connect/disconnect/is_connected |
| 4-6 | `TestMockGateway` | subscribe ticker/orderbook/trade — callback fire |
| 7 | `TestMockGateway` | unsubscribe → callback 미발생 |
| 8 | `TestModelDecimal` | Tick.last_price float 입력 → Decimal 강제 |
| 9 | `TestModelDecimal` | OrderBookL2 bids/asks Decimal 보존 |
| 10 | `TestModelImmutable` | Tick.frozen → 변조 차단 |
| 11-13 | `TestManagerSubscribe` | ticker/orderbook/trade — manager → active 위임 |
| 14 | `TestManagerSessionGate` | TradingSession.CLOSED → subscribe pending |
| 15 | `TestManagerSessionGate` | TradingSession.INTERLUDE → subscribe pending |
| 16 | `TestManagerSessionGate` | TradingSession.REGULAR → subscribe 즉시 적용 |
| 17 | `TestManagerSessionGate` | NXT_AFTER → subscribe 즉시 적용 |
| 18-19 | `TestManagerHealth` | 5분 무수신 → 재연결 시도 |
| 20 | `TestManagerHealth` | 재연결 3회 실패 → DEGRADED |
| 21-22 | `TestManagerFailover` | primary 30초 down → fallback 전환 |
| 23 | `TestManagerFailover` | fallback 도 실패 → DOWN |
| 24 | `TestManagerFailover` | fallback=None 일 때 primary 실패 → DEGRADED 유지 (DOWN 직행 금지) |
| 25 | `TestManagerLifecycle` | start/stop 호출 시 idempotent |

---

## §7. 디렉터리 / 파일

| 경로 | 역할 |
|------|------|
| `backend/core/gateway/__init__.py` | re-export |
| `backend/core/gateway/nxt.py` (신규) | INxtGateway, NxtGatewayManager, MockNxtGateway, NXT_AVAILABLE_SESSIONS |
| `backend/models/market.py` (확장) | Tick, Quote, OrderBookL2, Trade, HealthStatus, GatewayStatus |
| `backend/tests/gateway/__init__.py` (신규) | – |
| `backend/tests/gateway/test_nxt.py` (신규) | 25 케이스 |
| `Makefile` | `test-nxt-gateway` 타겟 |

---

## §8. 후속 BAR

- BAR-53.5 (선택) : 실제 키움 OpenAPI / KOSCOM 어댑터 — `INxtGateway` 만 구현하면 본 매니저에 즉시 plug-in
- BAR-54 : `OrderBookL2` stream 을 KRX 호가와 병합한 `CompositeOrderBook` + UI
- BAR-55 : SOR v1 — 본 Gateway 의 시세를 가격 비교 라우팅 입력으로 사용
