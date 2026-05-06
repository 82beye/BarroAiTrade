"""
BAR-52 MarketSessionService — 시각·날짜·휴장일 보고 거래 세션 판단.

Reference:
- Plan: docs/01-plan/features/bar-52-market-session.plan.md
- Design: docs/02-design/features/bar-52-market-session.design.md §1.2
- 시간표 (KST):
    08:00 ─ NXT_PRE ─ 08:30 ─ KRX_PRE ─ 09:00 ─ REGULAR ─
    15:20 ─ KRX_CLOSING_AUCTION ─ 15:30 ─ INTERLUDE ─ 15:40 ─
    KRX_AFTER ─ 18:00 ─ NXT_AFTER ─ 20:00 ─ CLOSED
"""
from __future__ import annotations

from datetime import date as date_cls, datetime, time as dtime, timedelta, timezone
from typing import List, Optional

from backend.models.market import Exchange, TradingSession


# 한국 표준시 (UTC+9)
KST = timezone(timedelta(hours=9))


# 세션 → 가용 거래소 매트릭스 (Plan §5.1, §5.2)
_AVAILABLE_EXCHANGES: dict[TradingSession, list[Exchange]] = {
    TradingSession.CLOSED: [],
    TradingSession.NXT_PRE: [Exchange.NXT],
    TradingSession.KRX_PRE: [Exchange.KRX, Exchange.NXT],
    TradingSession.REGULAR: [Exchange.KRX, Exchange.NXT],
    TradingSession.KRX_CLOSING_AUCTION: [Exchange.KRX],
    TradingSession.INTERLUDE: [],
    TradingSession.KRX_AFTER: [Exchange.KRX, Exchange.NXT],
    TradingSession.NXT_AFTER: [Exchange.NXT],
}


class MarketSessionService:
    """시각·날짜·휴장일 보고 현재 거래 세션 판단.

    사용:
        svc = MarketSessionService()
        svc.add_holiday(date(2026, 12, 25))  # 휴장일 등록
        session = svc.get_session()           # 현재 KST 시각의 세션
    """

    def __init__(self, holidays: Optional[set[date_cls]] = None) -> None:
        self._holidays: set[date_cls] = set(holidays or set())

    # === 휴장일 관리 ===

    def add_holiday(self, d: date_cls) -> None:
        """휴장일 등록."""
        self._holidays.add(d)

    def remove_holiday(self, d: date_cls) -> None:
        self._holidays.discard(d)

    def is_holiday(self, d: date_cls) -> bool:
        return d in self._holidays

    @property
    def holidays(self) -> set[date_cls]:
        return frozenset(self._holidays)  # type: ignore[return-value]

    # === 세션 판단 ===

    def get_session(self, now: Optional[datetime] = None) -> TradingSession:
        """현재 또는 주어진 시각의 거래 세션 반환 (KST 기준).

        Args:
            now: 시각 (None 이면 현재). naive datetime 은 KST 가정.

        Returns:
            TradingSession enum
        """
        if now is None:
            now = datetime.now(KST)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=KST)
        else:
            now = now.astimezone(KST)

        # 주말·휴장일 → CLOSED
        if now.weekday() >= 5 or self.is_holiday(now.date()):
            return TradingSession.CLOSED

        t = now.time()
        # 시각 분기 (Plan §5.2 우선순위 정책 적용)
        if t < dtime(8, 0):
            return TradingSession.CLOSED
        if t < dtime(8, 30):
            return TradingSession.NXT_PRE
        if t < dtime(9, 0):
            # 08:30~09:00: NXT_PRE + KRX_PRE 겹침 → KRX_PRE 우선
            return TradingSession.KRX_PRE
        if t < dtime(15, 20):
            return TradingSession.REGULAR
        if t < dtime(15, 30):
            return TradingSession.KRX_CLOSING_AUCTION
        if t < dtime(15, 40):
            return TradingSession.INTERLUDE
        if t < dtime(18, 0):
            # 15:40~18:00: KRX_AFTER + NXT_AFTER 겹침 → KRX_AFTER 우선
            return TradingSession.KRX_AFTER
        if t < dtime(20, 0):
            return TradingSession.NXT_AFTER
        return TradingSession.CLOSED

    # === 세션별 가용성 ===

    def available_exchanges(self, session: TradingSession) -> List[Exchange]:
        """세션 → 가용 거래소 목록."""
        return list(_AVAILABLE_EXCHANGES[session])

    def available_orders(self, session: TradingSession) -> dict[str, bool]:
        """세션 → 주문 유형 가용성 dict {market, limit, after_hours}."""
        if session in (TradingSession.CLOSED, TradingSession.INTERLUDE):
            return {"market": False, "limit": False, "after_hours": False}
        if session == TradingSession.KRX_CLOSING_AUCTION:
            # 단일가 (limit only)
            return {"market": False, "limit": True, "after_hours": False}
        if session in (TradingSession.KRX_AFTER, TradingSession.NXT_AFTER):
            # 시간외 — 지정가만, market 불가
            return {"market": False, "limit": True, "after_hours": True}
        # NXT_PRE / KRX_PRE / REGULAR
        return {"market": True, "limit": True, "after_hours": False}


__all__ = ["KST", "MarketSessionService"]
