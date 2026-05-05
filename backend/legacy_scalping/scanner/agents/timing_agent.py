"""
타이밍 에이전트

OHLCV 캐시를 분석하여 종목의 장중 타이밍 적합성을 평가한다.
기존 4개 에이전트(momentum, volume, technical, breakout)에 추가하여
5번째 팀 에이전트로 동작.

분석 항목:
  1. 최근 N일 양봉 비율 및 패턴 (연속 양봉 후 눌림목)
  2. 장 초반 강도 (시가 > 전일종가 비율)
  3. 가격 안정성 (일중 변동폭 수렴)
  4. 거래량 분포 패턴 (점진 증가 vs 급감)
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from scanner.agents.base_agent import BaseAgent, AgentSignal

logger = logging.getLogger(__name__)


class TimingAgent(BaseAgent):
    """
    타이밍 적합성 평가 에이전트

    "지금 진입하기 좋은 타이밍인가"를 일봉 패턴으로 판단.
    당일 장중 실시간 데이터는 사용하지 않고,
    최근 일봉 패턴에서 진입 적합 시점을 예측한다.
    """

    AGENT_NAME = "timing"
    MIN_DATA_LENGTH = 20

    def analyze_stock(
        self, code: str, name: str, df: pd.DataFrame,
    ) -> Optional[AgentSignal]:
        """종목 타이밍 분석"""
        score = 0.0
        reasons = []

        close = df['close'].values
        open_ = df['open'].values
        high = df['high'].values
        low = df['low'].values
        volume = df['volume'].values

        n = len(df)

        # ── 1. 눌림목 후 반등 패턴 (0-30점) ──
        pullback_score = self._score_pullback_bounce(close, high, low, n)
        score += pullback_score
        if pullback_score >= 15:
            reasons.append(
                f"눌림목 반등 패턴 ({pullback_score:.0f}점)")

        # ── 2. 장 초반 강도 — 갭 업 빈도 (0-25점) ──
        gap_score = self._score_gap_up_pattern(close, open_, n)
        score += gap_score
        if gap_score >= 12:
            reasons.append(f"갭업 빈도 양호 ({gap_score:.0f}점)")

        # ── 3. 가격 변동폭 수렴 (0-25점) ──
        volatility_score = self._score_volatility_convergence(
            high, low, close, n)
        score += volatility_score
        if volatility_score >= 12:
            reasons.append(
                f"변동폭 수렴 ({volatility_score:.0f}점)")

        # ── 4. 거래량 점진 증가 패턴 (0-20점) ──
        vol_pattern_score = self._score_volume_pattern(volume, n)
        score += vol_pattern_score
        if vol_pattern_score >= 10:
            reasons.append(
                f"거래량 점진 증가 ({vol_pattern_score:.0f}점)")

        if score <= 0:
            return None

        confidence = min(n / 60, 1.0)

        return AgentSignal(
            code=code,
            name=name,
            score=round(min(score, 100), 1),
            confidence=round(confidence, 2),
            reasons=reasons,
        )

    def _score_pullback_bounce(
        self,
        close: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        n: int,
    ) -> float:
        """
        눌림목 후 반등 패턴

        조건: 최근 5-10일 내 고점 형성 후 조정,
             마지막 1-2일 반등 시작 시 높은 점수
        """
        if n < 10:
            return 0

        recent = close[-10:]
        peak_idx = int(np.argmax(recent[:8]))  # 최근 10일 중 앞 8일의 고점
        peak_price = recent[peak_idx]

        if peak_idx < 2:
            return 0  # 고점이 너무 오래 전

        # 고점 대비 현재가 위치
        current = recent[-1]
        from_peak = (current - peak_price) / peak_price * 100

        # 고점에서 -3% ~ 0% 사이 = 눌림목 후 반등 구간
        if -5.0 <= from_peak <= -1.0:
            # 마지막 캔들이 양봉이면 반등 시작
            if current > recent[-2]:
                score = 30 * (1 - abs(from_peak + 3) / 4)
                return max(0, min(score, 30))
        elif 0 <= from_peak <= 2.0:
            # 고점 근처에서 안정적 = 돌파 임박
            if current > recent[-2]:
                return 20.0

        return 0

    def _score_gap_up_pattern(
        self,
        close: np.ndarray,
        open_: np.ndarray,
        n: int,
    ) -> float:
        """
        최근 10일 중 갭 업(시가 > 전일종가) 빈도

        갭 업이 잦으면 매수세가 강하다는 의미
        """
        if n < 11:
            return 0

        gap_ups = 0
        for i in range(-10, 0):
            if open_[i] > close[i - 1]:
                gap_ups += 1

        # 10일 중 4회 이상이면 좋음 (40%+)
        if gap_ups >= 6:
            return 25
        elif gap_ups >= 4:
            return min((gap_ups - 3) / 3 * 25, 25)

        return 0

    def _score_volatility_convergence(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        n: int,
    ) -> float:
        """
        일중 변동폭(high-low) 수렴 패턴

        최근 변동폭이 줄어들면 에너지 축적 → 돌파 임박
        """
        if n < 10:
            return 0

        # 일중 변동폭 (%)
        ranges = (high[-10:] - low[-10:]) / close[-10:] * 100

        # 전반부 vs 후반부 평균
        first_half = float(np.mean(ranges[:5]))
        second_half = float(np.mean(ranges[5:]))

        if first_half <= 0:
            return 0

        contraction = (first_half - second_half) / first_half

        # 수렴도 30% 이상이면 좋음
        if contraction >= 0.3:
            return min(contraction / 0.5 * 25, 25)

        return 0

    def _score_volume_pattern(
        self,
        volume: np.ndarray,
        n: int,
    ) -> float:
        """
        거래량 점진 증가 패턴

        급증이 아닌 점진적 증가 = 기관/세력 매집
        """
        if n < 10:
            return 0

        recent_vol = volume[-10:].astype(float)
        if np.mean(recent_vol[:5]) <= 0:
            return 0

        # 선형 회귀 기울기로 증가 추세 판단
        x = np.arange(10, dtype=float)
        # polyfit으로 기울기 계산
        coeffs = np.polyfit(x, recent_vol, 1)
        slope = coeffs[0]

        # 평균 대비 기울기 비율
        avg_vol = float(np.mean(recent_vol))
        if avg_vol <= 0:
            return 0
        slope_ratio = slope / avg_vol

        # 기울기가 양수이면서 적당한 범위 (0.02~0.15)
        # 너무 급격하면 이미 폭발 후일 수 있음
        if 0.02 <= slope_ratio <= 0.15:
            score = min(slope_ratio / 0.1 * 20, 20)
            return score

        return 0
