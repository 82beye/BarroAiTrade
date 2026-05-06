# BAR-61 — 일정 캘린더 design

## §1 모델 (`backend/models/event.py`)

```python
class EventType(str, Enum):
    EARNINGS = "earnings"
    IPO = "ipo"
    DIVIDEND = "dividend"
    POLICY = "policy"
    OTHER = "other"

class MarketEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    event_type: EventType
    symbol: Optional[str] = None
    event_date: date
    title: str = Field(min_length=1, max_length=512)
    source: str = "manual"
    metadata: dict[str, Any] = Field(default_factory=dict)
```

## §2 EventCollector Protocol + Stub

```python
@runtime_checkable
class EventCollector(Protocol):
    async def fetch(self, start: date, end: date) -> list[MarketEvent]: ...

class StubEventCollector:
    """fixture 기반. 운영 시 IR/인포맥스/FnGuide 어댑터로 교체 (BAR-61b)."""
    async def fetch(self, start, end): ...
```

## §3 EventRepository (`backend/db/repositories/event_repo.py`)

`text() + named param + dialect 분기`:
- insert_event / find_by_date_range / find_by_symbol
- alembic 0005 — market_events 테이블 (UNIQUE(symbol, event_date, event_type) + idx)

## §4 EventLinker

```python
class EventLinker:
    """event → theme → 관련 종목 자동 매핑 (theme_repo 활용)."""

    def __init__(self, theme_repo): ...

    async def link_event_to_stocks(self, event: MarketEvent) -> list[str]:
        """symbol 직접 지정이면 [symbol], 아니면 theme keyword 매칭으로 종목 탐색."""
```

## §5 alembic 0005

```python
op.create_table(
    "market_events",
    sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("symbol", sa.Text),
    sa.Column("event_date", ts_type, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("metadata", json_type, nullable=False, server_default=...),
    sa.UniqueConstraint("symbol", "event_date", "event_type",
                        name="uq_market_events_symbol_date_type"),
)
op.create_index("idx_market_events_event_date", "market_events", ["event_date"])
op.create_index("idx_market_events_symbol", "market_events", ["symbol"])
```

## §6 15 테스트
- MarketEvent frozen + EventType (3)
- StubEventCollector (2)
- EventRepository insert/find/UNIQUE (4)
- EventLinker symbol 직접 (2)
- EventLinker theme 매칭 (2)
- alembic 0005 (2)
