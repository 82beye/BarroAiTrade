"""
모멘텀 에이전트

분석 항목:
  - 연속 상승일수 (최근 N일)
  - ROC (Rate of Change) 강도
  - 주요 이동평균선 위치 관계 (5, 10, 20, 60일)
  - 최근 5일 추세 기울기
"""

import numpy as np
import pandas as pd
from typing import Optional

from scanner.agents.base_agent import BaseAgent, AgentSignal


class MomentumAgent(BaseAgent):

    AGENT_NAME = "momentum"
    MIN_DATA_LENGTH = 60

    def analyze_stock(
        self, code: str, name: str, df: pd.DataFrame,
    ) -> Optional[AgentSignal]:
        close = df['close']
        n = len(close)
        reasons = []
        score = 0.0

        # ── 1. 연속 양봉 (최대 5봉 탐색) ── (25점)
        consec_up = 0
        for i in range(1, min(6, n)):
            if close.iloc[-i] > close.iloc[-i - 1]:
                consec_up += 1
            else:
                break

        if consec_up >= 2:
            s = min(consec_up / 5 * 25, 25)
            score += s
            reasons.append(f"{consec_up}일 연속 상승")

        # ── 2. ROC(5) 강도 ── (25점)
        if n >= 6:
            roc5 = (close.iloc[-1] - close.iloc[-6]) / close.iloc[-6] * 100
            if roc5 > 0:
                s = min(roc5 / 15 * 25, 25)
                score += s
                reasons.append(f"ROC5: {roc5:+.1f}%")

        # ── 3. MA 정배열 (5 > 10 > 20 > 60) ── (30점)
        ma5 = close.rolling(5).mean().iloc[-1]
        ma10 = close.rolling(10).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        ma60 = close.rolling(60).mean().iloc[-1]

        if not any(pd.isna(v) for v in [ma5, ma10, ma20, ma60]):
            ma_order_score = 0
            if ma5 > ma10:
                ma_order_score += 10
            if ma10 > ma20:
                ma_order_score += 10
            if ma20 > ma60:
                ma_order_score += 10
            if ma_order_score > 0:
                score += ma_order_score
                if ma_order_score == 30:
                    reasons.append("MA 완전정배열")
                else:
                    reasons.append(f"MA 정배열 일부({ma_order_score}점)")

        # ── 4. 5일 추세 기울기 ── (20점)
        if n >= 6:
            recent = close.iloc[-6:].values
            x = np.arange(len(recent))
            slope = np.polyfit(x, recent, 1)[0]
            slope_pct = slope / recent[0] * 100
            if slope_pct > 0:
                s = min(slope_pct / 3 * 20, 20)
                score += s
                reasons.append(f"기울기: {slope_pct:+.2f}%/일")

        if score <= 0:
            return None

        # 신뢰도: 데이터 충분성
        confidence = min(n / 120, 1.0)

        return AgentSignal(
            code=code, name=name,
            score=round(score, 2),
            confidence=round(confidence, 2),
            reasons=reasons,
        )
