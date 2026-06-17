"""
신호 스캐너 — 전략 엔진 통합 실행기

전략별 timeframe 매트릭스 (BAR-OPS-09 Phase D2.1, 2026-05-28 단타 전용 모드):
- **활성 (default, 1분봉 intraday 단타)**: sf_zone, f_zone, gold_zone
- **비활성 (default)**: blue_line, crypto_breakout, swing_38
  · 단타 전략 완성 이후 재개 예정 — `enabled_strategies` 인자로 override 가능
- 우선순위 (활성 단타): SF존 > F존 > 골드존 (점수 기준 최종 정렬)

이전 Phase C 매트릭스 (이력 보존):
- swing_38           : 일봉 (1d) — multi-day 스윙 전략
- sf/f/blue/crypto   : 1분봉 (1m) — intraday
- 우선순위: SF > F > Blue > Crypto > Swing38
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from backend.core.gateway.base import MarketGateway
from backend.core.strategy.blue_line import BlueLineParams, BlueLineStrategy
from backend.core.strategy.crypto_breakout import (
    CryptoBreakoutParams,
    CryptoBreakoutStrategy,
)
from backend.core.strategy.closing_bet import ClosingBetParams, ClosingBetStrategy
from backend.core.strategy.f_zone import FZoneParams, FZoneStrategy
from backend.core.strategy.gold_zone import GoldZoneParams, GoldZoneStrategy
from backend.core.strategy.sf_zone import SFZoneStrategy
from backend.core.strategy.swing_38 import Swing38Params, Swing38Strategy
from backend.models.market import MarketType  # noqa: F401  (외부 import 일관성)
from backend.models.signal import EntrySignal

logger = logging.getLogger(__name__)


# BAR-OPS-09 Phase D2.1 (2026-05-28) — 단타 전용 모드 default.
# 사용자 결정: 1·2·6번(sf/f/gold) 만 활성, 나머지(blue/crypto/swing_38) 단타 전략 완성 후 재개.
# 재활성화 시 SignalScanner(..., enabled_strategies={"swing_38": True, ...}) 로 override.
_DEFAULT_ENABLED: Dict[str, bool] = {
    "sf_zone": True,           # 1번 단타 활성
    "f_zone": True,            # 2번 단타 활성
    "gold_zone": True,         # 6번 단타 활성 (이전엔 SignalScanner 미등록 — D2.1 신규 등록)
    "blue_line": False,        # 3번 비활성
    "crypto_breakout": False,  # 4번 비활성
    # BAR-OPS-33 (2026-06-08): swing_38 활성화 — 4~6월 백테스트 안정성 1위
    #   (승률55%·손익비7.36·기대값+5.34·MDD-7.6). multi-day 청산은 holding_evaluator
    #   STRATEGY_EXIT_PROFILES["swing_38"](min_hold 3/max_hold 20)이 게이트.
    "swing_38": True,          # 5번 활성 (BAR-OPS-33)
    # thetrading-uplift Increment 1 (2026-06-17): 종가베팅(종베) — 기본 비활성 스캐폴딩.
    #   라이브 활성(분봉 자금유입·존 진입가·선정 hard-cut + 오버나잇 carry 한도)은 별도
    #   HITL 단계. 활성: SignalScanner(..., enabled_strategies={"closing_bet": True}).
    "closing_bet": False,
}

# BAR-OPS-33: 안정성 순위(4~6월 백테스트) — 슬롯/자본 경합 시 점수 동률이면 상위 전략 우선.
#   점수 1차 정렬, priority 2차 tiebreaker (작을수록 우선).
STRATEGY_PRIORITY: Dict[str, int] = {
    "swing_38": 1, "gold_zone": 2, "f_zone": 3, "sf_zone": 4,
    "supertrend": 5, "blue_line": 6, "crypto_breakout": 7, "closing_bet": 8,
}


class SignalScanner:
    """
    전략 통합 스캐너 — 전략별 timeframe 매트릭스 + 활성/비활성 flag (Phase D2.1).

    사용법:
        scanner = SignalScanner(gateway)
        signals = await scanner.scan(["005930", "035720"])

    Args:
        enabled_strategies: 전략별 활성 dict. None 시 _DEFAULT_ENABLED (sf/f/gold 만 True).
                            재활성화 예: `{"swing_38": True}` (지정 안 한 키는 default 유지).
    """

    def __init__(
        self,
        gateway: MarketGateway,
        f_zone_params: Optional[FZoneParams] = None,
        blue_line_params: Optional[BlueLineParams] = None,
        crypto_params: Optional[CryptoBreakoutParams] = None,
        swing_38_params: Optional[Swing38Params] = None,
        gold_zone_params: Optional[GoldZoneParams] = None,
        closing_bet_params: Optional[ClosingBetParams] = None,
        timeframe: str = "1m",       # intraday default = 1분봉
        candle_limit: int = 120,
        daily_timeframe: str = "1d",  # swing_38 전용 일봉 (비활성 시 사용 X)
        daily_candle_limit: int = 120,
        enabled_strategies: Optional[Dict[str, bool]] = None,
    ) -> None:
        self.gateway = gateway
        self.timeframe = timeframe
        self.candle_limit = candle_limit
        self.daily_timeframe = daily_timeframe
        self.daily_candle_limit = daily_candle_limit

        # 활성/비활성 매트릭스 — default 와 override 병합 (override 키만 덮어씀)
        self._enabled: Dict[str, bool] = dict(_DEFAULT_ENABLED)
        if enabled_strategies:
            self._enabled.update(enabled_strategies)

        # 전략 인스턴스는 비활성이라도 lazy/eager 모두 동일하게 생성 — 메모리 영향 미미,
        # 재활성화 시 코드 변경 없이 _enabled flag 만 토글하면 됨.
        self.sf_zone = SFZoneStrategy(f_zone_params)
        self.f_zone = FZoneStrategy(f_zone_params)
        self.gold_zone = GoldZoneStrategy(gold_zone_params)
        self.blue_line = BlueLineStrategy(blue_line_params)
        self.crypto_breakout = CryptoBreakoutStrategy(crypto_params)
        # Phase C swing_38 = 일봉 (require_daily_candles=True), D2.1 default 비활성
        self.swing_38 = Swing38Strategy(swing_38_params)
        # thetrading-uplift Increment 1: 종베 = 일봉 dispatch + ctx.timestamp 진입창. default OFF.
        self.closing_bet = ClosingBetStrategy(closing_bet_params)

        # 운영 가시성: 활성 전략 목록 로깅 (재기동 시 1회)
        active = [k for k, v in self._enabled.items() if v]
        logger.info("SignalScanner 활성 전략: %s", active)

    def is_enabled(self, strategy_key: str) -> bool:
        """전략 활성 여부 — 외부 dispatch/모니터링에서 참조 가능."""
        return self._enabled.get(strategy_key, False)

    async def scan(self, symbols: List[str]) -> List[EntrySignal]:
        """심볼 리스트 스캔 → 모든 진입 신호를 점수 내림차순으로 반환."""
        tasks = [self._analyze_symbol(sym) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        signals: List[EntrySignal] = []
        for sym, result in zip(symbols, results):
            if isinstance(result, Exception):
                logger.warning("%s 분석 오류: %s", sym, result)
            elif result is not None:
                signals.append(result)

        # 점수 1차(내림차순), 안정성 priority 2차(오름차순) tiebreaker — BAR-OPS-33.
        signals.sort(key=lambda s: (-s.score, STRATEGY_PRIORITY.get(s.signal_type, 99)))
        logger.info("스캔 완료: %d/%d 종목에서 신호 발생", len(signals), len(symbols))
        return signals

    async def _analyze_symbol(self, symbol: str) -> Optional[EntrySignal]:
        """단일 종목 분석 — 활성 전략만 dispatch.

        - 활성 intraday (sf/f/gold/blue/crypto 중 활성된 것): 1분봉 candles 1회 fetch
        - 활성 swing_38: 일봉 candles 별도 fetch (intraday 미시그널 시 fallback)
        - 모든 intraday 비활성 시 1m fetch skip (cost 절약)
        - swing_38 비활성 시 1d fetch skip (cost 절약)
        우선순위: SF > F > Gold > Blue > Crypto > Swing38 (활성된 것 중 첫 시그널 반환).
        """
        try:
            ticker = await self.gateway.get_ticker(symbol)
            market_type = self.gateway.market_type

            # 활성 intraday 전략 dispatch 리스트 — 우선순위 순
            intraday_dispatch = []
            if self._enabled["sf_zone"]:
                intraday_dispatch.append(("sf_zone", self.sf_zone))
            if self._enabled["f_zone"]:
                intraday_dispatch.append(("f_zone", self.f_zone))
            if self._enabled["gold_zone"]:
                intraday_dispatch.append(("gold_zone", self.gold_zone))
            if self._enabled["blue_line"]:
                intraday_dispatch.append(("blue_line", self.blue_line))
            if self._enabled["crypto_breakout"]:
                intraday_dispatch.append(("crypto_breakout", self.crypto_breakout))

            # 1m fetch — 활성 intraday 가 하나라도 있을 때만
            if intraday_dispatch:
                candles_min = await self.gateway.get_ohlcv(
                    symbol, self.timeframe, self.candle_limit,
                )
                if not candles_min:
                    return None

                for key, strategy in intraday_dispatch:
                    signal = strategy.analyze(symbol, ticker.name, candles_min, market_type)
                    if signal:
                        logger.info(
                            "신호 발생 [%s] %s (%.1f점): %s",
                            signal.signal_type, symbol, signal.score, signal.reason,
                        )
                        return signal

            # 일봉 전략 평가 — swing_38 또는 closing_bet 활성 시에만 fetch (cost 절약).
            if self._enabled["swing_38"] or self._enabled.get("closing_bet", False):
                candles_daily = await self.gateway.get_ohlcv(
                    symbol, self.daily_timeframe, self.daily_candle_limit,
                )
                if candles_daily:
                    if self._enabled["swing_38"]:
                        sw_signal = self.swing_38.analyze(
                            symbol, ticker.name, candles_daily, market_type,
                        )
                        if sw_signal:
                            logger.info(
                                "신호 발생 [swing_38] %s (%.1f점): %s",
                                symbol, sw_signal.score, sw_signal.reason,
                            )
                            return sw_signal
                    # thetrading-uplift Increment 1: 종베 (기본 OFF). 진입 시간창(KST
                    #   15:00~15:20)·신고가·장대양봉 게이트. 활성화는 별도 HITL 단계.
                    if self._enabled.get("closing_bet", False):
                        cb_signal = self.closing_bet.analyze(
                            symbol, ticker.name, candles_daily, market_type,
                        )
                        if cb_signal:
                            logger.info(
                                "신호 발생 [closing_bet] %s (%.1f점): %s",
                                symbol, cb_signal.score, cb_signal.reason,
                            )
                            return cb_signal

        except Exception as e:
            logger.error("%s 분석 실패: %s", symbol, e)
            raise

        return None
