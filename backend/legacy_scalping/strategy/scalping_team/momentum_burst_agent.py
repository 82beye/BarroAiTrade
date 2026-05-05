"""
[Agent 2] 모멘텀 폭발 전문가

급등 초기 모멘텀을 포착하여 폭발적 상승의 초입에 진입.

핵심 원리:
  - 거래량 급증 + 가격 상승 동시 발생 = 모멘텀 폭발 시작
  - 상승률 대비 거래량 비율로 '진짜 모멘텀' vs '허위 급등' 판별
  - 5일 이내 유사 패턴 성공률 기반 신뢰도 산출
"""

import logging
from typing import List, Optional

import numpy as np
import pandas as pd

from strategy.scalping_team.base_agent import (
    BaseScalpingAgent, ScalpingSignal, StockSnapshot,
)

logger = logging.getLogger(__name__)


class MomentumBurstAgent(BaseScalpingAgent):

    @property
    def name(self) -> str:
        return "모멘텀폭발전문가"

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

        # ── 1. 당일 모멘텀 강도 ──
        change_pct = snapshot.change_pct
        vol_ratio = snapshot.volume_ratio

        # 모멘텀 점수: 상승률 × 거래량비율 (상호 증폭)
        momentum_score = min(change_pct * vol_ratio / 10, 50)

        # ── 2. 모멘텀 가속도 (분봉 기반) ──
        accel_bonus = 0
        if len(intraday_prices) >= 10:
            prices = [t['price'] for t in intraday_prices]
            # 최근 5틱 vs 이전 5틱 상승률 비교
            recent = prices[-5:]
            earlier = prices[-10:-5]
            recent_move = (recent[-1] - recent[0]) / recent[0] * 100 if recent[0] > 0 else 0
            earlier_move = (earlier[-1] - earlier[0]) / earlier[0] * 100 if earlier[0] > 0 else 0

            if recent_move > earlier_move and recent_move > 0:
                accel_bonus = min(recent_move * 5, 15)
                signal.reasons.append(
                    f"모멘텀 가속 중 (최근 {recent_move:+.1f}% > 이전 {earlier_move:+.1f}%)")
            elif recent_move < -0.3:
                # 2026-04-07: 강한 하락 모멘텀 감지 대폭 강화 (-20 → -30, 관망 강제)
                # 엔비알모션: 시가 21,250→18,300 하락 중 진입 방지
                accel_bonus = -30
                signal.timing = "관망"
                signal.reasons.append(
                    f"🔻 하락 모멘텀 (최근 {recent_move:+.1f}%) — 낙하 중 진입 금지")
            elif recent_move < 0:
                accel_bonus = -15
                signal.reasons.append(
                    f"모멘텀 감속 (최근 {recent_move:+.1f}%)")

            # 당일 고점 근접 추격 매수 감지 (고점 대비 1% 이내)
            if snapshot.high > 0:
                dist_from_high = (snapshot.high - snapshot.price) / snapshot.high * 100
                if dist_from_high < 1.0 and change_pct > 10:
                    accel_bonus -= 10
                    signal.reasons.append(
                        f"⚠ 고점 근접 ({dist_from_high:.1f}%) — 추격 매수 위험")

        # ── 3. 과거 유사 패턴 성공률 ──
        historical_score, hist_reason = self._check_historical_pattern(
            ohlcv, change_pct, vol_ratio)
        if hist_reason:
            signal.reasons.append(hist_reason)

        # ── 3.5. 시가 대비 하락추세 감지 (2026-04-07 신규) ──
        # 삼성E&A(시가 52K→45K), 엔비알모션(시가 21.25K→18.3K) 방지
        if snapshot.open > 0 and snapshot.price > 0:
            price_vs_open_pct = (snapshot.price - snapshot.open) / snapshot.open * 100
            if price_vs_open_pct <= -3.0:
                accel_bonus -= 25
                signal.timing = "관망"
                signal.reasons.append(
                    f"🔻 시가 대비 {price_vs_open_pct:+.1f}% 하락 — 하락추세 진입 금지")
            elif price_vs_open_pct <= -1.0:
                accel_bonus -= 10
                signal.reasons.append(
                    f"⚠ 시가 대비 {price_vs_open_pct:+.1f}% — 약세 주의")

        # ── 4. 모멘텀 단계 판단 (진입 구간 연동) ──
        # +5~15% 눌림목 구간이 최적, +20% 이상은 과열
        stage_adj = 0
        if change_pct < 5:
            stage_adj = -15
            signal.timing = "관망"
            signal.reasons.append(
                f"상승률 {change_pct:.1f}% — 방향 미확정 구간")
        elif change_pct <= 10:
            stage_adj = 20
            signal.timing = "즉시"
            signal.reasons.append(
                f"상승률 {change_pct:.1f}% — 눌림목 진입 최적 구간")
        elif change_pct <= 15:
            stage_adj = 10
            signal.timing = "즉시"
            signal.reasons.append(
                f"상승률 {change_pct:.1f}% — 눌림목 진입 양호")
        elif change_pct <= 20:
            stage_adj = -5
            signal.timing = "눌림목대기"
            signal.reasons.append(
                f"상승률 {change_pct:.1f}% — 고위험, 타이트 손절 필수")
        else:
            stage_adj = -25
            signal.timing = "관망"
            signal.reasons.append(
                f"상승률 {change_pct:.1f}% — 과열 구간, 진입 금지")

        # ── 5. 거래량 품질 ──
        vol_quality = 0
        if vol_ratio >= 5:
            vol_quality = 15
            signal.reasons.append(f"거래량 {vol_ratio:.1f}배 — 강한 수급")
        elif vol_ratio >= 3:
            vol_quality = 10
            signal.reasons.append(f"거래량 {vol_ratio:.1f}배 — 양호")
        elif vol_ratio >= 1.5:
            vol_quality = 0
            signal.reasons.append(f"거래량 {vol_ratio:.1f}배 — 보통")
        else:
            vol_quality = -15
            signal.reasons.append(f"거래량 {vol_ratio:.1f}배 — 수급 부족")

        # ── 종합 ──
        total = momentum_score + accel_bonus + historical_score + stage_adj + vol_quality
        signal.entry_score = max(0, min(100, total + 30))  # base 30
        signal.confidence = min(0.3 + (vol_ratio / 10) + (len(intraday_prices) / 60), 1.0)

        if not signal.timing:
            signal.timing = "대기"

        # 스캘핑 파라미터: 모멘텀 강할수록 넓은 TP
        signal.scalp_tp_pct = min(2.0 + change_pct * 0.15, 3.0)
        signal.scalp_sl_pct = -min(1.5 + change_pct * 0.05, 2.0)
        signal.hold_minutes = 10 if change_pct < 5 else 5

        signal.entry_trigger = (
            f"모멘텀 {change_pct:.1f}% × 거래량 {vol_ratio:.1f}배"
        )

        return signal

    def _check_historical_pattern(
        self,
        ohlcv: pd.DataFrame,
        current_change: float,
        current_vol_ratio: float,
    ) -> tuple:
        """과거 5일 이내 유사 급등 패턴 확인"""
        if len(ohlcv) < 20:
            return 0, None

        close = ohlcv['close'].values
        volume = ohlcv['volume'].values
        vol_ma20 = np.mean(volume[-20:])

        # 최근 10일 중 유사 급등일 찾기
        similar_days = 0
        follow_up_gains = []

        for i in range(-10, -1):
            day_change = (close[i] - close[i - 1]) / close[i - 1] * 100
            day_vol_ratio = volume[i] / vol_ma20 if vol_ma20 > 0 else 0

            # 유사 조건: 상승률 3%+ & 거래량 2배+
            if day_change >= 3 and day_vol_ratio >= 2:
                similar_days += 1
                # 다음날 성과
                if i + 1 < 0:
                    next_change = (close[i + 1] - close[i]) / close[i] * 100
                    follow_up_gains.append(next_change)

        if similar_days == 0:
            return 0, None

        avg_follow = np.mean(follow_up_gains) if follow_up_gains else 0
        win_rate = sum(1 for g in follow_up_gains if g > 0) / len(follow_up_gains) * 100

        score = 0
        if avg_follow > 1:
            score = 10
        elif avg_follow > 0:
            score = 5
        else:
            score = -10

        return score, (
            f"최근 유사 급등 {similar_days}회, "
            f"익일 평균 {avg_follow:+.1f}% (승률 {win_rate:.0f}%)"
        )
