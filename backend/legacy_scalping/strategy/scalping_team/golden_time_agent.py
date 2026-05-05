"""
[Agent 7] 골든타임 전문가 (v2 — 2026-03-27 재설계)

시간대별 5-윈도우 체계로 스캘핑 최적 시간 판단.

리포트 기반 시간대 전략:
  - 골든타임 09:00~09:30: 갭상승 후 첫 눌림목 대기, 추격 매수 금지
  - 오전장 09:30~11:00: 눌림목 진입 최적 구간
  - 데드존 11:00~12:00: 신규 진입 금지, 이탈/익절만
  - 오후장 13:00~14:00: 안정적 진입 가능
  - 14:00 이후: 신규 진입 금지 (강제 청산 대비)
"""

import logging
from datetime import datetime
from typing import List, Optional

import pandas as pd

from strategy.scalping_team.base_agent import (
    BaseScalpingAgent, ScalpingSignal, StockSnapshot,
)

logger = logging.getLogger(__name__)


class GoldenTimeAgent(BaseScalpingAgent):

    @property
    def name(self) -> str:
        return "골든타임전문가"

    def analyze(
        self,
        snapshot: StockSnapshot,
        ohlcv: Optional[pd.DataFrame],
        intraday_prices: List[dict],
    ) -> Optional[ScalpingSignal]:

        signal = ScalpingSignal(
            agent_name=self.name, code=snapshot.code, name=snapshot.name)

        now = datetime.now()
        hour = now.hour
        minute = now.minute
        change_pct = snapshot.change_pct

        # ── 5-윈도우 체계 ──

        if hour == 9 and minute < 10:
            # 장 초반 10분: 극도의 변동성, 방향 불명
            time_score = 25
            signal.timing = "관망"
            signal.entry_trigger = "장 초반 10분 — 노이즈 극심, 진입 금지"
            signal.reasons.append("⚡ 09:00~09:10 — 방향 미확정, 대기 필수")
            signal.hold_minutes = 5

        elif hour == 9 and minute < 30:
            # 골든타임: 갭상승 후 첫 눌림목 대기
            time_score = 65
            signal.timing = "대기"
            signal.entry_trigger = "골든타임 — 갭상승 후 첫 눌림목 대기"
            signal.reasons.append(
                "🟡 09:10~09:30 골든타임 — 추격 금지, 눌림목만 진입")
            signal.hold_minutes = 15

            # 눌림목이 확인되면 즉시 진입 (고점 대비 3%+ 하락)
            if snapshot.high > 0:
                pullback_from_high = (
                    (snapshot.high - snapshot.price) / snapshot.high * 100)
                if pullback_from_high >= 3.0:
                    time_score = 80
                    signal.timing = "즉시"
                    signal.entry_trigger = (
                        f"골든타임 눌림목 확인 "
                        f"(고점 대비 -{pullback_from_high:.1f}%)")
                    signal.reasons.append(
                        f"🟢 갭상승 후 {pullback_from_high:.1f}% 눌림 — "
                        f"최적 진입 시점")

        elif hour == 9:
            # 오전장 09:30~10:00: 눌림목 진입 최적 구간
            time_score = 80
            signal.timing = "즉시"
            signal.entry_trigger = "오전장 — 눌림목 진입 최적 구간"
            signal.reasons.append(
                "🟢 09:30~10:00 — 추세 형성 + 눌림목 진입 최적")
            signal.hold_minutes = 15

        elif hour == 10 and minute < 60:
            # 2026-04-09: 10:00-11:00 완화 (매매 빈도 확대)
            # 50~41점 → 60~51점, 즉시 허용 (다른 필터에서 품질 관리)
            time_score = 60 - minute // 15 * 3  # 10:00=60, 10:45=51
            signal.timing = "즉시"
            signal.entry_trigger = "10시대 — 추세 확인 후 진입"
            signal.reasons.append(
                "🟡 10:00~11:00 — 유동성 높은 구간, 확인 후 진입")
            signal.hold_minutes = 10

        elif (hour == 11 and minute >= 30) or (hour == 12 and minute < 30):
            # 데드존 11:30~12:30: 신규 진입 금지 (2026-04-09: 2시간→1시간 축소)
            time_score = 15
            signal.timing = "관망"
            signal.entry_trigger = "데드존 — 신규 진입 금지, 이탈/익절만"
            signal.reasons.append(
                "😴 11:30~12:30 데드존 — 거래량/변동성 급감, 진입 금지")
            signal.hold_minutes = 20

        elif hour == 11 and minute < 30:
            # 2026-04-09: 11:00~11:30 오전장 후반 (데드존에서 해제)
            time_score = 50
            signal.timing = "즉시"
            signal.entry_trigger = "오전장 후반 — 추세 확인 후 진입"
            signal.reasons.append(
                "🟡 11:00~11:30 — 오전 마무리 구간, 확인 후 진입")
            signal.hold_minutes = 10

        elif hour == 12 and minute >= 30:
            # 2026-04-09: 12:30~13:00 오후장 조기 시작 (데드존에서 해제)
            time_score = 50
            signal.timing = "즉시"
            signal.entry_trigger = "오후장 조기 시작 — 거래량 회복 확인"
            signal.reasons.append(
                "🟡 12:30~13:00 — 오후 거래량 회복 구간")
            signal.hold_minutes = 10

        elif hour == 13:
            # 오후장 13:00~14:00: 안정적 진입 가능
            time_score = 55 + minute // 15 * 3  # 13:00=55, 13:45=64
            signal.timing = "즉시"
            signal.entry_trigger = "오후장 — 거래량 회복, 안정적 진입"
            signal.reasons.append(
                "📈 13:00~14:00 — 오후 추세 진입 가능")
            signal.hold_minutes = 10

        elif hour >= 14:
            # 14:00 이후: 신규 진입 금지
            time_score = 10
            signal.timing = "관망"
            signal.entry_trigger = "장 마감 임박 — 신규 진입 금지"
            signal.reasons.append(
                "🚫 14:00 이후 — 강제 청산 임박, 진입 불가")
            signal.hold_minutes = 5

        else:
            signal.timing = "관망"
            signal.entry_trigger = "장 외"
            time_score = 0

        # ── 진입 구간별 시간대 보정 ──
        # +10% 이상 종목의 오전 늦은 진입은 추격 위험
        if change_pct > 10 and hour >= 10:
            time_score = max(time_score - 15, 0)
            signal.reasons.append(
                f"⚠ +{change_pct:.1f}% 종목 10시 이후 진입 — 추격 위험 가중")

        # +15% 이상 종목은 09:30 이전 진입 불가
        if change_pct > 15 and hour == 9 and minute < 30:
            time_score = max(time_score - 20, 0)
            signal.timing = "관망"
            signal.reasons.append(
                f"⚠ +{change_pct:.1f}% 과열 — 골든타임이라도 추격 금지")

        # ── 과거 상승일 종가 패턴 ──
        if ohlcv is not None and len(ohlcv) >= 10:
            gains = []
            for i in range(-10, 0):
                day_change = (
                    (ohlcv.iloc[i]['close'] - ohlcv.iloc[i]['open'])
                    / ohlcv.iloc[i]['open'] * 100
                )
                if day_change > 2:
                    day_range = ohlcv.iloc[i]['high'] - ohlcv.iloc[i]['low']
                    if day_range > 0:
                        close_pos = (
                            (ohlcv.iloc[i]['close'] - ohlcv.iloc[i]['low'])
                            / day_range
                        )
                        gains.append(close_pos)

            if gains:
                avg_close_pos = sum(gains) / len(gains)
                if avg_close_pos > 0.7:
                    time_score += 5
                    signal.reasons.append(
                        f"과거 상승일 종가위치 {avg_close_pos:.0%} — "
                        f"고점 유지 경향")

        signal.entry_score = max(0, min(100, time_score))
        signal.confidence = 0.75  # 시간 기반은 객관적
        signal.scalp_tp_pct = 2.5 if hour < 11 else 1.5
        signal.scalp_sl_pct = -1.5

        return signal
