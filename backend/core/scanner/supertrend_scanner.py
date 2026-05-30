"""Supertrend 전용 스캐너 — 5분봉 추세전환 신호 (2026-05-31 신규).

종목 유니버스(스캔 대상)는 **별도 모듈**(최근 7일 거래대금 선별)이 담당하며, 본
스캐너는 외부에서 받은 종목 리스트에 대해서만 5분봉 Supertrend 신호를 산출한다.

설계 의도:
  - 기존 `SignalScanner` 는 1분봉 intraday 단타 전용(sf/f/gold) → timeframe 충돌 회피를
    위해 5분봉 Supertrend 는 **독립 스캐너**로 분리 (DailyScreener 패턴 준용).
  - signal-only: EntrySignal 만 산출, 실거래 송출 없음 (상위 PM/리스크 게이트가 결정).
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from backend.core.gateway.base import MarketGateway
from backend.core.strategy.supertrend import SupertrendParams, SupertrendStrategy
from backend.models.signal import EntrySignal
from backend.models.strategy import AnalysisContext

logger = logging.getLogger(__name__)


class SupertrendScanner:
    """5분봉 Supertrend 추세전환 스캐너.

    사용법:
        scanner = SupertrendScanner(gateway)
        # symbols 는 외부(7일 거래대금 선별)에서 전달
        signals = await scanner.scan(["005930", "000660", ...])

    Args:
        gateway: 시세 게이트웨이 (5분봉 get_ohlcv).
        params: SupertrendParams (None 시 Pine 기본값 ATR10·×3.0·hl2).
        timeframe: 캔들 주기 (기본 "5m").
        candle_limit: fetch 봉 수 (ATR/추세 안정화 위해 충분히, 기본 200).
    """

    def __init__(
        self,
        gateway: MarketGateway,
        params: Optional[SupertrendParams] = None,
        timeframe: str = "5m",
        candle_limit: int = 200,
    ) -> None:
        self.gateway = gateway
        self.strategy = SupertrendStrategy(params)
        self.timeframe = timeframe
        self.candle_limit = candle_limit

    async def scan(self, symbols: List[str]) -> List[EntrySignal]:
        """종목 리스트 스캔 → 진입 신호를 점수 내림차순으로 반환."""
        tasks = [self._analyze_symbol(sym) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        signals: List[EntrySignal] = []
        for sym, result in zip(symbols, results):
            if isinstance(result, Exception):
                logger.warning("%s Supertrend 분석 오류: %s", sym, result)
            elif result is not None:
                signals.append(result)

        signals.sort(key=lambda s: s.score, reverse=True)
        logger.info(
            "Supertrend 스캔 완료: %d/%d 종목에서 추세전환 신호", len(signals), len(symbols),
        )
        return signals

    async def _analyze_symbol(self, symbol: str) -> Optional[EntrySignal]:
        ticker = await self.gateway.get_ticker(symbol)
        candles = await self.gateway.get_ohlcv(
            symbol, self.timeframe, self.candle_limit,
        )
        if not candles:
            return None

        ctx = AnalysisContext(
            symbol=symbol,
            name=ticker.name,
            candles=candles,
            market_type=self.gateway.market_type,
        )
        signal = self.strategy.analyze(ctx)
        if signal:
            logger.info(
                "신호 발생 [supertrend] %s (%.1f점): %s",
                symbol, signal.score, signal.reason,
            )
        return signal


__all__ = ["SupertrendScanner"]
