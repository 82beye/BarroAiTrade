"""
리스크/리워드 평가 에이전트

개별 종목의 리스크-리워드 프로파일을 OHLCV 캐시에서 분석하여
종목별 점수 부스트/페널티를 산출한다.

분석 항목:
  1. 일중 변동폭 (ATR) 대비 손절폭 적정성
  2. 과거 급등 후 급락 패턴 (whipsaw 위험)
  3. 종목별 하락 리스크 (최근 20일 최대 낙폭)
  4. 상승 여력 (저항선까지 거리)
  5. 과거 매매 이력 기반 종목 적합도
"""

import logging
from collections import defaultdict
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from strategy.strategy_team.base_agent import (
    BaseStrategyAgent, StrategySignal, TradeRecord,
)

logger = logging.getLogger(__name__)


class RiskRewardAgent(BaseStrategyAgent):

    AGENT_NAME = "risk_reward"

    def analyze(
        self,
        trades: List[TradeRecord],
        cache_data: Dict[str, pd.DataFrame],
        watchlist: List[dict],
    ) -> Optional[StrategySignal]:
        if not watchlist or not cache_data:
            return None

        signal = StrategySignal(
            agent_name=self.AGENT_NAME,
            confidence=0.0,
        )

        analyzed = 0
        watchlist_codes = {s['code'] for s in watchlist}

        # 과거 매매에서 종목별 실적
        trade_history = self._build_trade_history(trades)

        for stock in watchlist:
            code = stock['code']
            df = cache_data.get(code)
            if df is None or len(df) < 20:
                continue

            name = stock.get('name', code)
            analyzed += 1

            # ── 종목별 리스크 분석 ──
            risk_score = self._assess_risk(df, code, name)
            reward_score = self._assess_reward(df, code, name)

            # 과거 매매 실적 반영
            if code in trade_history:
                hist = trade_history[code]
                if hist['win_rate'] < 25 and hist['count'] >= 3:
                    signal.stock_penalty[code] = 0.3
                    signal.reasons.append(
                        f"[{code}] {name}: 과거 승률 "
                        f"{hist['win_rate']:.0f}% "
                        f"({hist['count']}회) → 30% 페널티")
                elif hist['win_rate'] >= 70 and hist['count'] >= 3:
                    signal.stock_boost[code] = 0.2
                    signal.reasons.append(
                        f"[{code}] {name}: 과거 승률 "
                        f"{hist['win_rate']:.0f}% "
                        f"→ 20% 부스트")

            # 고위험 종목 감지
            if risk_score >= 70 and reward_score < 40:
                signal.stock_penalty[code] = max(
                    signal.stock_penalty.get(code, 0), 0.5)
                signal.reasons.append(
                    f"[{code}] {name}: 고위험/저보상 "
                    f"(위험:{risk_score:.0f} 보상:{reward_score:.0f}) "
                    f"→ 50% 페널티")
            elif risk_score < 30 and reward_score >= 60:
                signal.stock_boost[code] = max(
                    signal.stock_boost.get(code, 0), 0.3)
                signal.reasons.append(
                    f"[{code}] {name}: 저위험/고보상 "
                    f"(위험:{risk_score:.0f} 보상:{reward_score:.0f}) "
                    f"→ 30% 부스트")

        # BB 과열 기준: ATR 분석 기반 동적 조정
        atr_based_bb = self._calc_atr_based_bb_limit(
            cache_data, watchlist_codes)
        if atr_based_bb is not None:
            signal.max_bb_excess_pct = atr_based_bb
            signal.reasons.append(
                f"ATR 기반 BB 과열 한도: +{atr_based_bb:.1f}%")

        signal.confidence = min(analyzed / max(len(watchlist), 1), 1.0)
        return signal if signal.reasons else None

    def _assess_risk(
        self, df: pd.DataFrame, code: str, name: str,
    ) -> float:
        """
        종목 리스크 점수 (0~100, 높을수록 위험)

        - ATR(14)/종가 비율이 높으면 고위험
        - 최근 20일 최대 낙폭이 크면 고위험
        - 윗꼬리 빈도가 높으면 고위험 (매도압력)
        """
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        n = len(df)
        risk = 0.0

        # ATR(14) 비율
        if n >= 15:
            tr = np.maximum(
                high[-14:] - low[-14:],
                np.maximum(
                    np.abs(high[-14:] - close[-15:-1]),
                    np.abs(low[-14:] - close[-15:-1])
                ),
            )
            atr = float(np.mean(tr))
            atr_pct = atr / close[-1] * 100 if close[-1] > 0 else 0
            # ATR 3% 이상 = 고위험
            risk += min(atr_pct / 5 * 40, 40)

        # 최근 20일 최대 일중 낙폭
        if n >= 20:
            daily_drops = (close[-20:] - high[-20:]) / high[-20:] * 100
            max_drop = float(np.min(daily_drops))
            # -5% 이상 낙폭 = 고위험
            risk += min(abs(max_drop) / 8 * 30, 30)

        # 윗꼬리 빈도 (최근 10일)
        if n >= 10:
            upper_wicks = 0
            for i in range(-10, 0):
                body_range = high[i] - low[i]
                if body_range > 0:
                    upper_wick = (high[i] - max(close[i], df['open'].values[i]))
                    if upper_wick / body_range > 0.4:
                        upper_wicks += 1
            # 10일 중 5회 이상 = 매도압력 강함
            risk += min(upper_wicks / 5 * 30, 30)

        return min(risk, 100)

    def _assess_reward(
        self, df: pd.DataFrame, code: str, name: str,
    ) -> float:
        """
        종목 보상 점수 (0~100, 높을수록 유망)

        - 상승 여력 (고점 대비 위치)
        - 최근 양봉 비율
        - 거래량 증가 추세
        """
        close = df['close'].values
        high = df['high'].values
        volume = df['volume'].values
        n = len(df)
        reward = 0.0

        # 20일 고점 대비 위치 (아직 여력이 있으면 높은 점수)
        if n >= 20:
            high_20 = float(np.max(high[-20:]))
            if high_20 > 0:
                headroom = (high_20 - close[-1]) / close[-1] * 100
                # 5% 이상 여력 = 좋음
                if headroom >= 2:
                    reward += min(headroom / 10 * 40, 40)

        # 최근 10일 양봉 비율
        if n >= 10:
            bullish = sum(
                1 for i in range(-10, 0)
                if close[i] > df['open'].values[i]
            )
            reward += bullish / 10 * 30

        # 거래량 5일 증가 추세
        if n >= 6:
            vol_5d = volume[-6:-1].astype(float)
            avg_vol = float(np.mean(vol_5d))
            if avg_vol > 0:
                slope = float(np.polyfit(
                    np.arange(5), vol_5d, 1)[0])
                slope_ratio = slope / avg_vol
                if slope_ratio > 0:
                    reward += min(slope_ratio * 100, 30)

        return min(reward, 100)

    def _build_trade_history(
        self, trades: List[TradeRecord],
    ) -> Dict[str, dict]:
        """종목별 과거 매매 실적"""
        history = defaultdict(lambda: {
            'count': 0, 'wins': 0, 'total_pnl': 0.0})

        for t in trades:
            if t.action == 'SELL':
                h = history[t.code]
                h['count'] += 1
                h['total_pnl'] += t.pnl_pct
                if t.pnl_pct > 0:
                    h['wins'] += 1

        result = {}
        for code, h in history.items():
            if h['count'] > 0:
                h['win_rate'] = h['wins'] / h['count'] * 100
                result[code] = h

        return result

    def _calc_atr_based_bb_limit(
        self,
        cache_data: Dict[str, pd.DataFrame],
        watchlist_codes: set,
    ) -> Optional[float]:
        """
        관심종목 평균 ATR 기반 BB 과열 한도 산출

        ATR(14)/종가가 작은 종목은 BB 돌파폭도 작으므로
        BB 과열 한도를 동적으로 조정
        """
        atr_pcts = []

        for code in watchlist_codes:
            df = cache_data.get(code)
            if df is None or len(df) < 15:
                continue

            close = df['close'].values
            high = df['high'].values
            low = df['low'].values

            tr = np.maximum(
                high[-14:] - low[-14:],
                np.maximum(
                    np.abs(high[-14:] - close[-15:-1]),
                    np.abs(low[-14:] - close[-15:-1])
                ),
            )
            atr = float(np.mean(tr))
            if close[-1] > 0:
                atr_pcts.append(atr / close[-1] * 100)

        if len(atr_pcts) >= 3:
            avg_atr = sum(atr_pcts) / len(atr_pcts)
            # BB 과열 한도 = 평균 ATR% × 2 (최소 3%, 최대 10%)
            limit = max(3.0, min(avg_atr * 2, 10.0))
            return round(limit, 1)

        return None
