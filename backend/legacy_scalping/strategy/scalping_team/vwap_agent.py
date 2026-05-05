"""
[Agent 1] VWAP 전략가

거래량 가중 평균가(VWAP) 기반 스캘핑 진입 분석.
VWAP 아래에서 매수 → VWAP 위에서 매도하는 평균 회귀 전략.

핵심 원리:
  - 상승 종목이 VWAP 아래로 일시 후퇴 = 기관 매수 영역
  - VWAP 위에서 멀어질수록 단기 과열 → 진입 불리
  - VWAP 기울기로 매수 모멘텀 지속 여부 판단
"""

import logging
from typing import List, Optional

import pandas as pd

from strategy.scalping_team.base_agent import (
    BaseScalpingAgent, ScalpingSignal, StockSnapshot,
)

logger = logging.getLogger(__name__)


class VWAPAgent(BaseScalpingAgent):

    @property
    def name(self) -> str:
        return "VWAP전략가"

    def analyze(
        self,
        snapshot: StockSnapshot,
        ohlcv: Optional[pd.DataFrame],
        intraday_prices: List[dict],
    ) -> Optional[ScalpingSignal]:

        signal = ScalpingSignal(
            agent_name=self.name, code=snapshot.code, name=snapshot.name)

        if not intraday_prices or len(intraday_prices) < 5:
            # 분봉 데이터 없으면 일봉 기반 간접 VWAP
            return self._fallback_daily_vwap(snapshot, ohlcv, signal)

        # ── VWAP 계산 ──
        cum_vol = 0
        cum_pv = 0  # price × volume
        vwap_values = []
        for tick in intraday_prices:
            p = tick.get('price', 0)
            v = tick.get('volume', 0)
            cum_vol += v
            cum_pv += p * v
            vwap = cum_pv / cum_vol if cum_vol > 0 else p
            vwap_values.append(vwap)

        current_vwap = vwap_values[-1] if vwap_values else snapshot.price
        price = snapshot.price
        deviation_pct = (price - current_vwap) / current_vwap * 100

        # ── VWAP 기울기 (최근 vs 이전) ──
        vwap_slope = 0
        if len(vwap_values) >= 10:
            recent = sum(vwap_values[-5:]) / 5
            earlier = sum(vwap_values[-10:-5]) / 5
            vwap_slope = (recent - earlier) / earlier * 100

        # ── 진입 판단 ──
        score = 35  # 기본 (2026-04-07: 50→35 하향, 하락 중 진입 방지)

        # VWAP 아래 = 매수 유리 (평균 회귀 기대)
        if deviation_pct < -0.5:
            score += min(abs(deviation_pct) * 10, 30)
            signal.timing = "즉시"
            signal.entry_trigger = f"VWAP 하방 이탈 {deviation_pct:+.1f}% — 평균 회귀 진입"
            signal.entry_price_zone = current_vwap * 0.997
        elif deviation_pct < 0.3:
            score += 15
            signal.timing = "즉시"
            signal.entry_trigger = f"VWAP 근접 {deviation_pct:+.1f}% — 적정 진입 구간"
            signal.entry_price_zone = current_vwap
        elif deviation_pct < 1.5:
            score -= 10
            signal.timing = "눌림목대기"
            signal.entry_trigger = f"VWAP 상방 {deviation_pct:+.1f}% — 눌림 시 진입"
            signal.entry_price_zone = current_vwap * 1.003
        else:
            score -= 25
            signal.timing = "관망"
            signal.entry_trigger = f"VWAP 과이탈 {deviation_pct:+.1f}% — 추격 금지"

        # VWAP 상승 기울기면 보너스
        if vwap_slope > 0.1:
            score += 10
            signal.reasons.append(f"VWAP 상승세 ({vwap_slope:+.2f}%)")
        elif vwap_slope < -0.1:
            # 2026-04-07: VWAP 하락세 → 강제 관망 (하락 중 진입 방지)
            score -= 15
            signal.timing = "관망"
            signal.reasons.append(f"VWAP 하락세 ({vwap_slope:+.2f}%) — 진입 금지")

        signal.reasons.insert(0, f"VWAP {current_vwap:,.0f}원, 편차 {deviation_pct:+.1f}%")
        signal.entry_score = max(0, min(100, score))
        signal.confidence = min(len(intraday_prices) / 30, 1.0)
        signal.scalp_tp_pct = min(max(1.5, abs(deviation_pct) * 1.5), 3.0)
        signal.scalp_sl_pct = max(-max(1.0, abs(deviation_pct)), -2.0)
        signal.hold_minutes = 10

        return signal

    def _fallback_daily_vwap(
        self,
        snapshot: StockSnapshot,
        ohlcv: Optional[pd.DataFrame],
        signal: ScalpingSignal,
    ) -> Optional[ScalpingSignal]:
        """분봉 없을 때 일봉 기반 간접 분석"""
        if ohlcv is None or len(ohlcv) < 5:
            return None

        # 전일 VWAP 근사: (고가+저가+종가)/3
        last = ohlcv.iloc[-1]
        approx_vwap = (last['high'] + last['low'] + last['close']) / 3

        # 당일: 시가/현재가 대비 위치
        price = snapshot.price
        open_price = snapshot.open
        intraday_range = snapshot.high - snapshot.low
        position_in_range = (
            (price - snapshot.low) / intraday_range * 100
            if intraday_range > 0 else 50
        )

        score = 35  # 2026-04-07: 50→35 하향
        if position_in_range < 30:
            score += 20
            signal.timing = "대기"  # 2026-04-07: 즉시→대기 (하락 중일 수 있음)
            signal.entry_trigger = "일중 저점 구간 — 반등 확인 필요"
        elif position_in_range < 50:
            score += 10
            signal.timing = "대기"
            signal.entry_trigger = "일중 중하단 — 적정 구간"
        elif position_in_range < 70:
            signal.timing = "대기"
            signal.entry_trigger = "일중 중상단 — 눌림 대기"
        else:
            score -= 15
            signal.timing = "관망"
            signal.entry_trigger = "일중 고점 구간 — 추격 위험"

        signal.reasons.append(f"일중 위치 {position_in_range:.0f}% (저→고)")
        signal.entry_score = max(0, min(100, score))
        signal.confidence = 0.5  # 간접 분석이므로 낮은 신뢰도
        signal.scalp_tp_pct = 2.0
        signal.scalp_sl_pct = -1.5
        signal.hold_minutes = 15
        return signal
