"""
동적 포지션 사이징 에이전트

종목 변동성, 팀 에이전트 합의도, 과거 승률을 종합하여
종목별 최적 포지션 크기를 결정한다.

원칙:
  - 확신도 높은 종목: 비중 확대 (최대 1.5배)
  - 불확실한 종목: 비중 축소 (최소 0.5배)
  - 고변동성 종목: 비중 축소 (리스크 보정)
  - 연속 손실 시: 전체 비중 축소

분석 항목:
  1. 종목 변동성(ATR) 기반 기본 배율
  2. 팀 에이전트 합의 점수 반영
  3. 최근 연속 손실 횟수
  4. 일일 누적 손익 상태
"""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from strategy.strategy_team.base_agent import (
    BaseStrategyAgent, StrategySignal, TradeRecord,
)

logger = logging.getLogger(__name__)


class SizingAgent(BaseStrategyAgent):

    AGENT_NAME = "sizing"

    def analyze(
        self,
        trades: List[TradeRecord],
        cache_data: Dict[str, pd.DataFrame],
        watchlist: List[dict],
    ) -> Optional[StrategySignal]:
        signal = StrategySignal(
            agent_name=self.AGENT_NAME,
            confidence=0.0,
        )

        checks_done = 0

        # ── 1. 연속 손실 기반 전체 비중 조정 ──
        consecutive_losses = self._count_consecutive_losses(trades)
        if consecutive_losses >= 5:
            signal.position_size_multiplier = 0.3
            signal.reasons.append(
                f"연속 손절 {consecutive_losses}회 → "
                f"전체 포지션 30%로 축소")
            checks_done += 1
        elif consecutive_losses >= 3:
            signal.position_size_multiplier = 0.5
            signal.reasons.append(
                f"연속 손절 {consecutive_losses}회 → "
                f"전체 포지션 50%로 축소")
            checks_done += 1
        elif consecutive_losses >= 2:
            signal.position_size_multiplier = 0.7
            signal.reasons.append(
                f"연속 손절 {consecutive_losses}회 → "
                f"전체 포지션 70%로 축소")
            checks_done += 1

        # ── 2. 일일 손실 누적 상태 ──
        last_daily_pnl = self._get_latest_daily_pnl(trades)
        if last_daily_pnl is not None:
            if last_daily_pnl <= -3.0:
                current_mult = signal.position_size_multiplier or 1.0
                signal.position_size_multiplier = min(
                    current_mult, 0.3)
                signal.reasons.append(
                    f"전일 손실 {last_daily_pnl:+.1f}% → "
                    f"포지션 대폭 축소")
                checks_done += 1
            elif last_daily_pnl <= -1.5:
                current_mult = signal.position_size_multiplier or 1.0
                signal.position_size_multiplier = min(
                    current_mult, 0.7)
                signal.reasons.append(
                    f"전일 손실 {last_daily_pnl:+.1f}% → "
                    f"포지션 소폭 축소")
                checks_done += 1

        # ── 3. 종목별 변동성 기반 사이징 ──
        for stock in watchlist:
            code = stock['code']
            name = stock.get('name', code)
            df = cache_data.get(code)
            if df is None or len(df) < 15:
                continue

            vol_mult = self._calc_volatility_sizing(df)
            checks_done += 1

            if vol_mult < 0.7:
                signal.stock_penalty[code] = max(
                    signal.stock_penalty.get(code, 0),
                    1.0 - vol_mult)
                signal.reasons.append(
                    f"[{code}] {name}: 고변동성 "
                    f"→ 비중 {vol_mult:.0%}")
            elif vol_mult > 1.2:
                signal.stock_boost[code] = max(
                    signal.stock_boost.get(code, 0),
                    vol_mult - 1.0)
                signal.reasons.append(
                    f"[{code}] {name}: 안정적 변동성 "
                    f"→ 비중 {vol_mult:.0%}")

        # ── 4. 최근 수익 종목 패턴 기반 ──
        profit_pattern = self._analyze_profit_pattern(trades, watchlist)
        if profit_pattern:
            signal.reasons.append(profit_pattern)
            checks_done += 1

        signal.confidence = min(
            checks_done / max(len(watchlist) + 3, 1), 1.0)
        return signal if signal.reasons else None

    def _count_consecutive_losses(
        self, trades: List[TradeRecord],
    ) -> int:
        """최근 연속 손절 횟수 (뒤에서부터)"""
        sells = sorted(
            [t for t in trades if t.action == 'SELL'],
            key=lambda t: t.timestamp,
            reverse=True,
        )
        count = 0
        for s in sells:
            if s.pnl_pct < 0:
                count += 1
            else:
                break
        return count

    def _get_latest_daily_pnl(
        self, trades: List[TradeRecord],
    ) -> Optional[float]:
        """가장 최근 매매의 일일 누적 손익률"""
        if not trades:
            return None

        sorted_trades = sorted(
            trades, key=lambda t: t.timestamp, reverse=True)
        return sorted_trades[0].daily_pnl_pct

    def _calc_volatility_sizing(self, df: pd.DataFrame) -> float:
        """
        변동성 기반 포지션 비중 배율

        ATR(14) / 종가 비율 기반:
          - 2% 미만: 1.3배 (안정적)
          - 2~4%: 1.0배 (보통)
          - 4~6%: 0.7배 (위험)
          - 6%+: 0.5배 (고위험)
        """
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        n = len(df)

        if n < 15 or close[-1] <= 0:
            return 1.0

        tr = np.maximum(
            high[-14:] - low[-14:],
            np.maximum(
                np.abs(high[-14:] - close[-15:-1]),
                np.abs(low[-14:] - close[-15:-1])
            ),
        )
        atr_pct = float(np.mean(tr)) / close[-1] * 100

        if atr_pct < 2.0:
            return 1.3
        elif atr_pct < 4.0:
            return 1.0
        elif atr_pct < 6.0:
            return 0.7
        else:
            return 0.5

    def _analyze_profit_pattern(
        self,
        trades: List[TradeRecord],
        watchlist: List[dict],
    ) -> Optional[str]:
        """최근 수익 종목과 관심종목의 유사성 분석"""
        # 최근 수익 종목 코드 수집
        profit_codes = set()
        for t in trades:
            if t.action == 'SELL' and t.pnl_pct >= 3.0:
                profit_codes.add(t.code)

        if not profit_codes:
            return None

        # 관심종목 중 과거 수익 종목이 있으면 부스트 제안
        watchlist_codes = {s['code'] for s in watchlist}
        overlap = profit_codes & watchlist_codes

        if overlap:
            names = []
            for s in watchlist:
                if s['code'] in overlap:
                    names.append(s.get('name', s['code']))
            return (
                f"과거 수익 종목 재등장: "
                f"{', '.join(names[:3])} → 부스트 대상")

        return None
