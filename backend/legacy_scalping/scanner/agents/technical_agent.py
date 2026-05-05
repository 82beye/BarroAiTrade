"""
기술적 분석 에이전트

분석 항목:
  - 볼린저밴드 스퀴즈 (밴드 폭 축소 → 폭발 전조)
  - RSI 유리 구간 (40~65: 과매도도 아니고 과매수도 아닌 상승 여력)
  - MACD 골든크로스 임박/발생
  - 20일선 위 안착 + 이격도 적정
"""

import numpy as np
import pandas as pd
from typing import Optional

from scanner.agents.base_agent import BaseAgent, AgentSignal


class TechnicalAgent(BaseAgent):

    AGENT_NAME = "technical"
    MIN_DATA_LENGTH = 40

    def analyze_stock(
        self, code: str, name: str, df: pd.DataFrame,
    ) -> Optional[AgentSignal]:
        close = df['close']
        n = len(close)
        reasons = []
        score = 0.0

        # ── 1. BB 스퀴즈 (밴드 폭 축소 → 폭발 전조) ── (30점)
        bb_score = self._check_bb_squeeze(close)
        if bb_score > 0:
            score += bb_score
            reasons.append("BB 스퀴즈 (변동성 축소)")

        # ── 2. RSI 유리 구간 ── (20점)
        rsi = self._calc_rsi(close, 14)
        if not pd.isna(rsi):
            if 40 <= rsi <= 65:
                # 50이 최적, 40/65에서 0점
                dist = abs(rsi - 52.5)
                s = max(0, (12.5 - dist) / 12.5 * 20)
                score += s
                reasons.append(f"RSI {rsi:.0f} (적정 구간)")
            elif 30 <= rsi < 40:
                # 과매도 반등 기대
                score += 10
                reasons.append(f"RSI {rsi:.0f} (반등 기대)")

        # ── 3. MACD 골든크로스 ── (30점)
        macd_score, macd_reason = self._check_macd(close)
        if macd_score > 0:
            score += macd_score
            reasons.append(macd_reason)

        # ── 4. 20일선 위 안착 + 이격도 적정 ── (20점)
        ma20 = close.rolling(20).mean().iloc[-1]
        if not pd.isna(ma20) and ma20 > 0:
            deviation = (close.iloc[-1] - ma20) / ma20 * 100
            # 0~5% 이격 = 최적 (20일선 위 안착하되 과열 아님)
            if 0 <= deviation <= 5:
                s = 20 - abs(deviation - 2.5) / 2.5 * 10
                score += max(s, 0)
                reasons.append(f"20MA 위 {deviation:.1f}%")
            elif -2 <= deviation < 0:
                # 20일선 약간 아래 = 반등 기대
                score += 10
                reasons.append(f"20MA 근접 {deviation:.1f}%")

        if score <= 0:
            return None

        confidence = min(n / 100, 1.0)

        return AgentSignal(
            code=code, name=name,
            score=round(score, 2),
            confidence=round(confidence, 2),
            reasons=reasons,
        )

    def _check_bb_squeeze(self, close: pd.Series) -> float:
        """
        볼린저밴드 스퀴즈 감지 (30점)

        밴드 폭이 최근 20일 중 최소 근처이면 폭발 전조
        """
        if len(close) < 40:
            return 0

        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std(ddof=0)
        bandwidth = (std20 / ma20 * 100).dropna()

        if len(bandwidth) < 20:
            return 0

        current_bw = bandwidth.iloc[-1]
        recent_bw = bandwidth.iloc[-20:]
        min_bw = recent_bw.min()
        max_bw = recent_bw.max()

        if max_bw <= min_bw or pd.isna(current_bw):
            return 0

        # 현재 밴드 폭이 최소에 가까울수록 높은 점수
        # percentile: 0% = 가장 좁음 = 30점, 30% = 15점, 50%+ = 0점
        percentile = (current_bw - min_bw) / (max_bw - min_bw)
        if percentile <= 0.3:
            return (0.3 - percentile) / 0.3 * 30
        return 0

    @staticmethod
    def _calc_rsi(close: pd.Series, period: int = 14) -> float:
        """RSI 계산"""
        if len(close) < period + 1:
            return float('nan')

        delta = close.diff()
        gain = delta.where(delta > 0, 0)
        loss = (-delta).where(delta < 0, 0)

        avg_gain = gain.rolling(period).mean().iloc[-1]
        avg_loss = loss.rolling(period).mean().iloc[-1]

        if pd.isna(avg_gain) or pd.isna(avg_loss) or avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _check_macd(close: pd.Series) -> tuple:
        """
        MACD 골든크로스 체크 (30점)

        MACD = EMA(12) - EMA(26)
        Signal = EMA(MACD, 9)
        """
        if len(close) < 35:
            return 0, ""

        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9).mean()

        macd_cur = macd.iloc[-1]
        macd_prev = macd.iloc[-2]
        sig_cur = signal.iloc[-1]
        sig_prev = signal.iloc[-2]

        if pd.isna(macd_cur) or pd.isna(sig_cur):
            return 0, ""

        # 골든크로스 발생 (1~3봉 이내)
        for i in range(1, min(4, len(macd))):
            mc = macd.iloc[-i]
            mp = macd.iloc[-i - 1]
            sc = signal.iloc[-i]
            sp = signal.iloc[-i - 1]
            if not any(pd.isna(v) for v in [mc, mp, sc, sp]):
                if mc > sc and mp <= sp:
                    freshness = (4 - i) / 3  # 최근일수록 높은 점수
                    return round(30 * freshness, 2), f"MACD 골든크로스({i}봉전)"

        # 골든크로스 임박 (MACD > 0이고 시그널에 수렴 중)
        if macd_cur > 0:
            gap = (macd_cur - sig_cur) / abs(sig_cur) if sig_cur != 0 else 0
            if -0.1 < gap < 0.3:
                return 15, "MACD 시그널 수렴중"

        return 0, ""
