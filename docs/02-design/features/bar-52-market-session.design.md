---
tags: [design, feature/bar-52, status/in_progress, phase/2, area/data]
template: design
version: 1.0
---

# BAR-52 Exchange/TradingSession + MarketSessionService Design

> **관련 문서**: [[../../01-plan/features/bar-52-market-session.plan|Plan]] | [[../../01-plan/MASTER-EXECUTION-PLAN-v2|Master Plan v2]]

> **Date**: 2026-05-06 / **Status**: Draft

---

## 1. Implementation Spec

### 1.1 Enum 추가 (`backend/models/market.py`)

```python
class Exchange(str, Enum):
    """거래소 — KRX 본장, NXT 대체거래소, COMPOSITE 통합 뷰."""
    KRX = "krx"
    NXT = "nxt"
    COMPOSITE = "composite"


class TradingSession(str, Enum):
    """거래 세션 (한국 시간 기준)."""
    CLOSED = "closed"
    NXT_PRE = "nxt_pre"                    # 08:00–08:50 (NXT 만)
    KRX_PRE = "krx_pre"                    # 08:30–09:00 (KRX+NXT)
    REGULAR = "regular"                    # 09:00–15:20 (KRX+NXT)
    KRX_CLOSING_AUCTION = "krx_closing_auction"  # 15:20–15:30 (KRX 단일가)
    INTERLUDE = "interlude"                # 15:30–15:40 (양 거래소 휴식)
    KRX_AFTER = "krx_after"                # 15:40–18:00 (KRX+NXT)
    NXT_AFTER = "nxt_after"                # 18:00–20:00 (NXT 단독, 블루오션)
```

### 1.2 MarketSessionService (`backend/core/market_session/service.py`)

```python
from __future__ import annotations

from datetime import date, datetime, time as dtime, timezone, timedelta
from typing import List

from backend.models.market import Exchange, TradingSession


KST = timezone(timedelta(hours=9))


class MarketSessionService:
    """시각·날짜·휴장일 보고 현재 거래 세션 판단."""

    def __init__(self, holidays: set[date] | None = None) -> None:
        self._holidays: set[date] = set(holidays or set())

    def add_holiday(self, d: date) -> None:
        self._holidays.add(d)

    def is_holiday(self, d: date) -> bool:
        return d in self._holidays

    def get_session(self, now: datetime | None = None) -> TradingSession:
        """현재 또는 주어진 시각의 세션 반환 (KST 기준)."""
        if now is None:
            now = datetime.now(KST)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=KST)
        else:
            now = now.astimezone(KST)

        # 주말·휴장일
        if now.weekday() >= 5 or self.is_holiday(now.date()):
            return TradingSession.CLOSED

        t = now.time()
        if t < dtime(8, 0):
            return TradingSession.CLOSED
        if t < dtime(8, 30):
            return TradingSession.NXT_PRE
        if t < dtime(9, 0):
            return TradingSession.KRX_PRE  # 우선 정책 (Plan §5.2)
        if t < dtime(15, 20):
            return TradingSession.REGULAR
        if t < dtime(15, 30):
            return TradingSession.KRX_CLOSING_AUCTION
        if t < dtime(15, 40):
            return TradingSession.INTERLUDE
        if t < dtime(18, 0):
            return TradingSession.KRX_AFTER  # 우선 정책 (KRX_AFTER + NXT_AFTER 겹침)
        if t < dtime(20, 0):
            return TradingSession.NXT_AFTER
        return TradingSession.CLOSED

    def available_exchanges(self, session: TradingSession) -> List[Exchange]:
        """세션 → 가용 거래소 목록."""
        return {
            TradingSession.CLOSED: [],
            TradingSession.NXT_PRE: [Exchange.NXT],
            TradingSession.KRX_PRE: [Exchange.KRX, Exchange.NXT],
            TradingSession.REGULAR: [Exchange.KRX, Exchange.NXT],
            TradingSession.KRX_CLOSING_AUCTION: [Exchange.KRX],
            TradingSession.INTERLUDE: [],
            TradingSession.KRX_AFTER: [Exchange.KRX, Exchange.NXT],
            TradingSession.NXT_AFTER: [Exchange.NXT],
        }[session]

    def available_orders(self, session: TradingSession) -> dict[str, bool]:
        """세션 → 주문 유형 가용성. {market, limit, after_hours}."""
        if session in (TradingSession.CLOSED, TradingSession.INTERLUDE):
            return {"market": False, "limit": False, "after_hours": False}
        if session == TradingSession.KRX_CLOSING_AUCTION:
            return {"market": False, "limit": True, "after_hours": False}  # 단일가만
        if session in (TradingSession.KRX_AFTER, TradingSession.NXT_AFTER):
            return {"market": False, "limit": True, "after_hours": True}
        # NXT_PRE, KRX_PRE, REGULAR
        return {"market": True, "limit": True, "after_hours": False}
```

### 1.3 AnalysisContext.trading_session 정식 type

```python
# backend/models/strategy.py
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.models.market import TradingSession

class AnalysisContext(BaseModel):
    ...
    trading_session: Optional["TradingSession"] = None  # BAR-52 정식 type
```

→ forward ref 유지 (Pydantic v2 가 처리). placeholder 주석 제거.

---

## 2. Test Cases (24+, `tests/market_session/test_service.py`)

```python
import pytest
from datetime import date, datetime, time, timezone, timedelta

from backend.core.market_session.service import MarketSessionService, KST
from backend.models.market import Exchange, TradingSession


@pytest.fixture
def svc() -> MarketSessionService:
    return MarketSessionService()


def _kst(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=KST)


# 평일 = 2026-05-06 (수요일)
WEEKDAY = (2026, 5, 6)
# 토 = 2026-05-09
SAT = (2026, 5, 9)
# 일 = 2026-05-10
SUN = (2026, 5, 10)


class TestGetSession:
    """24+ 시간대 매트릭스."""

    def test_closed_before_8am(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 7, 30)) == TradingSession.CLOSED

    def test_nxt_pre_8_00(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 8, 0)) == TradingSession.NXT_PRE

    def test_nxt_pre_8_29(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 8, 29)) == TradingSession.NXT_PRE

    def test_krx_pre_8_30(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 8, 30)) == TradingSession.KRX_PRE

    def test_krx_pre_8_50(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 8, 50)) == TradingSession.KRX_PRE

    def test_regular_9_00(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 9, 0)) == TradingSession.REGULAR

    def test_regular_12_00(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 12, 0)) == TradingSession.REGULAR

    def test_regular_15_19(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 15, 19)) == TradingSession.REGULAR

    def test_closing_auction_15_20(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 15, 20)) == TradingSession.KRX_CLOSING_AUCTION

    def test_closing_auction_15_29(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 15, 29)) == TradingSession.KRX_CLOSING_AUCTION

    def test_interlude_15_30(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 15, 30)) == TradingSession.INTERLUDE

    def test_interlude_15_39(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 15, 39)) == TradingSession.INTERLUDE

    def test_krx_after_15_40(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 15, 40)) == TradingSession.KRX_AFTER

    def test_krx_after_17_00(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 17, 0)) == TradingSession.KRX_AFTER

    def test_krx_after_17_59(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 17, 59)) == TradingSession.KRX_AFTER

    def test_nxt_after_18_00(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 18, 0)) == TradingSession.NXT_AFTER

    def test_nxt_after_19_30(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 19, 30)) == TradingSession.NXT_AFTER

    def test_closed_20_00(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 20, 0)) == TradingSession.CLOSED

    def test_closed_22_00(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 22, 0)) == TradingSession.CLOSED

    def test_saturday_closed(self, svc):
        assert svc.get_session(_kst(*SAT, 12, 0)) == TradingSession.CLOSED

    def test_sunday_closed(self, svc):
        assert svc.get_session(_kst(*SUN, 12, 0)) == TradingSession.CLOSED

    def test_holiday_closed(self, svc):
        svc.add_holiday(date(2026, 5, 6))
        assert svc.get_session(_kst(*WEEKDAY, 12, 0)) == TradingSession.CLOSED

    def test_naive_datetime_handled(self, svc):
        """tzinfo 없는 datetime → KST 가정."""
        naive = datetime(2026, 5, 6, 10, 0)
        assert svc.get_session(naive) == TradingSession.REGULAR

    def test_default_now_works(self, svc):
        """now=None → datetime.now(KST) 사용."""
        result = svc.get_session()
        assert isinstance(result, TradingSession)


class TestAvailableExchanges:
    @pytest.mark.parametrize("session,expected", [
        (TradingSession.CLOSED, []),
        (TradingSession.NXT_PRE, [Exchange.NXT]),
        (TradingSession.KRX_PRE, [Exchange.KRX, Exchange.NXT]),
        (TradingSession.REGULAR, [Exchange.KRX, Exchange.NXT]),
        (TradingSession.KRX_CLOSING_AUCTION, [Exchange.KRX]),
        (TradingSession.INTERLUDE, []),
        (TradingSession.KRX_AFTER, [Exchange.KRX, Exchange.NXT]),
        (TradingSession.NXT_AFTER, [Exchange.NXT]),
    ])
    def test_available_exchanges(self, svc, session, expected):
        assert svc.available_exchanges(session) == expected


class TestAvailableOrders:
    def test_closed_blocks_all(self, svc):
        orders = svc.available_orders(TradingSession.CLOSED)
        assert orders == {"market": False, "limit": False, "after_hours": False}

    def test_regular_market_and_limit(self, svc):
        orders = svc.available_orders(TradingSession.REGULAR)
        assert orders["market"] is True
        assert orders["limit"] is True

    def test_after_only_limit(self, svc):
        orders = svc.available_orders(TradingSession.KRX_AFTER)
        assert orders["market"] is False
        assert orders["limit"] is True
        assert orders["after_hours"] is True


class TestHoliday:
    def test_add_and_check(self, svc):
        d = date(2026, 12, 25)
        assert svc.is_holiday(d) is False
        svc.add_holiday(d)
        assert svc.is_holiday(d) is True


class TestAnalysisContextIntegration:
    """BAR-45 placeholder forward ref 해소 검증."""

    def test_analysis_context_with_session(self, sample_candles):
        from backend.models.strategy import AnalysisContext
        from backend.models.market import MarketType

        ctx = AnalysisContext(
            symbol="005930",
            candles=sample_candles,
            market_type=MarketType.STOCK,
            trading_session=TradingSession.REGULAR,
        )
        assert ctx.trading_session == TradingSession.REGULAR
```

---

## 3. Verification (V1~V6)

| # | 시나리오 |
|---|---|
| V1 | `make test-market-session` 24+ 통과 |
| V2 | cov ≥ 80% |
| V3 | BAR-44 베이스라인 (F존 6 / BlueLine 12) |
| V4 | BAR-40~50 회귀 무영향 |
| V5 | timezone naive 처리 |
| V6 | AnalysisContext.trading_session 정식 type |

---

## 4. Implementation Checklist (D1~D6)

1. D1 — market.py 확장 (Exchange, TradingSession enum)
2. D2 — market_session/service.py 신규
3. D3 — strategy.py trading_session 정식 type
4. D4 — tests/market_session/test_service.py 24+
5. D5 — Makefile test-market-session 타겟
6. D6 — V1~V6 + PR

---

## 5. Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 design — enum + service + 24+ 시나리오 |
