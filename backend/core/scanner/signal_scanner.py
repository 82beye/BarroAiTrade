"""
신호 스캐너 — 전략 엔진 통합 실행기

모든 전략을 순서대로 실행하고 진입 신호를 리스크 엔진으로 전달.
우선순위: SF존 > F존 > 블루라인 > 돌파 (점수 기준 최종 정렬)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

from backend.core.gateway.base import MarketGateway
from backend.core.strategy.f_zone import FZoneStrategy, FZoneParams
from backend.core.strategy.blue_line import BlueLineStrategy, BlueLineParams
from backend.core.strategy.crypto_breakout import CryptoBreakoutStrategy, CryptoBreakoutParams
from backend.models.market import MarketType
from backend.models.signal import EntrySignal

logger = logging.getLogger(__name__)


class SignalScanner:
    """
    전략 통합 스캐너

    사용법:
        scanner = SignalScanner(gateway)
        signals = await scanner.scan(["005930", "035720"])
    """

    def __init__(
        self,
        gateway: MarketGateway,
        f_zone_params: Optional[FZoneParams] = None,
        blue_line_params: Optional[BlueLineParams] = None,
        crypto_params: Optional[CryptoBreakoutParams] = None,
        timeframe: str = "5m",
        candle_limit: int = 120,
    ) -> None:
        self.gateway = gateway
        self.timeframe = timeframe
        self.candle_limit = candle_limit

        self.f_zone = FZoneStrategy(f_zone_params)
        self.blue_line = BlueLineStrategy(blue_line_params)
        self.crypto_breakout = CryptoBreakoutStrategy(crypto_params)

    async def scan(self, symbols: List[str]) -> List[EntrySignal]:
        """
        심볼 리스트를 스캔하여 모든 진입 신호를 점수 내림차순으로 반환.

        Args:
            symbols: 종목 코드 리스트

        Returns:
            EntrySignal 리스트 (점수 내림차순)
        """
        tasks = [self._analyze_symbol(sym) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        signals: List[EntrySignal] = []
        for sym, result in zip(symbols, results):
            if isinstance(result, Exception):
                logger.warning("%s 분석 오류: %s", sym, result)
            elif result is not None:
                signals.append(result)

        signals.sort(key=lambda s: s.score, reverse=True)
        logger.info("스캔 완료: %d/%d 종목에서 신호 발생", len(signals), len(symbols))
        return signals

    async def _analyze_symbol(self, symbol: str) -> Optional[EntrySignal]:
        """단일 종목 분석 — 전략 우선순위 순으로 실행"""
        try:
            ticker = await self.gateway.get_ticker(symbol)
            candles = await self.gateway.get_ohlcv(symbol, self.timeframe, self.candle_limit)
            if not candles:
                return None

            market_type = self.gateway.market_type

            # 전략 실행 (SF존 > F존 > 블루라인 > 돌파)
            for strategy_fn in [
                lambda: self.f_zone.analyze(symbol, ticker.name, candles, market_type),
                lambda: self.blue_line.analyze(symbol, ticker.name, candles, market_type),
                lambda: self.crypto_breakout.analyze(symbol, ticker.name, candles, market_type),
            ]:
                signal = strategy_fn()
                if signal:
                    logger.info("신호 발생 [%s] %s (%.1f점): %s", signal.signal_type, symbol, signal.score, signal.reason)
                    return signal

        except Exception as e:
            logger.error("%s 분석 실패: %s", symbol, e)
            raise

        return None
