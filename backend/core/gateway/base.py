"""
MarketGateway — 멀티마켓 추상 인터페이스

모든 시장 연동(키움, Binance, Upbit 등)은 이 인터페이스를 구현한다.
전략/실행 레이어는 구체적인 시장을 알지 못한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from backend.models.market import OHLCV, Ticker, MarketType, OrderBook
from backend.models.position import Order, OrderResult, Balance


class MarketGateway(ABC):
    """모든 시장 연동의 공통 인터페이스"""

    market_type: MarketType

    # ── 인증 ───────────────────────────────────────────────────────────────────

    @abstractmethod
    async def authenticate(self) -> None:
        """인증/토큰 갱신"""

    # ── 시장 데이터 ────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> List[OHLCV]:
        """OHLCV 데이터 조회

        Args:
            symbol: 종목코드 (주식: '005930', 크립토: 'BTC/USDT')
            timeframe: '1m', '5m', '15m', '1h', '1d'
            limit: 캔들 수
        """

    @abstractmethod
    async def get_ticker(self, symbol: str) -> Ticker:
        """현재가 조회"""

    @abstractmethod
    async def get_order_book(self, symbol: str) -> OrderBook:
        """호가 조회"""

    # ── 계좌 ───────────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_balance(self) -> Balance:
        """잔고 조회"""

    # ── 주문 ───────────────────────────────────────────────────────────────────

    @abstractmethod
    async def place_order(self, order: Order) -> OrderResult:
        """주문 실행"""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """주문 취소"""

    @abstractmethod
    async def get_order_status(self, order_id: str) -> OrderResult:
        """주문 상태 조회"""

    # ── 유니버스 ────────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_universe(self) -> List[str]:
        """전종목 리스트 (주식: KOSPI+KOSDAQ, 크립토: 지정 페어)"""

    @abstractmethod
    async def get_prices(self, symbols: List[str]) -> Dict[str, float]:
        """복수 종목 현재가 일괄 조회 — {symbol: price}"""

    # ── 시장 상태 ──────────────────────────────────────────────────────────────

    @abstractmethod
    def is_market_open(self) -> bool:
        """현재 거래 시간 여부"""

    @abstractmethod
    async def get_market_condition(self) -> dict:
        """시장 상태 조회 — {'is_open': bool, 'condition': str, 'updated_at': str}"""

    @abstractmethod
    async def health_check(self) -> bool:
        """API 연결 상태 확인"""
