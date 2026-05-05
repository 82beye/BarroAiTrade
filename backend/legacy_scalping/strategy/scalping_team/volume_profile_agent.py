"""
[Agent 6] 거래량 프로파일 전문가

거래량 분포와 수급 패턴으로 기관/세력 매집 구간 식별.

핵심 원리:
  - 거래량 급증 + 가격 상승 = 매수세 유입 (긍정)
  - 거래량 급증 + 가격 정체 = 매집 구간 (강한 긍정)
  - 거래량 감소 + 가격 상승 = 유동성 부족 (위험)
  - 상승 캔들 거래량 > 하락 캔들 거래량 = OBV 양성
"""

import logging
from typing import List, Optional

import numpy as np
import pandas as pd

from strategy.scalping_team.base_agent import (
    BaseScalpingAgent, ScalpingSignal, StockSnapshot,
)

logger = logging.getLogger(__name__)


class VolumeProfileAgent(BaseScalpingAgent):

    @property
    def name(self) -> str:
        return "거래량프로파일전문가"

    def analyze(
        self,
        snapshot: StockSnapshot,
        ohlcv: Optional[pd.DataFrame],
        intraday_prices: List[dict],
    ) -> Optional[ScalpingSignal]:

        if ohlcv is None or len(ohlcv) < 10:
            return None

        signal = ScalpingSignal(
            agent_name=self.name, code=snapshot.code, name=snapshot.name)

        close = ohlcv['close'].values
        volume = ohlcv['volume'].values
        score = 35  # 2026-04-07: 40→35 하향 (false positive 감소)

        # ── 1. OBV (On-Balance Volume) 추세 ──
        obv = [0]
        for i in range(1, len(close)):
            if close[i] > close[i - 1]:
                obv.append(obv[-1] + volume[i])
            elif close[i] < close[i - 1]:
                obv.append(obv[-1] - volume[i])
            else:
                obv.append(obv[-1])

        # OBV 5일 vs 10일 추세
        obv_arr = np.array(obv)
        if len(obv_arr) >= 10:
            obv_5 = np.mean(obv_arr[-5:])
            obv_10 = np.mean(obv_arr[-10:])

            if obv_5 > obv_10:
                score += 15
                signal.reasons.append("OBV 상승세 — 매수세 우위")
            else:
                score -= 10
                signal.reasons.append("OBV 하락세 — 매도세 우위")

        # ── 2. 거래량-가격 괴리 분석 ──
        # 최근 5일 거래량 추세 vs 가격 추세
        if len(close) >= 5 and len(volume) >= 5:
            price_change = (close[-1] - close[-5]) / close[-5] * 100
            vol_change = (
                np.mean(volume[-3:]) / np.mean(volume[-8:-3]) * 100 - 100
                if np.mean(volume[-8:-3]) > 0 else 0
            )

            if price_change > 0 and vol_change > 50:
                score += 15
                signal.reasons.append(
                    f"가격 +{price_change:.1f}% & 거래량 +{vol_change:.0f}% — 수급 동반 상승")
            elif price_change > 0 and vol_change < -20:
                score -= 10
                signal.reasons.append(
                    f"가격 +{price_change:.1f}% & 거래량 {vol_change:.0f}% — 유동성 부족 상승")
            elif price_change < 0 and vol_change > 50:
                # 하락 중 거래량 증가 = 패닉셀
                score -= 15
                signal.reasons.append(
                    f"가격 {price_change:.1f}% & 거래량 +{vol_change:.0f}% — 투매 발생")

        # ── 3. 당일 거래량 품질 ──
        vol_ratio = snapshot.volume_ratio
        if vol_ratio >= 5:
            score += 15
            signal.reasons.append(f"당일 거래량 {vol_ratio:.1f}배 — 강한 수급 유입")
        elif vol_ratio >= 3:
            score += 10
            signal.reasons.append(f"당일 거래량 {vol_ratio:.1f}배 — 관심 집중")
        elif vol_ratio >= 1.5:
            score += 0
            signal.reasons.append(f"당일 거래량 {vol_ratio:.1f}배 — 보통")
        else:
            score -= 10
            signal.reasons.append(f"당일 거래량 {vol_ratio:.1f}배 — 관심 부족")

        # ── 4. 거래대금 기반 세력 감지 ──
        trade_value = snapshot.trade_value
        if trade_value >= 50_000_000_000:  # 500억+
            score += 10
            signal.reasons.append(f"거래대금 {trade_value/1e8:,.0f}억원 — 대형 수급")
        elif trade_value >= 10_000_000_000:  # 100억+
            score += 5
            signal.reasons.append(f"거래대금 {trade_value/1e8:,.0f}억원 — 양호")

        # ── 5. 분봉 체결 강도 (있을 경우) ──
        # 2026-04-07: intraday 가격 방향 체크 추가 — 하락 중이면 거래량 가속도 무효
        intraday_falling = False
        if len(intraday_prices) >= 5:
            recent_prices = [t['price'] for t in intraday_prices[-5:]]
            if recent_prices[-1] < recent_prices[0]:
                price_drop = (recent_prices[-1] - recent_prices[0]) / recent_prices[0] * 100
                if price_drop < -0.3:
                    intraday_falling = True
                    score -= 10
                    signal.reasons.append(
                        f"직전 5틱 하락 {price_drop:.1f}% — 매도 진행 중")

        if len(intraday_prices) >= 10:
            recent_vols = [t.get('volume', 0) for t in intraday_prices[-10:]]
            earlier_vols = [t.get('volume', 0) for t in intraday_prices[-20:-10]]

            if earlier_vols and sum(earlier_vols) > 0:
                vol_accel = sum(recent_vols) / sum(earlier_vols)
                if vol_accel > 1.5 and not intraday_falling:
                    # 2026-04-07: 하락 중이면 거래량 가속 보너스 비활성화
                    score += 10
                    signal.reasons.append(
                        f"체결 가속 {vol_accel:.1f}배 — 매수세 증가 중")
                elif vol_accel > 1.5 and intraday_falling:
                    score -= 5
                    signal.reasons.append(
                        f"체결 가속 {vol_accel:.1f}배이나 하락 중 — 투매 의심")
                elif vol_accel < 0.5:
                    score -= 5
                    signal.reasons.append(
                        f"체결 감속 {vol_accel:.1f}배 — 관심 이탈")

        # ── 종합 ──
        signal.entry_score = max(0, min(100, score))
        signal.confidence = min(0.4 + vol_ratio * 0.05 + len(intraday_prices) / 100, 0.95)

        if score >= 65:
            signal.timing = "즉시"
            signal.entry_trigger = "거래량 수급 양호 — 매수세 확인"
        elif score >= 50:
            signal.timing = "대기"
            signal.entry_trigger = "거래량 보통 — 추가 수급 확인 필요"
        else:
            signal.timing = "관망"
            signal.entry_trigger = "거래량 부정적 — 수급 미확인"

        signal.scalp_tp_pct = 2.5
        signal.scalp_sl_pct = -1.5
        signal.hold_minutes = 15

        return signal
