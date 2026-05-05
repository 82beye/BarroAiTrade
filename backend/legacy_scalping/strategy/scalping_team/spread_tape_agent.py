"""
[Agent 10] 호가/테이프 분석 전문가

체결 데이터와 가격 움직임으로 매수/매도 세력 균형 판단.

핵심 원리:
  - 체결 속도 가속 = 관심 집중, 변동성 확대 임박
  - 상승 틱 비율 > 60% = 매수 우위
  - 가격 급등 후 체결 지속 = 진짜 수급
  - 가격 급등 후 체결 급감 = 허위 돌파
"""

import logging
from typing import List, Optional

import pandas as pd

from strategy.scalping_team.base_agent import (
    BaseScalpingAgent, ScalpingSignal, StockSnapshot,
)

logger = logging.getLogger(__name__)


class SpreadTapeAgent(BaseScalpingAgent):

    @property
    def name(self) -> str:
        return "호가테이프전문가"

    def analyze(
        self,
        snapshot: StockSnapshot,
        ohlcv: Optional[pd.DataFrame],
        intraday_prices: List[dict],
    ) -> Optional[ScalpingSignal]:

        signal = ScalpingSignal(
            agent_name=self.name, code=snapshot.code, name=snapshot.name)

        # ── 분봉/틱 데이터 기반 분석 ──
        if len(intraday_prices) < 5:
            return self._fallback_analysis(snapshot, ohlcv, signal)

        prices = [t['price'] for t in intraday_prices]
        volumes = [t.get('volume', 0) for t in intraday_prices]

        score = 40

        # ── 1. 상승/하락 틱 비율 ──
        up_ticks = 0
        down_ticks = 0
        up_volume = 0
        down_volume = 0
        for i in range(1, len(prices)):
            if prices[i] > prices[i - 1]:
                up_ticks += 1
                up_volume += volumes[i]
            elif prices[i] < prices[i - 1]:
                down_ticks += 1
                down_volume += volumes[i]

        total_ticks = up_ticks + down_ticks
        tick_ratio = up_ticks / total_ticks * 100 if total_ticks > 0 else 50

        if tick_ratio >= 65:
            score += 20
            signal.reasons.append(
                f"상승틱 {tick_ratio:.0f}% — 강한 매수 우위")
        elif tick_ratio >= 55:
            score += 10
            signal.reasons.append(f"상승틱 {tick_ratio:.0f}% — 매수 우위")
        elif tick_ratio <= 40:
            score -= 15
            signal.reasons.append(f"상승틱 {tick_ratio:.0f}% — 매도 우위")
        else:
            signal.reasons.append(f"상승틱 {tick_ratio:.0f}% — 균형")

        # ── 2. 매수/매도 거래량 비율 ──
        total_vol = up_volume + down_volume
        if total_vol > 0:
            buy_vol_ratio = up_volume / total_vol * 100
            if buy_vol_ratio >= 60:
                score += 15
                signal.reasons.append(
                    f"매수 거래량 {buy_vol_ratio:.0f}% — 수급 양호")
            elif buy_vol_ratio <= 40:
                score -= 10
                signal.reasons.append(
                    f"매수 거래량 {buy_vol_ratio:.0f}% — 매도 압력")

        # ── 3. 체결 속도 변화 ──
        n = len(intraday_prices)
        if n >= 10:
            recent_count = len(intraday_prices[-(n // 3):])
            earlier_count = len(intraday_prices[:n // 3])
            recent_vol = sum(volumes[-(n // 3):])
            earlier_vol = sum(volumes[:n // 3])

            if earlier_vol > 0:
                vol_accel = recent_vol / earlier_vol
                if vol_accel > 2.0:
                    score += 15
                    signal.reasons.append(
                        f"체결 가속 {vol_accel:.1f}배 — 관심 폭증")
                elif vol_accel > 1.3:
                    score += 5
                    signal.reasons.append(
                        f"체결 증가 {vol_accel:.1f}배")
                elif vol_accel < 0.5:
                    score -= 10
                    signal.reasons.append(
                        f"체결 급감 {vol_accel:.1f}배 — 관심 이탈")

        # ── 4. 가격 안정성 (최근 변동폭) ──
        if n >= 5:
            recent = prices[-5:]
            recent_volatility = (max(recent) - min(recent)) / min(recent) * 100

            if recent_volatility < 0.5:
                score += 5
                signal.reasons.append(
                    f"최근 변동폭 {recent_volatility:.1f}% — 안정 (돌파 임박 가능)")
            elif recent_volatility > 3:
                score -= 5
                signal.reasons.append(
                    f"최근 변동폭 {recent_volatility:.1f}% — 불안정")

        # ── 5. 마지막 추세 방향 ──
        if n >= 3:
            last_3 = prices[-3:]
            if last_3[0] < last_3[1] < last_3[2]:
                score += 5
                signal.reasons.append("최근 3틱 연속 상승")
            elif last_3[0] > last_3[1] > last_3[2]:
                score -= 5
                signal.reasons.append("최근 3틱 연속 하락")

        # ── 6. 하락 캔들 감지 (2026-04-07: 1분봉 분석 기반) ──
        # 최근 5틱 연속 하락 = 강한 매도 압력, 진입 금지
        if n >= 5:
            last_5 = prices[-5:]
            consecutive_fall = all(
                last_5[i] > last_5[i + 1] for i in range(len(last_5) - 1))
            if consecutive_fall:
                fall_pct = (last_5[0] - last_5[-1]) / last_5[0] * 100
                score -= 20
                signal.timing = "관망"
                signal.reasons.append(
                    f"🔻 5틱 연속 하락 (-{fall_pct:.1f}%) — 낙하 중 진입 금지")

            # 최근 1분 내 급락 체크 (>0.5% 하락)
            if last_5[0] > 0:
                minute_drop = (last_5[-1] - last_5[0]) / last_5[0] * 100
                if minute_drop <= -0.5:
                    score -= 15
                    signal.timing = "관망"
                    signal.reasons.append(
                        f"⚠ 직전 급락 {minute_drop:+.1f}% — 반등 확인 필요")

        # ── 종합 ──
        signal.entry_score = max(0, min(100, score))
        signal.confidence = min(0.3 + n / 50, 0.9)

        if score >= 65:
            signal.timing = "즉시"
            signal.entry_trigger = "체결 분석 매수 우위 — 적극 진입"
        elif score >= 50:
            signal.timing = "대기"
            signal.entry_trigger = "체결 분석 중립 — 추가 확인"
        else:
            signal.timing = "관망"
            signal.entry_trigger = "체결 분석 매도 우위 — 진입 회피"

        signal.scalp_tp_pct = 2.0
        signal.scalp_sl_pct = -1.5
        signal.hold_minutes = 10

        return signal

    def _fallback_analysis(
        self,
        snapshot: StockSnapshot,
        ohlcv: Optional[pd.DataFrame],
        signal: ScalpingSignal,
    ) -> Optional[ScalpingSignal]:
        """분봉 데이터 없을 때 일봉 기반 간접 분석"""
        score = 40

        # 시가 대비 현재가 위치로 매수/매도 세력 추정
        if snapshot.open > 0:
            open_to_cur = (snapshot.price - snapshot.open) / snapshot.open * 100
            if open_to_cur > 3:
                score += 15
                signal.reasons.append(
                    f"시가대비 +{open_to_cur:.1f}% — 장중 매수 우위")
            elif open_to_cur < -1:
                score -= 10
                signal.reasons.append(
                    f"시가대비 {open_to_cur:.1f}% — 장중 매도 우위")

        # 윗꼬리 비율로 매도 압력 추정
        if snapshot.high > snapshot.low:
            upper_shadow = (snapshot.high - snapshot.price) / (snapshot.high - snapshot.low)
            if upper_shadow > 0.5:
                score -= 10
                signal.reasons.append(
                    f"윗꼬리 {upper_shadow:.0%} — 상단 매도 압력")

        signal.entry_score = max(0, min(100, score))
        signal.confidence = 0.4
        signal.timing = "대기"
        signal.entry_trigger = "분봉 데이터 부족 — 간접 분석"
        signal.scalp_tp_pct = 2.0
        signal.scalp_sl_pct = -1.5
        signal.hold_minutes = 15

        return signal
