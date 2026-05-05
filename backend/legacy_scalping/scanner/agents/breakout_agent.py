"""
돌파 임박 에이전트

분석 항목:
  - 최근 N일 고가 근접도 (신고가 돌파 임박)
  - 저항선(20일/60일 고점) 접근
  - 파란점선(수박지표) 돌파 임박
  - 전일 대비 양봉 마감 + 윗꼬리 짧음 (매수세 강도)
"""

import numpy as np
import pandas as pd
from typing import Optional

from scanner.agents.base_agent import BaseAgent, AgentSignal


class BreakoutAgent(BaseAgent):

    AGENT_NAME = "breakout"
    MIN_DATA_LENGTH = 30

    def analyze_stock(
        self, code: str, name: str, df: pd.DataFrame,
    ) -> Optional[AgentSignal]:
        close = df['close']
        high = df['high']
        low = df['low']
        open_ = df['open']
        n = len(df)
        reasons = []
        score = 0.0

        cur_close = close.iloc[-1]

        # ── 1. 20일 신고가 근접도 ── (30점)
        high_20 = high.iloc[-20:].max()
        if high_20 > 0:
            proximity = cur_close / high_20
            if proximity >= 0.95:
                # 95%=15점, 100%+=30점
                s = min((proximity - 0.95) / 0.05 * 30, 30)
                score += s
                pct = (1 - proximity) * 100
                reasons.append(f"20일 고가 {pct:.1f}% 남음")

        # ── 2. 60일 저항선 접근 ── (20점)
        if n >= 60:
            high_60 = high.iloc[-60:].max()
            if high_60 > 0:
                prox_60 = cur_close / high_60
                if prox_60 >= 0.90:
                    s = min((prox_60 - 0.90) / 0.10 * 20, 20)
                    score += s
                    if prox_60 >= 0.98:
                        reasons.append("60일 고가 돌파 임박")

        # ── 3. 양봉 + 윗꼬리 짧음 (매수세 강도) ── (25점)
        candle_score = self._check_candle_strength(df)
        if candle_score > 0:
            score += candle_score
            reasons.append("강한 양봉 마감")

        # ── 4. 박스권 상단 돌파 시도 ── (25점)
        if n >= 20:
            box_score = self._check_box_breakout(close, high, low)
            if box_score > 0:
                score += box_score
                reasons.append("박스권 상단 돌파 시도")

        if score <= 0:
            return None

        confidence = min(n / 60, 1.0)

        return AgentSignal(
            code=code, name=name,
            score=round(score, 2),
            confidence=round(confidence, 2),
            reasons=reasons,
        )

    @staticmethod
    def _check_candle_strength(df: pd.DataFrame) -> float:
        """
        최근 캔들 매수세 강도 (25점)

        - 양봉 (종가 > 시가)
        - 윗꼬리 짧음 (고가-종가) / (고가-저가) < 20%
        - 몸통 비율 (종가-시가) / (고가-저가) > 50%
        """
        if len(df) < 1:
            return 0

        row = df.iloc[-1]
        o, h, l, c = row['open'], row['high'], row['low'], row['close']
        body_range = h - l

        if body_range <= 0 or c <= o:
            return 0

        body = c - o
        upper_wick = h - c
        body_ratio = body / body_range
        wick_ratio = upper_wick / body_range

        score = 0
        # 몸통 비율 높을수록
        if body_ratio > 0.5:
            score += min(body_ratio * 15, 15)
        # 윗꼬리 짧을수록
        if wick_ratio < 0.2:
            score += 10

        return score

    @staticmethod
    def _check_box_breakout(
        close: pd.Series, high: pd.Series, low: pd.Series,
    ) -> float:
        """
        박스권 상단 돌파 시도 (25점)

        최근 20일 가격 범위가 10% 이내(박스)이고,
        현재 종가가 박스 상단 90% 이상에 위치
        """
        recent_high = high.iloc[-20:].max()
        recent_low = low.iloc[-20:].min()

        if recent_low <= 0:
            return 0

        box_range = (recent_high - recent_low) / recent_low * 100

        # 박스권 판정: 범위 15% 이내
        if box_range > 15:
            return 0

        cur = close.iloc[-1]
        position = (cur - recent_low) / (recent_high - recent_low)

        # 상단 80% 이상 = 돌파 임박
        if position >= 0.8:
            return min((position - 0.8) / 0.2 * 25, 25)

        return 0
