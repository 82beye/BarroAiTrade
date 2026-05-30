"""Supertrend 보유 포지션 청산 감시기 — 2026-05-31 신규.

슈퍼트렌드 전략으로 **매수된 종목을 추적**하다가, 5분봉 슈퍼트렌드가
하락 추세전환(매도/숏 시그널)을 내면 해당 포지션을 정리하라는 ExitSignal 을 산출한다.
SupertrendScanner(진입 신호)의 거울상 — 같은 5분봉·같은 지표로 진입의 역(逆)을 본다.

설계:
  - 입력: 현재 보유 Position 리스트(브로커/active_positions 에서 구성).
  - strategy_id 가 supertrend 인 포지션만 대상 (다른 전략 포지션엔 개입 X).
  - 각 대상 종목의 5분봉을 재조회 → AnalysisContext → strategy.exit_on_signal().
  - 하락 전환이면 ExitSignal(exit_type="reverse_signal"), 아니면 보유 유지.

가격 기반 TP/SL(ExitEngine)·브로커 pnl 기반(HoldingEvaluator)을 **보완**하는
지표 기반 청산 트리거. signal-only — 실제 매도 주문은 상위 실행 레이어가 담당.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Iterable, Optional

from backend.core.gateway.base import MarketGateway
from backend.core.strategy.supertrend import SupertrendParams, SupertrendStrategy
from backend.models.position import Position
from backend.models.signal import ExitSignal
from backend.models.strategy import AnalysisContext

logger = logging.getLogger(__name__)

# 이 watcher 가 책임지는 전략 식별자 (supertrend_v1 등 prefix 매칭).
_STRATEGY_PREFIX = "supertrend"


class SupertrendExitWatcher:
    """슈퍼트렌드 보유분 추적 → 하락 추세전환 시 청산 시그널 산출.

    사용법:
        watcher = SupertrendExitWatcher(gateway)
        exits = await watcher.check(current_positions)   # list[ExitSignal]
        # exits 의 각 종목을 상위 실행 레이어가 매도 처리

    Args:
        gateway: 5분봉 get_ohlcv 시세 게이트웨이.
        strategy: SupertrendStrategy (None 시 기본 파라미터 — 진입과 동일 설정 권장).
        timeframe: 캔들 주기 (기본 "5m" — 진입 스캐너와 일치).
        candle_limit: fetch 봉 수 (ATR/추세 안정화, 기본 200).
    """

    def __init__(
        self,
        gateway: MarketGateway,
        strategy: Optional[SupertrendStrategy] = None,
        params: Optional[SupertrendParams] = None,
        timeframe: str = "5m",
        candle_limit: int = 200,
    ) -> None:
        self.gateway = gateway
        self.strategy = strategy or SupertrendStrategy(params)
        self.timeframe = timeframe
        self.candle_limit = candle_limit

    @staticmethod
    def _is_supertrend(position: Position) -> bool:
        sid = (position.strategy_id or "").lower()
        return sid.startswith(_STRATEGY_PREFIX)

    async def check(self, positions: Iterable[Position]) -> list[ExitSignal]:
        """보유 포지션 중 슈퍼트렌드 진입분의 하락전환 청산 시그널 리스트.

        한 종목 평가 실패는 해당 종목만 skip (나머지 청산 평가 계속 — 안전망 보존).
        """
        targets = [p for p in positions if self._is_supertrend(p)]
        if not targets:
            return []

        results = await asyncio.gather(
            *(self._check_one(p) for p in targets), return_exceptions=True,
        )
        exits: list[ExitSignal] = []
        for pos, res in zip(targets, results):
            if isinstance(res, Exception):
                logger.warning("%s 청산 평가 오류: %s", pos.symbol, type(res).__name__)
            elif res is not None:
                exits.append(res)
        if exits:
            logger.info("슈퍼트렌드 청산 시그널 %d건: %s",
                        len(exits), [e.symbol for e in exits])
        return exits

    async def _check_one(self, position: Position) -> Optional[ExitSignal]:
        candles = await self.gateway.get_ohlcv(
            position.symbol, self.timeframe, self.candle_limit,
        )
        if not candles:
            return None
        ctx = AnalysisContext(
            symbol=position.symbol,
            name=position.name or position.symbol,
            candles=candles,
            market_type=position.market_type,
        )
        # 현재가: 포지션 보고가 우선, 없으면 최신 종가.
        cur = position.current_price if position.current_price > 0 else candles[-1].close
        return self.strategy.exit_on_signal(position, ctx, Decimal(str(cur)))


__all__ = ["SupertrendExitWatcher"]
