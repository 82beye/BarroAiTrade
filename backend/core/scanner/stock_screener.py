"""
종목 스크리너 — Daily + Realtime 스캔

Daily Screener: 일중 캔들 분석 (5분봉)
- 파란점선 돌파: 주가 > blue_dotted_line
- 수박 신호: 3중 조건 (거래량폭증 + 캔들확장 + 바닥권)

Realtime Screener: 실시간 신호 감지
- WebSocket을 통한 실시간 거래량/가격 업데이트
- 신호 발생 시 즉시 브로드캐스트
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set, Callable

from backend.core.gateway.base import MarketGateway
from backend.core.scanner.indicators import IndicatorCalculator, TechnicalIndicators
from backend.models.market import OHLCV, MarketType
from backend.models.signal import EntrySignal

logger = logging.getLogger(__name__)


@dataclass
class ScreenerSignal:
    """스크리너에서 감지한 신호"""

    symbol: str
    name: str
    signal_type: str  # "blue_dotted_line", "watermelon"
    price: float
    indicator_value: float  # blue_dotted_line 선 또는 watermelon 강도
    score: float
    timestamp: datetime
    metadata: Dict = None

    def to_entry_signal(self, strategy_id: str = "screener_v1") -> EntrySignal:
        """EntrySignal로 변환"""
        return EntrySignal(
            symbol=self.symbol,
            name=self.name,
            price=self.price,
            signal_type=self.signal_type,
            score=self.score,
            reason=f"[{self.signal_type}] 스크리너 신호",
            market_type=MarketType.STOCK,
            strategy_id=strategy_id,
            timestamp=self.timestamp,
            metadata=self.metadata or {},
        )


class DailyScreener:
    """일중 종목 스크리너 (5분봉 기반)"""

    def __init__(self, gateway: MarketGateway):
        self.gateway = gateway
        self.indicator_calc = IndicatorCalculator()
        self.timeframe = "5m"
        self.candle_limit = 300  # ~25시간 (5분봉 기준)

    async def scan(self, symbols: List[str]) -> List[ScreenerSignal]:
        """여러 종목 스캔"""
        tasks = [self._analyze_symbol(sym) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        signals: List[ScreenerSignal] = []
        for sym, result in zip(symbols, results):
            if isinstance(result, Exception):
                logger.warning(f"스캔 오류 {sym}: {result}")
            elif result:
                signals.extend(result)

        signals.sort(key=lambda s: s.score, reverse=True)
        logger.info(f"일중 스캔 완료: {len(signals)}/{len(symbols)} 종목에서 신호 발생")
        return signals

    async def _analyze_symbol(self, symbol: str) -> List[ScreenerSignal]:
        """단일 종목 분석"""
        try:
            ticker = await self.gateway.get_ticker(symbol)
            candles = await self.gateway.get_ohlcv(symbol, self.timeframe, self.candle_limit)

            if not candles or len(candles) < 50:
                return []

            signals: List[ScreenerSignal] = []

            # 지표 계산
            indicators = self.indicator_calc.calculate(candles)
            if not indicators:
                return []

            latest = indicators[-1]
            candle = candles[-1]

            # 파란점선 신호
            if (
                candle.close > latest.blue_dotted_line
                and candles[-2].close <= indicators[-2].blue_dotted_line
            ):
                score = self._calculate_blue_dotted_score(candle, latest, indicators)
                signals.append(
                    ScreenerSignal(
                        symbol=symbol,
                        name=ticker.name,
                        signal_type="blue_dotted_line",
                        price=candle.close,
                        indicator_value=latest.blue_dotted_line,
                        score=score,
                        timestamp=datetime.now(),
                        metadata={
                            "blue_dotted_line": round(latest.blue_dotted_line, 2),
                            "atr": round(latest.atr, 2),
                            "rsi": round(latest.rsi, 2),
                        },
                    )
                )
                logger.info(
                    f"[파란점선] {symbol} | {candle.close:.0f} > {latest.blue_dotted_line:.0f} (점수: {score})"
                )

            # 수박 신호
            if latest.watermelon_signal:
                score = self._calculate_watermelon_score(latest, candle)
                signals.append(
                    ScreenerSignal(
                        symbol=symbol,
                        name=ticker.name,
                        signal_type="watermelon",
                        price=candle.close,
                        indicator_value=latest.watermelon_strength,
                        score=score,
                        timestamp=datetime.now(),
                        metadata={
                            "strength": round(latest.watermelon_strength, 2),
                            "volume": int(candle.volume),
                            "rsi": round(latest.rsi, 2),
                        },
                    )
                )
                logger.info(
                    f"[수박] {symbol} | 강도: {latest.watermelon_strength:.1%} (점수: {score})"
                )

            return signals

        except Exception as e:
            logger.error(f"분석 오류 {symbol}: {e}")
            return []

    @staticmethod
    def _calculate_blue_dotted_score(
        candle: OHLCV, latest_indicator, indicators: List
    ) -> float:
        """파란점선 신호 점수 계산"""
        # 기본 점수: 6.0
        score = 6.0

        # RSI 점수 (중립권 40~60 선호)
        rsi_score = 0
        if 40 <= latest_indicator.rsi <= 60:
            rsi_score = 1.5
        elif 30 <= latest_indicator.rsi <= 70:
            rsi_score = 1.0

        # 돌파 강도
        penetration_pct = (candle.close - latest_indicator.blue_dotted_line) / latest_indicator.blue_dotted_line
        penetration_score = min(penetration_pct / 0.02, 1.0) * 2.0

        score += rsi_score + penetration_score
        return round(min(score, 10.0), 2)

    @staticmethod
    def _calculate_watermelon_score(latest_indicator, candle: OHLCV) -> float:
        """수박 신호 점수 계산"""
        # 기본 점수: 7.0 (수박은 강한 신호)
        score = 7.0

        # 강도에 따른 보너스
        strength_bonus = latest_indicator.watermelon_strength * 2.5
        score += strength_bonus

        # RSI 점수
        if latest_indicator.rsi < 30:
            score += 1.0

        return round(min(score, 10.0), 2)


class RealtimeScreener:
    """실시간 종목 스크리너"""

    def __init__(
        self,
        gateway: MarketGateway,
        symbols: List[str],
        broadcast_callback: Optional[Callable[[ScreenerSignal], None]] = None,
    ):
        self.gateway = gateway
        self.symbols = symbols
        self.broadcast_callback = broadcast_callback

        self.running = False
        self.cached_candles: Dict[str, List[OHLCV]] = {}
        self.cached_indicators: Dict[str, List] = {}

        self.indicator_calc = IndicatorCalculator()
        self.scan_interval = 5  # 초 (5분봉 기준)

    async def start(self) -> None:
        """실시간 스캔 시작"""
        self.running = True
        logger.info(f"실시간 스캐너 시작: {len(self.symbols)} 종목")

        # 초기 캔들 로드
        await self._load_initial_candles()

        # 주기적 스캔
        while self.running:
            try:
                await self._scan_cycle()
                await asyncio.sleep(self.scan_interval)
            except Exception as e:
                logger.error(f"스캔 오류: {e}")
                await asyncio.sleep(self.scan_interval)

    async def stop(self) -> None:
        """실시간 스캔 중지"""
        self.running = False
        logger.info("실시간 스캐너 중지")

    async def _load_initial_candles(self) -> None:
        """초기 캔들 로드"""
        tasks = [self._fetch_candles(sym) for sym in self.symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for sym, result in zip(self.symbols, results):
            if not isinstance(result, Exception) and result:
                self.cached_candles[sym] = result
                self.cached_indicators[sym] = self.indicator_calc.calculate(result)

    async def _fetch_candles(self, symbol: str) -> List[OHLCV]:
        """캔들 데이터 조회"""
        try:
            return await self.gateway.get_ohlcv(symbol, "5m", 300)
        except Exception as e:
            logger.warning(f"캔들 조회 실패 {symbol}: {e}")
            return []

    async def _scan_cycle(self) -> None:
        """한 번의 스캔 사이클"""
        # 병렬 처리: 모든 종목 캔들 업데이트
        tasks = [self._check_symbol(sym) for sym in self.symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for sym, signal in zip(self.symbols, results):
            if signal and self.broadcast_callback:
                try:
                    await self.broadcast_callback(signal)
                except Exception as e:
                    logger.error(f"브로드캐스트 오류 {sym}: {e}")

    async def _check_symbol(self, symbol: str) -> Optional[ScreenerSignal]:
        """단일 종목 체크"""
        try:
            # 최신 캔들 조회
            candles = await self._fetch_candles(symbol)
            if not candles:
                return None

            # 캐시 업데이트
            self.cached_candles[symbol] = candles
            indicators = self.indicator_calc.calculate(candles)
            self.cached_indicators[symbol] = indicators

            if not indicators:
                return None

            latest = indicators[-1]
            candle = candles[-1]

            # 파란점선 돌파 감지
            if len(candles) >= 2:
                prev_candle = candles[-2]
                prev_indicator = indicators[-2]

                if (
                    candle.close > latest.blue_dotted_line
                    and prev_candle.close <= prev_indicator.blue_dotted_line
                ):
                    ticker = await self.gateway.get_ticker(symbol)
                    score = DailyScreener._calculate_blue_dotted_score(
                        candle, latest, indicators
                    )
                    return ScreenerSignal(
                        symbol=symbol,
                        name=ticker.name,
                        signal_type="blue_dotted_line",
                        price=candle.close,
                        indicator_value=latest.blue_dotted_line,
                        score=score,
                        timestamp=datetime.now(),
                        metadata={
                            "blue_dotted_line": round(latest.blue_dotted_line, 2),
                            "atr": round(latest.atr, 2),
                            "rsi": round(latest.rsi, 2),
                        },
                    )

                # 수박 신호 감지
                if latest.watermelon_signal:
                    ticker = await self.gateway.get_ticker(symbol)
                    score = DailyScreener._calculate_watermelon_score(latest, candle)
                    return ScreenerSignal(
                        symbol=symbol,
                        name=ticker.name,
                        signal_type="watermelon",
                        price=candle.close,
                        indicator_value=latest.watermelon_strength,
                        score=score,
                        timestamp=datetime.now(),
                        metadata={
                            "strength": round(latest.watermelon_strength, 2),
                            "volume": int(candle.volume),
                            "rsi": round(latest.rsi, 2),
                        },
                    )

            return None

        except Exception as e:
            logger.error(f"종목 체크 오류 {symbol}: {e}")
            return None

    def get_cached_signal_state(self) -> Dict[str, Dict]:
        """현재 캐시된 신호 상태 반환"""
        state = {}
        for sym in self.symbols:
            if sym in self.cached_indicators:
                indicators = self.cached_indicators[sym]
                if indicators:
                    latest = indicators[-1]
                    state[sym] = {
                        "blue_dotted_line": round(latest.blue_dotted_line, 2),
                        "watermelon": {
                            "signal": latest.watermelon_signal,
                            "strength": round(latest.watermelon_strength, 2),
                        },
                        "rsi": round(latest.rsi, 2),
                        "atr": round(latest.atr, 2),
                    }
        return state
