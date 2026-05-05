"""
[Agent 5] 캔들 패턴 전문가

당일 분봉/일봉 캔들 패턴으로 반전/지속 시그널 판별.

핵심 원리:
  - 양봉 장악형(Bullish Engulfing) = 강한 매수 전환
  - 망치형(Hammer) = 하단 지지 확인, 반등 시작
  - 도지(Doji) 후 양봉 = 방향 결정 후 상승
  - 윗꼬리 긴 캔들 = 매도 압력, 진입 회피
"""

import logging
from typing import List, Optional

import pandas as pd

from strategy.scalping_team.base_agent import (
    BaseScalpingAgent, ScalpingSignal, StockSnapshot,
)

logger = logging.getLogger(__name__)


class CandlePatternAgent(BaseScalpingAgent):

    @property
    def name(self) -> str:
        return "캔들패턴전문가"

    def analyze(
        self,
        snapshot: StockSnapshot,
        ohlcv: Optional[pd.DataFrame],
        intraday_prices: List[dict],
    ) -> Optional[ScalpingSignal]:

        if ohlcv is None or len(ohlcv) < 3:
            return None

        signal = ScalpingSignal(
            agent_name=self.name, code=snapshot.code, name=snapshot.name)

        patterns_found = []
        score = 35  # 2026-04-07: 40→35 하향 (false positive 감소)

        # ── 1. 당일 캔들 분석 (실시간) ──
        o, h, l, c = snapshot.open, snapshot.high, snapshot.low, snapshot.price
        body = abs(c - o)
        full_range = h - l if h > l else 1
        upper_shadow = h - max(o, c)
        lower_shadow = min(o, c) - l

        # 양봉/음봉
        is_bullish = c > o

        # ── 2026-04-07: 윗꼬리 비율 분석 (상단 매도 압력 감지) ──
        upper_wick_pct = upper_shadow / full_range * 100 if full_range > 0 else 0
        if upper_wick_pct >= 50:
            score -= 20
            patterns_found.append(
                f"🚨 윗꼬리 {upper_wick_pct:.0f}% — 강한 매도 압력, 진입 회피")
        elif upper_wick_pct >= 35:
            score -= 10
            patterns_found.append(
                f"⚠️ 윗꼬리 {upper_wick_pct:.0f}% — 상단 저항 주의")

        # ── 2026-04-07: 음봉 진입 감점 (entry candle must be bullish) ──
        if not is_bullish:
            score -= 10
            patterns_found.append("🔴 현재 음봉 — 매수 시그널 약화")

        # 패턴 1: 망치형 (하꼬리 긴 양봉)
        if lower_shadow > body * 2 and upper_shadow < body * 0.5 and is_bullish:
            score += 15
            patterns_found.append("🔨 망치형 — 하단 지지 확인, 반등 기대")

        # 패턴 2: 역망치형 (윗꼬리 긴 양봉) — 약한 매수
        if upper_shadow > body * 2 and lower_shadow < body * 0.5 and is_bullish:
            score -= 5
            patterns_found.append("⚠️ 역망치형 — 상단 저항 존재")

        # 패턴 3: 교수형 (윗꼬리 긴 음봉) — 매도 신호
        if upper_shadow > body * 2 and not is_bullish:
            score -= 15
            patterns_found.append("🔴 교수형 — 매도 압력 강함")

        # 패턴 4: 강한 양봉 (몸통이 전체의 70% 이상)
        if is_bullish and body > full_range * 0.7:
            score += 10
            patterns_found.append("💪 강한 양봉 — 매수 우위")

        # 패턴 5: 도지 (몸통 매우 작음)
        if body < full_range * 0.1:
            patterns_found.append("⏸️ 도지 — 방향 미결정, 다음 캔들 확인 필요")

        # ── 2. 전일 캔들 조합 분석 ──
        prev = ohlcv.iloc[-1]
        prev2 = ohlcv.iloc[-2] if len(ohlcv) >= 3 else None

        prev_body = abs(prev['close'] - prev['open'])
        prev_bullish = prev['close'] > prev['open']

        # 패턴 6: 양봉 장악형 (전일 음봉 → 당일 큰 양봉)
        if (not prev_bullish and is_bullish
                and c > prev['open'] and o < prev['close']):
            score += 20
            patterns_found.append("🟢 양봉 장악형 — 강한 매수 전환")

        # 패턴 7: 상승 연속 (3일 연속 양봉)
        if prev2 is not None:
            prev2_bullish = prev2['close'] > prev2['open']
            if prev2_bullish and prev_bullish and is_bullish:
                score += 10
                patterns_found.append("📈 3연속 양봉 — 추세 지속")

        # 패턴 8: 갭상승 양봉
        if o > prev['high']:
            gap_pct = (o - prev['high']) / prev['high'] * 100
            if is_bullish:
                score += 10
                patterns_found.append(f"⬆️ 갭상승 +{gap_pct:.1f}% 양봉 — 강세 시작")
            else:
                score -= 5
                patterns_found.append(f"⬆️ 갭상승 +{gap_pct:.1f}% 음봉 — 갭 메우기 우려")

        # ── 3. 캔들 위치 (일봉 레인지 대비) ──
        if len(ohlcv) >= 5:
            recent_highs = ohlcv['high'].values[-5:]
            recent_lows = ohlcv['low'].values[-5:]
            range_high = max(recent_highs)
            range_low = min(recent_lows)
            total_range = range_high - range_low

            if total_range > 0:
                position = (c - range_low) / total_range * 100
                if position > 90:
                    score -= 5
                    patterns_found.append(f"📍 5일 레인지 상단 {position:.0f}% — 단기 과열")
                elif position < 30:
                    score += 5
                    patterns_found.append(f"📍 5일 레인지 하단 {position:.0f}% — 반등 여지")

        # ── 종합 ──
        if not patterns_found:
            patterns_found.append("특이 캔들 패턴 없음")

        signal.reasons = patterns_found
        signal.entry_score = max(0, min(100, score))
        signal.confidence = min(0.4 + len(patterns_found) * 0.08, 0.9)

        # 타이밍 결정
        if score >= 65:
            signal.timing = "즉시"
            signal.entry_trigger = "캔들 패턴 매수 신호 — 적극 진입"
        elif score >= 50:
            signal.timing = "대기"
            signal.entry_trigger = "캔들 패턴 중립 — 추가 확인 필요"
        else:
            signal.timing = "관망"
            signal.entry_trigger = "캔들 패턴 부정적 — 진입 회피"

        signal.scalp_tp_pct = 2.5
        signal.scalp_sl_pct = -1.5
        signal.hold_minutes = 10

        return signal
