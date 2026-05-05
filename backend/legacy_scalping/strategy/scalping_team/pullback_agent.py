"""
[Agent 3] 눌림목 전문가 (v2 — 2026-03-27 재설계)

급등 후 조정(눌림목) 구간에서 최적의 재진입 타이밍 포착.

핵심 원리 (리포트 기반):
  - 급등 후 30-50% 되돌림 = 최적 재진입 구간 (피보나치)
  - 눌림 중 거래량 감소 = 매도 소진 → 반등 임박
  - 5일 이동평균선 위에서 가격 횡보 = 건전한 눌림
  - 전일 고가/당일 시가가 지지선 역할
  - 체결강도 200%+ = 매수 우위

눌림목 진입 5조건 (모두 충족 시 최고 점수):
  1. 상승폭의 30~50% 되돌림 후 안정
  2. 5일 이동평균선 위, 거래량 줄며 가격 횡보
  3. 체결강도 200% 이상 + 매수벽 존재
  4. 거래대금 500억 이상
  5. 급등 사유 명확 (테마 지속 가능)
"""

import logging
from typing import List, Optional

import numpy as np
import pandas as pd

from strategy.scalping_team.base_agent import (
    BaseScalpingAgent, ScalpingSignal, StockSnapshot,
)

logger = logging.getLogger(__name__)


class PullbackAgent(BaseScalpingAgent):

    @property
    def name(self) -> str:
        return "눌림목전문가"

    def analyze(
        self,
        snapshot: StockSnapshot,
        ohlcv: Optional[pd.DataFrame],
        intraday_prices: List[dict],
    ) -> Optional[ScalpingSignal]:

        signal = ScalpingSignal(
            agent_name=self.name, code=snapshot.code, name=snapshot.name)

        price = snapshot.price
        open_price = snapshot.open
        high = snapshot.high
        low = snapshot.low

        # ── 1. 당일 고점 대비 되돌림 분석 ──
        if high <= open_price or high == low:
            signal.entry_score = 25
            signal.timing = "대기"
            signal.reasons.append("고점 미형성 — 눌림목 판단 불가")
            signal.confidence = 0.3
            return signal

        # 고점 대비 되돌림 비율
        rally = high - open_price
        pullback = high - price
        pullback_ratio = pullback / rally if rally > 0 else 0
        rally_pct = rally / open_price * 100 if open_price > 0 else 0

        # ── 1.5. 반등 확인 (직전 N분봉 기반) ──
        # 핵심: 가격이 아직 떨어지고 있는지, 반등을 시작했는지 판별
        bounce_confirmed = False
        still_falling = False
        bounce_strength = 0.0  # 반등 강도 (%)

        if len(intraday_prices) >= 5:
            prices_recent = [t['price'] for t in intraday_prices[-5:]]
            # 직전 5틱 중 최저점 위치
            min_idx = prices_recent.index(min(prices_recent))
            current = prices_recent[-1]
            recent_low = min(prices_recent)

            # 2026-04-09: 반등 확인 완화 (2틱→1틱, 매매 빈도 확대)
            # 최저점 이후 1틱 이상 상승 + 반등 강도 0.2% 이상
            if min_idx < len(prices_recent) - 1 and current > recent_low:
                # 최저점 이후 연속 상승 틱 수 확인
                post_low = prices_recent[min_idx:]
                consecutive_up = 0
                for j in range(1, len(post_low)):
                    if post_low[j] > post_low[j - 1]:
                        consecutive_up += 1
                    else:
                        break

                bounce_strength = (current - recent_low) / recent_low * 100
                if bounce_strength >= 0.2 and consecutive_up >= 1:
                    bounce_confirmed = True

            # 하락 지속: 직전 3틱이 연속 하락
            if len(prices_recent) >= 3:
                last3 = prices_recent[-3:]
                if last3[0] > last3[1] > last3[2]:
                    still_falling = True

        # 더 넓은 구간도 체크 (10틱)
        if len(intraday_prices) >= 10 and not bounce_confirmed:
            prices_10 = [t['price'] for t in intraday_prices[-10:]]
            min_10 = min(prices_10)
            min_10_idx = prices_10.index(min_10)
            # 최저점이 최근 3틱 이전이고 현재가가 최저점 대비 +0.3% 이상
            if min_10_idx <= 6 and prices_10[-1] > min_10:
                bounce_10 = (prices_10[-1] - min_10) / min_10 * 100
                if bounce_10 >= 0.5:
                    bounce_confirmed = True
                    bounce_strength = bounce_10

        # ── 2. 피보나치 되돌림 구간 판정 (30~50% 최적) ──
        score = 25  # 기본
        conditions_met = 0  # 5조건 충족 카운터

        if pullback_ratio < 0.15:
            score += 0
            signal.timing = "눌림목대기"
            signal.entry_trigger = (
                f"고점 근처 (되돌림 {pullback_ratio:.0%}) — 눌림 대기")
        elif pullback_ratio < 0.3:
            score += 15
            signal.timing = "대기"
            signal.entry_trigger = (
                f"얕은 눌림 {pullback_ratio:.0%} — 30% 되돌림 대기")
        elif pullback_ratio <= 0.5:
            # ★ 최적 구간 (30~50%) — 반등 확인 필수
            conditions_met += 1
            if bounce_confirmed:
                score += 30
                signal.timing = "즉시"
                signal.entry_trigger = (
                    f"최적 눌림목 {pullback_ratio:.0%} + 반등 확인 "
                    f"(+{bounce_strength:.1f}%) — 적극 진입")
            elif still_falling:
                score += 5
                signal.timing = "눌림목대기"
                signal.entry_trigger = (
                    f"최적 구간 {pullback_ratio:.0%}이나 하락 지속 — 반등 대기")
            else:
                score += 15
                signal.timing = "대기"
                signal.entry_trigger = (
                    f"최적 구간 {pullback_ratio:.0%} — 반등 신호 대기")
            signal.reasons.append(
                f"[조건1] 30~50% 되돌림 충족 ({pullback_ratio:.0%})")
        elif pullback_ratio <= 0.618:
            # 깊은 눌림 — 반등 확인 없으면 진입 금지
            if bounce_confirmed and bounce_strength >= 0.5:
                score += 15
                signal.timing = "즉시"
                signal.entry_trigger = (
                    f"깊은 눌림 {pullback_ratio:.0%} + 강한 반등 "
                    f"(+{bounce_strength:.1f}%) — 진입")
            elif bounce_confirmed:
                score += 5
                signal.timing = "대기"
                signal.entry_trigger = (
                    f"깊은 눌림 {pullback_ratio:.0%} + 약한 반등 — 확인 대기")
            else:
                score -= 5
                signal.timing = "관망"
                signal.entry_trigger = (
                    f"깊은 눌림 {pullback_ratio:.0%} 반등 없음 — 관망")
        else:
            score -= 15
            signal.timing = "관망"
            signal.entry_trigger = (
                f"과도한 되돌림 {pullback_ratio:.0%} — 추세 훼손")

        # 반등 확인 보너스/페널티 로그
        if bounce_confirmed:
            score += 5
            signal.reasons.append(
                f"✓ 반등 확인 (+{bounce_strength:.1f}%)")
        elif still_falling:
            score -= 10
            signal.reasons.append(
                "✗ 직전 3틱 연속 하락 — 낙하 중")

        signal.reasons.append(
            f"시가→고가 +{rally_pct:.1f}%, "
            f"고가→현재 -{pullback / high * 100:.1f}%"
        )

        # ── 3. 5일 이동평균선 위 + 거래량 감소 (조건2) ──
        above_ma5 = False
        vol_declining = False

        if ohlcv is not None and len(ohlcv) >= 5:
            ma5 = ohlcv['close'].iloc[-5:].mean()
            if price > ma5:
                above_ma5 = True
                score += 10
                signal.reasons.append(
                    f"[조건2a] 5일선({ma5:,.0f}) 위 — 건전한 눌림")
            else:
                score -= 10
                signal.reasons.append(
                    f"5일선({ma5:,.0f}) 이탈 — 약세")

        # 분봉 기반 눌림 중 거래량 패턴
        if len(intraday_prices) >= 10:
            volumes = [t.get('volume', 0) for t in intraday_prices]
            prices_list = [t['price'] for t in intraday_prices]

            peak_idx = prices_list.index(max(prices_list))
            if 2 < peak_idx < len(prices_list) - 2:
                vol_before = np.mean(volumes[:peak_idx])
                vol_after = np.mean(volumes[peak_idx:])

                if vol_before > 0:
                    vol_decline = vol_after / vol_before
                    if vol_decline < 0.5:
                        vol_declining = True
                        score += 15
                        signal.reasons.append(
                            f"[조건2b] 매도 소진 "
                            f"(거래량 {vol_decline:.0%}로 급감)")
                    elif vol_decline < 0.8:
                        vol_declining = True
                        score += 5
                        signal.reasons.append(
                            f"거래량 감소 ({vol_decline:.0%})")
                    else:
                        score -= 10
                        signal.reasons.append(
                            f"매도 지속 (거래량 {vol_decline:.0%} 유지)")

        if above_ma5 and vol_declining:
            conditions_met += 1

        # ── 4. 체결강도 확인 (조건3) ──
        # 체결강도 = 매수 체결량 / 매도 체결량 × 100
        # intraday에 buy_volume이 있으면 활용
        if len(intraday_prices) >= 5:
            recent = intraday_prices[-5:]
            up_ticks = sum(
                1 for i in range(1, len(recent))
                if recent[i]['price'] > recent[i - 1]['price']
            )
            tick_ratio = up_ticks / max(len(recent) - 1, 1) * 100
            if tick_ratio >= 60:
                conditions_met += 1
                score += 10
                signal.reasons.append(
                    f"[조건3] 매수 우위 체결 ({tick_ratio:.0f}%)")
            elif tick_ratio <= 30:
                score -= 10
                signal.reasons.append(
                    f"매도 우위 체결 ({tick_ratio:.0f}%)")

        # ── 5. 거래대금 확인 (조건4) — 코디네이터에서도 필터하지만 점수 반영 ──
        trade_value = getattr(snapshot, 'trade_value', 0) or 0
        tv_억 = trade_value / 100_000_000
        if tv_억 >= 500:
            conditions_met += 1
            score += 5
            signal.reasons.append(
                f"[조건4] 거래대금 충분 ({tv_억:.0f}억)")
        elif tv_억 >= 100:
            pass  # 중립
        else:
            score -= 5
            signal.reasons.append(
                f"거래대금 부족 ({tv_억:.0f}억)")

        # ── 6. 급등 사유 확인 (조건5) — category 기반 ──
        category = getattr(snapshot, 'category', '') or ''
        if category and category != '—' and category != '사유 불명':
            conditions_met += 1
            signal.reasons.append(
                f"[조건5] 급등 테마: {category}")
        else:
            signal.reasons.append("급등 사유 불명 — 지속성 우려")

        # ── 5조건 충족 보너스 ──
        if conditions_met >= 4:
            score += 15
            signal.reasons.insert(0,
                f"★ 눌림목 5조건 중 {conditions_met}개 충족 — 강력 진입")
        elif conditions_met >= 3:
            score += 8
            signal.reasons.insert(0,
                f"눌림목 5조건 중 {conditions_met}개 충족 — 양호")

        # ── 7. 지지선 확인 (보너스) ──
        support_score = self._check_support(snapshot, ohlcv)
        score += support_score
        if support_score > 0:
            signal.reasons.append("하방 지지선 확인")
        elif support_score < 0:
            signal.reasons.append("지지선 이탈 위험")

        # ── 8. 피보나치 진입가 제안 ──
        fib_50 = high - rally * 0.5
        fib_382 = high - rally * 0.382
        if price <= fib_50:
            signal.entry_price_zone = fib_50
        else:
            signal.entry_price_zone = fib_382

        # ── 2026-04-07: 반등 미확인 시 점수 상한 40 (하락 중 진입 방지) ──
        if not bounce_confirmed:
            score = min(score, 40)
            if signal.timing == "즉시":
                signal.timing = "대기"

        # ── 2026-04-07 신규: 고점 이후 경과 시간 체크 ──
        # 남선알미늄: 고점 10:14, 진입 11:34 (80분 경과) → 모멘텀 소실
        if len(intraday_prices) >= 10:
            prices_all = [t['price'] for t in intraday_prices]
            peak_idx = prices_all.index(max(prices_all))
            ticks_since_peak = len(prices_all) - 1 - peak_idx
            if ticks_since_peak >= 30:
                # 고점 후 30틱(≈30분) 이상 경과
                score -= 20
                signal.reasons.append(
                    f"⚠ 고점 후 {ticks_since_peak}틱 경과 — 모멘텀 소실 위험")
                if signal.timing == "즉시":
                    signal.timing = "대기"
            elif ticks_since_peak >= 15:
                score -= 10
                signal.reasons.append(
                    f"고점 후 {ticks_since_peak}틱 경과 — 주의")

        # ── 2026-04-07 신규: 시가 대비 현재가 위치 체크 ──
        # 삼성E&A: 시가 52K→현재 49.6K (시가 이하 진입)
        if open_price > 0 and price < open_price:
            below_open_pct = (price - open_price) / open_price * 100
            score -= 15
            signal.reasons.append(
                f"⚠ 시가 이하 ({below_open_pct:+.1f}%) — 약세 구간")
            if below_open_pct <= -3.0 and signal.timing == "즉시":
                signal.timing = "관망"
                signal.reasons.append(
                    f"시가 대비 {below_open_pct:+.1f}% — 하락추세 관망")

        # ── 결과 조립 ──
        signal.entry_score = max(0, min(100, score))
        signal.confidence = min(
            0.4
            + min(len(intraday_prices) / 40, 0.25)
            + (0.25 if 0.3 <= pullback_ratio <= 0.5 else 0)
            + (0.1 if conditions_met >= 4 else 0),
            0.95,
        )

        # 구간별 TP/SL (리포트 기반)
        change_pct = snapshot.change_pct
        if change_pct < 15:
            signal.scalp_tp_pct = min(rally_pct * 0.4, 3.0)
            signal.scalp_sl_pct = -1.5
        else:
            signal.scalp_tp_pct = min(rally_pct * 0.25, 2.0)
            signal.scalp_sl_pct = -1.0
        signal.hold_minutes = 15

        return signal

    def _check_support(
        self,
        snapshot: StockSnapshot,
        ohlcv: Optional[pd.DataFrame],
    ) -> int:
        """지지선 확인: 전일 고가, 시가, 전일 종가"""
        if ohlcv is None or len(ohlcv) < 2:
            return 0

        price = snapshot.price
        prev_high = ohlcv.iloc[-1]['high']
        prev_close = snapshot.prev_close
        today_open = snapshot.open

        score = 0
        if price >= prev_high * 0.99:
            score += 5
        if price >= today_open * 0.99:
            score += 5
        if price < prev_close:
            score -= 10

        return score
