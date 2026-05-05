"""
[Agent 4] 돌파 확인 전문가 (v2 — 2026-04-03 고점돌파 추가)

가격 돌파의 유효성을 검증하여 허위 돌파(Fakeout) 필터링.

핵심 원리:
  - 전일 고가/저항선 돌파 후 3분 유지 = 유효 돌파
  - 돌파 시 거래량 3배 이상 = 진짜 매수세
  - 돌파 후 되돌아와서 지지 확인 = 리테스트 진입 기회

v2 추가 — 당일 고점 돌파 전략:
  - 장중 고점 근처 횡보(매물 소화) 후 돌파 = 강력 진입
  - 돌파 시점 거래량 서지 (직전 대비 2배+) = 진짜 돌파
  - 돌파 후 2~3틱 안착 확인 = 가짜 돌파 필터
  - 급등 직후 즉시 재돌파 시도 = 추격 위험 감점
"""

import logging
from typing import List, Optional

import numpy as np
import pandas as pd

from strategy.scalping_team.base_agent import (
    BaseScalpingAgent, ScalpingSignal, StockSnapshot,
)

logger = logging.getLogger(__name__)


class BreakoutConfirmAgent(BaseScalpingAgent):

    @property
    def name(self) -> str:
        return "돌파확인전문가"

    def analyze(
        self,
        snapshot: StockSnapshot,
        ohlcv: Optional[pd.DataFrame],
        intraday_prices: List[dict],
    ) -> Optional[ScalpingSignal]:

        signal = ScalpingSignal(
            agent_name=self.name, code=snapshot.code, name=snapshot.name)

        price = snapshot.price
        score = 40

        # ── 1. 당일 고점 돌파 분석 (신규) ──
        intraday_bo = self._check_intraday_breakout(
            snapshot, intraday_prices)

        if intraday_bo['detected']:
            score += intraday_bo['score']
            signal.reasons.extend(intraday_bo['reasons'])

            if intraday_bo['score'] >= 25:
                signal.timing = "즉시"
                signal.entry_trigger = intraday_bo['trigger']
            elif intraday_bo['score'] >= 10:
                signal.timing = "대기"
                signal.entry_trigger = intraday_bo['trigger']
            else:
                signal.timing = "관망"
                signal.entry_trigger = intraday_bo['trigger']

            # 고점 돌파 시 TP 확대 (상승폭 기대)
            signal.scalp_tp_pct = 3.0
            signal.scalp_sl_pct = -1.2
            signal.hold_minutes = 10

        # ── 2. 과거 저항선 돌파 분석 (기존 로직) ──
        resist_bo = self._check_resistance_breakout(
            snapshot, ohlcv, intraday_prices)

        if resist_bo:
            score += resist_bo['score']
            signal.reasons.extend(resist_bo['reasons'])

            # 당일 고점 돌파가 없으면 저항선 기반 타이밍
            if not intraday_bo['detected']:
                signal.timing = resist_bo['timing']
                signal.entry_trigger = resist_bo['trigger']
                signal.scalp_tp_pct = 2.5
                signal.scalp_sl_pct = -1.5
                signal.hold_minutes = 10

        # ── 3. 돌파 거래량 검증 ──
        if snapshot.volume_ratio >= 3:
            score += 15
            signal.reasons.append(
                f"돌파 거래량 {snapshot.volume_ratio:.1f}배 — 유효 돌파")
        elif snapshot.volume_ratio >= 1.5:
            score += 5
            signal.reasons.append(
                f"돌파 거래량 {snapshot.volume_ratio:.1f}배 — 보통")
        else:
            score -= 10
            signal.reasons.append(
                f"돌파 거래량 {snapshot.volume_ratio:.1f}배 — 허위 돌파 우려")

        # 아무 돌파도 감지되지 않은 경우
        if not intraday_bo['detected'] and not resist_bo:
            signal.timing = "대기"
            signal.entry_trigger = "돌파 미감지 — 대기"
            signal.confidence = 0.3
            signal.scalp_tp_pct = 2.0
            signal.scalp_sl_pct = -1.5
            signal.hold_minutes = 10

        signal.entry_score = max(0, min(100, score))
        signal.confidence = min(
            0.35
            + (0.25 if intraday_bo['detected'] and intraday_bo['score'] >= 20 else 0)
            + (0.15 if resist_bo and resist_bo['score'] >= 15 else 0)
            + (0.2 if snapshot.volume_ratio >= 3 else
               0.1 if snapshot.volume_ratio >= 1.5 else 0),
            0.95,
        )

        return signal

    # ================================================================
    # 당일 고점 돌파 분석 (NEW)
    # ================================================================
    def _check_intraday_breakout(
        self,
        snapshot: StockSnapshot,
        intraday_prices: List[dict],
    ) -> dict:
        """당일 장중 고점 돌파 여부 및 품질 판정"""
        result = {
            'detected': False, 'score': 0,
            'reasons': [], 'trigger': '',
        }

        price = snapshot.price
        high = snapshot.high
        open_price = snapshot.open

        # 고점이 의미 없으면 스킵 (시가 대비 최소 2% 상승 필요)
        if high <= 0 or open_price <= 0:
            return result
        rally_from_open = (high - open_price) / open_price * 100
        if rally_from_open < 2.0:
            return result

        # 현재가가 고점 근처(±0.3%) 또는 돌파 중인지
        margin_from_high = (price - high) / high * 100

        # 2026-04-07: -0.5% → -0.2% 타이트닝 (느슨한 감지 → false positive 원인)
        if margin_from_high < -0.2:
            # 고점 대비 0.2% 이상 아래 → 돌파 아님
            return result

        result['detected'] = True
        bo_score = 0

        # ── A. 고점 돌파 상태 판정 ──
        if margin_from_high >= 0.3:
            # 고점 확실히 돌파 (+0.3% 이상)
            bo_score += 15
            state = "돌파 확인"
        elif margin_from_high >= 0:
            # 고점 터치/소폭 돌파 (0~0.3%)
            bo_score += 10
            state = "돌파 직후"
        else:
            # 고점 임박 (-0.2% ~ 0%) — 2026-04-07: 점수 축소
            bo_score += 0
            state = "고점 임박"

        # ── B. 고점 근처 횡보(매물 소화) 시간 체크 ──
        consolidation_ticks = 0
        if len(intraday_prices) >= 5:
            high_zone_lower = high * 0.997  # 고점 -0.3%
            for t in reversed(intraday_prices):
                if high_zone_lower <= t['price'] <= high * 1.005:
                    consolidation_ticks += 1
                else:
                    break

            if consolidation_ticks >= 5:
                # 5틱+ 횡보 후 돌파 = 매물 소화 완료 → 강력
                bo_score += 15
                result['reasons'].append(
                    f"[고점돌파] 고점 근처 {consolidation_ticks}틱 횡보 "
                    f"— 매물 소화 완료")
            elif consolidation_ticks >= 3:
                bo_score += 8
                result['reasons'].append(
                    f"[고점돌파] 고점 근처 {consolidation_ticks}틱 횡보")
            elif consolidation_ticks == 0 and margin_from_high >= 0:
                # 급등 직후 바로 신고가 → 추격 위험
                bo_score -= 5
                result['reasons'].append(
                    "[고점돌파] 급등 직후 고점 돌파 — 추격 주의")

        # ── C. 돌파 시점 거래량 서지 ──
        if len(intraday_prices) >= 8:
            volumes = [t.get('volume', 0) for t in intraday_prices]
            recent_vol = volumes[-3:]  # 최근 3틱
            prior_vol = volumes[-8:-3]  # 직전 5틱

            avg_recent = np.mean(recent_vol) if recent_vol else 0
            avg_prior = np.mean(prior_vol) if prior_vol else 0

            if avg_prior > 0:
                vol_surge = avg_recent / avg_prior
                if vol_surge >= 2.0:
                    bo_score += 10
                    result['reasons'].append(
                        f"[고점돌파] 거래량 서지 {vol_surge:.1f}배 — 진짜 돌파")
                elif vol_surge >= 1.3:
                    bo_score += 3
                    result['reasons'].append(
                        f"[고점돌파] 거래량 소폭 증가 {vol_surge:.1f}배")
                elif vol_surge < 0.7:
                    bo_score -= 5
                    result['reasons'].append(
                        f"[고점돌파] 거래량 감소 {vol_surge:.1f}배 — 가짜 돌파 우려")

        # ── D. 돌파 후 안착 확인 (최근 틱이 고점 위 유지) ──
        if len(intraday_prices) >= 3 and margin_from_high >= 0:
            last_3 = [t['price'] for t in intraday_prices[-3:]]
            above_count = sum(1 for p in last_3 if p >= high)

            if above_count >= 3:
                bo_score += 8
                result['reasons'].append(
                    "[고점돌파] 최근 3틱 고점 위 안착 — 강한 돌파")
            elif above_count >= 2:
                bo_score += 3
                result['reasons'].append(
                    "[고점돌파] 최근 3틱 중 2틱 고점 위")
            elif above_count == 0:
                bo_score -= 8
                result['reasons'].append(
                    "[고점돌파] 돌파 후 즉시 밀림 — 가짜 돌파")

        # ── E-0. 직전 가격 방향 체크 (2026-04-07) ──
        if len(intraday_prices) >= 5:
            last5_prices = [t['price'] for t in intraday_prices[-5:]]
            if last5_prices[-1] < last5_prices[0]:
                price_drop = (last5_prices[-1] - last5_prices[0]) / last5_prices[0] * 100
                if price_drop < -0.3:
                    bo_score -= 10
                    result['reasons'].append(
                        f"[고점돌파] 직전 5틱 하락 {price_drop:.1f}% — 돌파 신뢰도 낮음")

        # ── E. 상승 가속도 (고점 돌파 후 추가 상승 모멘텀) ──
        if len(intraday_prices) >= 5 and margin_from_high >= 0:
            last_5 = [t['price'] for t in intraday_prices[-5:]]
            # 연속 상승틱 카운트
            up_streak = 0
            for i in range(len(last_5) - 1, 0, -1):
                if last_5[i] >= last_5[i - 1]:
                    up_streak += 1
                else:
                    break
            if up_streak >= 4:
                bo_score += 5
                result['reasons'].append(
                    f"[고점돌파] {up_streak}틱 연속 상승 — 모멘텀 강함")

        # 트리거 메시지 조립
        result['score'] = bo_score
        result['trigger'] = (
            f"당일 고점 {high:,.0f}원 {state} "
            f"(마진 {margin_from_high:+.1f}%, "
            f"시가 대비 +{rally_from_open:.1f}%)"
        )

        return result

    # ================================================================
    # 과거 저항선 돌파 분석 (기존 리팩토링)
    # ================================================================
    def _check_resistance_breakout(
        self,
        snapshot: StockSnapshot,
        ohlcv: Optional[pd.DataFrame],
        intraday_prices: List[dict],
    ) -> Optional[dict]:
        """과거 일봉 저항선 돌파 판정"""
        if ohlcv is None or len(ohlcv) < 5:
            return None

        resistances = self._find_resistances(ohlcv)
        if not resistances:
            return None

        price = snapshot.price
        broken = [(r, (price - r) / r * 100) for r in resistances if price > r]
        unbroken = [(r, (r - price) / price * 100)
                    for r in resistances if price <= r]

        result = {'score': 0, 'reasons': [], 'timing': '대기', 'trigger': ''}

        if broken:
            nearest_broken = max(broken, key=lambda x: x[0])
            breakout_margin = nearest_broken[1]

            if breakout_margin < 0.5:
                result['score'] += 20
                result['timing'] = "즉시"
                result['trigger'] = (
                    f"저항선 {nearest_broken[0]:,.0f}원 돌파 직후 "
                    f"(+{breakout_margin:.1f}%) — 리테스트 진입")
            elif breakout_margin < 2.0:
                result['score'] += 25
                result['timing'] = "즉시"
                result['trigger'] = (
                    f"저항선 {nearest_broken[0]:,.0f}원 돌파 확인 "
                    f"(+{breakout_margin:.1f}%)")
            else:
                result['score'] -= 5
                result['timing'] = "눌림목대기"
                result['trigger'] = (
                    f"저항선 {nearest_broken[0]:,.0f}원 돌파 후 "
                    f"+{breakout_margin:.1f}% 상승 — 추격 주의")

            result['reasons'].append(
                f"돌파 저항: {nearest_broken[0]:,.0f}원 "
                f"(마진 +{breakout_margin:.1f}%)")

            # 분봉 기반 돌파 유지력
            if len(intraday_prices) >= 5:
                broken_level = nearest_broken[0]
                prices_above = sum(
                    1 for t in intraday_prices[-10:]
                    if t['price'] > broken_level
                )
                hold_ratio = prices_above / min(len(intraday_prices[-10:]), 10)
                if hold_ratio >= 0.8:
                    result['score'] += 10
                    result['reasons'].append(
                        f"돌파 유지력 {hold_ratio:.0%} — 강한 돌파")
                elif hold_ratio < 0.5:
                    result['score'] -= 10
                    result['reasons'].append(
                        f"돌파 유지력 {hold_ratio:.0%} — 불안정")
        else:
            nearest_resist = min(unbroken, key=lambda x: x[1])
            dist = nearest_resist[1]

            if dist < 1.0:
                result['score'] += 10
                result['timing'] = "대기"
                result['trigger'] = (
                    f"저항선 {nearest_resist[0]:,.0f}원 임박 "
                    f"(-{dist:.1f}%) — 돌파 시 진입")
            else:
                result['score'] -= 5
                result['timing'] = "대기"
                result['trigger'] = (
                    f"저항선 {nearest_resist[0]:,.0f}원까지 {dist:.1f}% 남음")

            result['reasons'].append(
                f"미돌파 저항: {nearest_resist[0]:,.0f}원 ({dist:.1f}%)")

        return result

    def _find_resistances(self, ohlcv: pd.DataFrame) -> List[float]:
        """최근 20일 고점 기반 저항선 식별"""
        if len(ohlcv) < 5:
            return []

        highs = ohlcv['high'].values[-20:]
        resistances = []

        for period in [3, 5, 10, 20]:
            if len(highs) >= period:
                resistances.append(float(np.max(highs[-period:])))

        # 중복 제거 (1% 이내는 동일 저항선)
        unique = []
        for r in sorted(set(resistances)):
            if not unique or (r - unique[-1]) / unique[-1] > 0.01:
                unique.append(r)

        return unique
