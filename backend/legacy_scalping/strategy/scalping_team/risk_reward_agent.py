"""
[Agent 9] 리스크/보상 전문가

진입 시점의 리스크 대비 보상 비율(R:R)을 정밀 계산.

핵심 원리:
  - R:R >= 2:1 이상일 때만 진입 가치 있음
  - 지지선까지 거리 = 리스크, 저항선까지 거리 = 보상
  - ATR 기반 목표가/손절가로 실현 가능한 R:R 산출
  - 슬리피지와 수수료 반영한 순수익 기반 R:R
"""

import logging
from typing import List, Optional

import numpy as np
import pandas as pd

from strategy.scalping_team.base_agent import (
    BaseScalpingAgent, ScalpingSignal, StockSnapshot,
)

logger = logging.getLogger(__name__)


class RiskRewardAgent(BaseScalpingAgent):

    @property
    def name(self) -> str:
        return "리스크보상전문가"

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

        price = snapshot.price
        close = ohlcv['close'].values
        high = ohlcv['high'].values
        low = ohlcv['low'].values

        # ── 1. ATR 계산 (14일) ──
        tr_values = []
        for i in range(1, min(15, len(close))):
            tr = max(
                high[-i] - low[-i],
                abs(high[-i] - close[-i - 1]),
                abs(low[-i] - close[-i - 1]),
            )
            tr_values.append(tr)

        atr = np.mean(tr_values) if tr_values else price * 0.02
        atr_pct = atr / price * 100

        # ── 2. 지지선/저항선 기반 R:R ──
        # 지지: 전일 저가, 당일 시가, 전일 종가
        support = max(snapshot.low, snapshot.open * 0.98, snapshot.prev_close)
        # 저항: 당일 고가 확장, 과거 고점
        recent_high = float(np.max(high[-5:]))
        resistance = max(snapshot.high, recent_high) * 1.02

        risk = max(price - support, price * 0.01)    # 최소 1%
        reward = max(resistance - price, price * 0.01)
        rr_ratio = reward / risk if risk > 0 else 0

        score = 30

        # ── 3. R:R 평가 ──
        if rr_ratio >= 3:
            score += 30
            signal.reasons.append(f"R:R {rr_ratio:.1f}:1 — 매우 유리한 진입")
        elif rr_ratio >= 2:
            score += 20
            signal.reasons.append(f"R:R {rr_ratio:.1f}:1 — 양호한 진입")
        elif rr_ratio >= 1.5:
            score += 10
            signal.reasons.append(f"R:R {rr_ratio:.1f}:1 — 보통")
        elif rr_ratio >= 1:
            score += 0
            signal.reasons.append(f"R:R {rr_ratio:.1f}:1 — 불리한 진입")
        else:
            score -= 20
            signal.reasons.append(f"R:R {rr_ratio:.1f}:1 — 매우 불리")

        signal.reasons.append(
            f"지지 {support:,.0f}원 / 저항 {resistance:,.0f}원")

        # ── 4. ATR 기반 변동성 대비 수익 가능성 ──
        # 스캘핑 목표: ATR의 50-100%
        potential_gain_pct = atr_pct * 0.7  # ATR의 70% 수익 목표
        potential_loss_pct = atr_pct * 0.4  # ATR의 40% 손절

        atr_rr = potential_gain_pct / potential_loss_pct if potential_loss_pct > 0 else 0

        if atr_pct >= 3:
            score += 10
            signal.reasons.append(
                f"ATR {atr_pct:.1f}% — 스캘핑에 충분한 변동성")
        elif atr_pct >= 2:
            score += 5
            signal.reasons.append(f"ATR {atr_pct:.1f}% — 적정 변동성")
        elif atr_pct < 1:
            score -= 15
            signal.reasons.append(f"ATR {atr_pct:.1f}% — 변동성 부족, 스캘핑 부적합")

        # ── 5. 수수료/슬리피지 반영 순수익 R:R ──
        # 실제 비용: 매수0.015% + 매도0.015% + 거래세0.18% = 0.21%
        # + 최유리지정가 슬리피지 추정 (유동성에 따라 0.2~0.5%)
        slippage_est = 0.3 if snapshot.volume_ratio >= 5 else 0.5
        commission = 0.21 + slippage_est
        net_gain = potential_gain_pct - commission
        net_loss = potential_loss_pct + commission
        net_rr = net_gain / net_loss if net_loss > 0 else 0

        if net_rr >= 2:
            score += 10
            signal.reasons.append(
                f"순수익 R:R {net_rr:.1f}:1 (수수료 반영) — 수익 구조 양호")
        elif net_rr < 1:
            score -= 10
            signal.reasons.append(
                f"순수익 R:R {net_rr:.1f}:1 — 수수료 감안 시 비효율")

        # ── 6. 일중 위치 기반 리스크 ──
        day_range = snapshot.high - snapshot.low
        if day_range > 0:
            position_ratio = (price - snapshot.low) / day_range
            if position_ratio > 0.85:
                score -= 10
                signal.reasons.append(
                    f"일중 고점 {position_ratio:.0%} — 하방 리스크 큼")
            elif position_ratio < 0.3:
                score += 10
                signal.reasons.append(
                    f"일중 저점 {position_ratio:.0%} — 하방 리스크 작음")

        # ── 종합 ──
        signal.entry_score = max(0, min(100, score))
        signal.confidence = min(0.5 + rr_ratio * 0.1, 0.95)

        if score >= 65:
            signal.timing = "즉시"
            signal.entry_trigger = f"R:R {rr_ratio:.1f}:1 유리 — 진입 추천"
        elif score >= 45:
            signal.timing = "대기"
            signal.entry_trigger = f"R:R {rr_ratio:.1f}:1 보통 — 가격 개선 대기"
        else:
            signal.timing = "관망"
            signal.entry_trigger = f"R:R {rr_ratio:.1f}:1 불리 — 진입 비추"

        # 스캘핑 파라미터 (리포트 권장 범위 내 캡 적용)
        signal.scalp_tp_pct = min(round(potential_gain_pct, 1), 3.0)
        signal.scalp_sl_pct = max(round(-potential_loss_pct, 1), -2.0)
        signal.hold_minutes = max(5, min(int(30 / max(atr_pct, 1)), 20))
        signal.entry_price_zone = support + (price - support) * 0.3

        return signal
