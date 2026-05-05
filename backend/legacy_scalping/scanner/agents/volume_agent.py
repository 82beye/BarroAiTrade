"""
거래량 에이전트

분석 항목:
  - OBV (On Balance Volume) 추세 상승
  - 거래량 점증 패턴 (최근 5일 거래량 증가 추세)
  - 거래대금 급증 전조 (평균 대비 1.5~3배 — 본격 폭발 직전)
  - 가격 횡보 + 거래량 증가 패턴 (매집 의심)
"""

import numpy as np
import pandas as pd
from typing import Optional

from scanner.agents.base_agent import BaseAgent, AgentSignal


class VolumeAgent(BaseAgent):

    AGENT_NAME = "volume"
    MIN_DATA_LENGTH = 30

    def analyze_stock(
        self, code: str, name: str, df: pd.DataFrame,
    ) -> Optional[AgentSignal]:
        close = df['close']
        volume = df['volume']
        n = len(df)
        reasons = []
        score = 0.0

        avg_vol_20 = volume.iloc[-21:-1].mean() if n >= 21 else volume.mean()
        if pd.isna(avg_vol_20) or avg_vol_20 <= 0:
            return None

        # ── 1. OBV 추세 (최근 10일 OBV 기울기) ── (25점)
        obv = self._calc_obv(close, volume)
        if len(obv) >= 11:
            obv_recent = obv.iloc[-11:].values
            x = np.arange(len(obv_recent))
            slope = np.polyfit(x, obv_recent, 1)[0]
            if slope > 0:
                # 정규화: 기울기를 평균 거래량 대비 비율로
                slope_ratio = slope / avg_vol_20
                s = min(slope_ratio * 25, 25)
                score += s
                reasons.append(f"OBV 상승추세(x{slope_ratio:.1f})")

        # ── 2. 거래량 점증 (최근 5일 기울기) ── (25점)
        if n >= 6:
            vol_5d = volume.iloc[-6:-1].values  # 전일 기준 5일
            x = np.arange(len(vol_5d))
            vol_slope = np.polyfit(x, vol_5d, 1)[0]
            if vol_slope > 0:
                slope_ratio = vol_slope / avg_vol_20
                s = min(slope_ratio * 5 * 25, 25)
                score += s
                reasons.append(f"거래량 5일 점증")

        # ── 3. 거래대금 급증 전조 (1.5~3배 구간) ── (25점)
        #    완전 폭발(3x+)은 이미 뉴스 반영, 1.5~3배가 매집 초기
        if n >= 21:
            trade_val = (close * volume).iloc[-1]
            avg_tv_20 = (close * volume).iloc[-21:-1].mean()
            if avg_tv_20 > 0:
                tv_ratio = trade_val / avg_tv_20
                if 1.3 <= tv_ratio <= 4.0:
                    # 1.5x=10점, 2.5x=25점 (스위트 스팟)
                    s = min((tv_ratio - 1.3) / 1.7 * 25, 25)
                    score += s
                    reasons.append(f"거래대금 x{tv_ratio:.1f}")

        # ── 4. 매집 패턴 (가격 횡보 + 거래량 증가) ── (25점)
        if n >= 11:
            price_5d = close.iloc[-6:-1]
            vol_5d_vals = volume.iloc[-6:-1]

            price_range = (price_5d.max() - price_5d.min()) / price_5d.mean() * 100
            vol_change = vol_5d_vals.iloc[-1] / vol_5d_vals.iloc[0] if vol_5d_vals.iloc[0] > 0 else 0

            # 가격 변동 3% 이내 + 거래량 50% 이상 증가 = 매집
            if price_range < 3.0 and vol_change > 1.5:
                s = min((vol_change - 1) * 25, 25)
                score += s
                reasons.append(f"매집 의심(가격 {price_range:.1f}% 횡보, 거래량 +{(vol_change-1)*100:.0f}%)")

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
    def _calc_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
        """On Balance Volume 계산"""
        direction = close.diff().apply(
            lambda x: 1 if x > 0 else (-1 if x < 0 else 0)
        )
        obv = (volume * direction).cumsum()
        return obv
