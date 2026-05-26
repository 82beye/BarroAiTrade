"""
신호 스캐너 — 전략 엔진 통합 실행기

전략별 timeframe 매트릭스 (BAR-OPS-09 Phase C, 2026-05-27):
- swing_38           : 일봉 (1d) — multi-day 스윙 전략
- sf_zone, f_zone, blue_line, crypto_breakout : 1분봉 (1m) — intraday
- 우선순위: SF존 > F존 > 블루라인 > 돌파 > swing_38 (점수 기준 최종 정렬)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

from backend.core.gateway.base import MarketGateway
from backend.core.strategy.f_zone import FZoneStrategy, FZoneParams
from backend.core.strategy.sf_zone import SFZoneStrategy
from backend.core.strategy.blue_line import BlueLineStrategy, BlueLineParams
from backend.core.strategy.crypto_breakout import CryptoBreakoutStrategy, CryptoBreakoutParams
from backend.core.strategy.swing_38 import Swing38Strategy, Swing38Params
from backend.models.market import MarketType
from backend.models.signal import EntrySignal

logger = logging.getLogger(__name__)


class SignalScanner:
    """
    전략 통합 스캐너 — 전략별 timeframe 매트릭스 적용 (Phase C).

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
        swing_38_params: Optional[Swing38Params] = None,
        timeframe: str = "1m",       # BAR-OPS-09 Phase C: intraday 4 strategy default = 1분봉
        candle_limit: int = 120,
        daily_timeframe: str = "1d",  # swing_38 전용 일봉
        daily_candle_limit: int = 120,
    ) -> None:
        self.gateway = gateway
        self.timeframe = timeframe
        self.candle_limit = candle_limit
        self.daily_timeframe = daily_timeframe
        self.daily_candle_limit = daily_candle_limit

        self.sf_zone = SFZoneStrategy(f_zone_params)
        self.f_zone = FZoneStrategy(f_zone_params)
        self.blue_line = BlueLineStrategy(blue_line_params)
        self.crypto_breakout = CryptoBreakoutStrategy(crypto_params)
        # BAR-OPS-09 Phase C: swing_38 = 일봉 스캔 + 3~8일 보유 (require_daily_candles=True)
        self.swing_38 = Swing38Strategy(swing_38_params)

    async def scan(self, symbols: List[str]) -> List[EntrySignal]:
        """
        심볼 리스트 스캔 → 모든 진입 신호를 점수 내림차순으로 반환.

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
        """단일 종목 분석 — 전략별 timeframe 매트릭스 적용.

        - intraday 4 strategy (sf/f/blue/crypto): 1분봉 candles
        - swing_38: 일봉 candles
        우선순위: SF > F > Blue > Crypto > Swing38 (각 strategy 첫 시그널 반환).
        """
        try:
            ticker = await self.gateway.get_ticker(symbol)
            # 1분봉 fetch (intraday 4 strategy 용)
            candles_min = await self.gateway.get_ohlcv(symbol, self.timeframe, self.candle_limit)
            if not candles_min:
                return None

            market_type = self.gateway.market_type

            # intraday 4 strategy 평가 (1분봉)
            for strategy_fn in [
                lambda: self.sf_zone.analyze(symbol, ticker.name, candles_min, market_type),
                lambda: self.f_zone.analyze(symbol, ticker.name, candles_min, market_type),
                lambda: self.blue_line.analyze(symbol, ticker.name, candles_min, market_type),
                lambda: self.crypto_breakout.analyze(symbol, ticker.name, candles_min, market_type),
            ]:
                signal = strategy_fn()
                if signal:
                    logger.info("신호 발생 [%s] %s (%.1f점): %s",
                                signal.signal_type, symbol, signal.score, signal.reason)
                    return signal

            # swing_38 평가 (일봉) — intraday 미시그널 시 fallback
            candles_daily = await self.gateway.get_ohlcv(
                symbol, self.daily_timeframe, self.daily_candle_limit,
            )
            if candles_daily:
                sw_signal = self.swing_38.analyze(symbol, ticker.name, candles_daily, market_type)
                if sw_signal:
                    logger.info("신호 발생 [swing_38] %s (%.1f점): %s",
                                symbol, sw_signal.score, sw_signal.reason)
                    return sw_signal

        except Exception as e:
            logger.error("%s 분석 실패: %s", symbol, e)
            raise

        return None
