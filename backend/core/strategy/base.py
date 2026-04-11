"""
Strategy 추상 인터페이스

모든 매매 전략은 이 인터페이스를 구현해야 한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from backend.models.market import OHLCV, MarketType
from backend.models.signal import EntrySignal


class Strategy(ABC):
    """매매 전략 추상 기반 클래스"""

    STRATEGY_ID: str = ""

    @abstractmethod
    def analyze(
        self,
        symbol: str,
        name: str,
        candles: List[OHLCV],
        market_type: MarketType,
    ) -> Optional[EntrySignal]:
        """캔들 데이터를 분석하여 진입 신호를 반환.

        Args:
            symbol: 종목 코드
            name: 종목명
            candles: OHLCV 캔들 목록 (시간순, 최신이 마지막)
            market_type: 마켓 타입 (stock | crypto)

        Returns:
            EntrySignal — 신호 있을 때
            None — 신호 없을 때
        """

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(strategy_id={self.STRATEGY_ID!r})"
