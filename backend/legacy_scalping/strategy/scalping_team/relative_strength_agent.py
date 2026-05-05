"""
[Agent 8] 상대강도 전문가

동종 상승 종목 대비 상대적 강도를 분석하여
가장 강한 종목/약한 종목 구분.

핵심 원리:
  - 상승률 상위 종목 중에서도 "가장 강한 놈"이 더 오른다
  - 거래량 대비 상승률이 높은 종목 = 효율적 상승 (매수 저항 적음)
  - 같은 섹터 내에서 가장 먼저/많이 오르는 종목 = 주도주
"""

import logging
from typing import List, Optional

import pandas as pd

from strategy.scalping_team.base_agent import (
    BaseScalpingAgent, ScalpingSignal, StockSnapshot,
)

logger = logging.getLogger(__name__)


class RelativeStrengthAgent(BaseScalpingAgent):
    """
    다른 에이전트와 달리 단일 종목이 아닌 전체 후보군 대비
    상대 순위를 고려한다. coordinator에서 all_snapshots를 주입.
    """

    def __init__(self):
        self._all_snapshots: List[StockSnapshot] = []

    def set_universe(self, snapshots: List[StockSnapshot]):
        """전체 후보군 주입 (coordinator에서 호출)"""
        self._all_snapshots = snapshots

    @property
    def name(self) -> str:
        return "상대강도전문가"

    def analyze(
        self,
        snapshot: StockSnapshot,
        ohlcv: Optional[pd.DataFrame],
        intraday_prices: List[dict],
    ) -> Optional[ScalpingSignal]:

        if not self._all_snapshots:
            return None

        signal = ScalpingSignal(
            agent_name=self.name, code=snapshot.code, name=snapshot.name)

        n = len(self._all_snapshots)
        score = 40

        # ── 1. 상승률 순위 ──
        sorted_by_change = sorted(
            self._all_snapshots, key=lambda s: s.change_pct, reverse=True)
        change_rank = next(
            (i + 1 for i, s in enumerate(sorted_by_change) if s.code == snapshot.code),
            n
        )
        change_percentile = (1 - change_rank / n) * 100

        if change_percentile >= 80:
            score += 15
            signal.reasons.append(
                f"상승률 상위 {100-change_percentile:.0f}% ({change_rank}/{n}위)")
        elif change_percentile >= 50:
            score += 5
            signal.reasons.append(
                f"상승률 중상위 ({change_rank}/{n}위)")
        else:
            score -= 10
            signal.reasons.append(
                f"상승률 하위 ({change_rank}/{n}위)")

        # ── 2. 거래량 효율 (상승률 / 거래량비율) ──
        vol_ratio = max(snapshot.volume_ratio, 0.1)
        efficiency = snapshot.change_pct / vol_ratio
        efficiencies = [
            s.change_pct / max(s.volume_ratio, 0.1)
            for s in self._all_snapshots
        ]
        avg_efficiency = sum(efficiencies) / len(efficiencies)

        if efficiency > avg_efficiency * 1.3:
            score += 15
            signal.reasons.append(
                f"상승 효율 {efficiency:.2f} (평균 {avg_efficiency:.2f}의 "
                f"{efficiency/avg_efficiency:.1f}배) — 저항 적음"
            )
        elif efficiency < avg_efficiency * 0.7:
            score -= 10
            signal.reasons.append(
                f"상승 효율 {efficiency:.2f} (평균 대비 낮음) — 비효율적 상승"
            )

        # ── 3. 거래대금 순위 ──
        sorted_by_value = sorted(
            self._all_snapshots, key=lambda s: s.trade_value, reverse=True)
        value_rank = next(
            (i + 1 for i, s in enumerate(sorted_by_value) if s.code == snapshot.code),
            n
        )

        if value_rank <= 3:
            score += 10
            signal.reasons.append(f"거래대금 {value_rank}위 — 시장 주도주")
        elif value_rank <= n * 0.3:
            score += 5
            signal.reasons.append(f"거래대금 상위 ({value_rank}/{n}위)")

        # ── 4. 종합 점수 순위 (주도주 점수) ──
        sorted_by_score = sorted(
            self._all_snapshots, key=lambda s: s.score, reverse=True)
        score_rank = next(
            (i + 1 for i, s in enumerate(sorted_by_score) if s.code == snapshot.code),
            n
        )

        if score_rank <= 3:
            score += 10
            signal.reasons.append(f"주도주 점수 {score_rank}위 — 최상위 주도주")
        elif score_rank <= 5:
            score += 5
            signal.reasons.append(f"주도주 점수 {score_rank}위")

        # ── 5. 일봉 상대강도 (RSI 간접) ──
        if ohlcv is not None and len(ohlcv) >= 14:
            close = ohlcv['close'].values
            deltas = [close[i] - close[i-1] for i in range(1, len(close))]
            gains = [max(d, 0) for d in deltas[-14:]]
            losses = [abs(min(d, 0)) for d in deltas[-14:]]
            avg_gain = sum(gains) / 14
            avg_loss = sum(losses) / 14

            if avg_loss > 0:
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
            else:
                rsi = 100

            if rsi >= 70:
                score += 5
                signal.reasons.append(f"RSI {rsi:.0f} — 강세 모멘텀")
            elif rsi <= 30:
                score -= 5
                signal.reasons.append(f"RSI {rsi:.0f} — 약세 구간")

        # ── 종합 ──
        signal.entry_score = max(0, min(100, score))
        signal.confidence = 0.5 + min(n / 20, 0.3)

        if score >= 65:
            signal.timing = "즉시"
            signal.entry_trigger = "상대강도 최상위 — 주도주 진입"
        elif score >= 50:
            signal.timing = "대기"
            signal.entry_trigger = "상대강도 중위 — 선별 진입"
        else:
            signal.timing = "관망"
            signal.entry_trigger = "상대강도 하위 — 더 강한 종목 우선"

        signal.scalp_tp_pct = 3.0 if change_percentile >= 80 else 2.0
        signal.scalp_sl_pct = -1.5
        signal.hold_minutes = 10

        return signal
