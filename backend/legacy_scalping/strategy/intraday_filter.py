"""
장중 진입 필터 (IntradayFilter)

매수 신호 발생 시 실시간 상태를 추가 검증하여
잘못된 타이밍의 진입을 차단한다.

필터 항목:
  1. 손절 후 쿨다운 (종목당 10분)
  2. 동일 종목 최대 매수 횟수 제한 (일 3회)
  3. 하락추세 매수 차단 (최근 가격 추세)
  4. 과열 진입 차단 (BB 돌파율 과대)
  5. 일일 손실 진행 중 매수 축소
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class IntradayFilter:
    """
    장중 매수 진입 필터

    전략 모듈(entry_signal)이 매수 신호를 생성한 후,
    OrderProcessor에 제출하기 전에 이 필터를 통과해야 한다.
    """

    def __init__(self, config: dict):
        filter_config = config.get('strategy', {}).get('intraday_filter', {})

        # 손절 후 쿨다운 (분)
        self.cooldown_minutes = filter_config.get('cooldown_minutes', 10)
        # 종목당 일일 최대 매수 횟수
        self.max_entries_per_stock = filter_config.get(
            'max_entries_per_stock', 3)
        # 하락추세 차단: 최근 N개 가격 비교에서 하락이면 차단
        self.trend_check_enabled = filter_config.get(
            'trend_check_enabled', True)
        # BB 돌파율 상한: 이 이상이면 과열로 판단
        self.max_bb_excess_pct = filter_config.get('max_bb_excess_pct', 8.0)
        # 일일 손실 진행 시 매수 비중 축소 임계값
        self.loss_reduce_threshold = filter_config.get(
            'loss_reduce_threshold', -2.0)

        # 익절 후 재진입 쿨다운 (분) — 고점 추격 방지
        self.tp_cooldown_minutes = filter_config.get('tp_cooldown_minutes', 15)

        # 상태 추적 (일중 리셋)
        self._stop_loss_times: Dict[str, datetime] = {}   # code → 마지막 손절 시각
        self._tp_times: Dict[str, datetime] = {}           # code → 마지막 익절 시각
        self._daily_buy_count: Dict[str, int] = defaultdict(int)  # code → 당일 매수 횟수
        self._recent_prices: Dict[str, list] = defaultdict(list)  # code → 최근 가격들

    def reset_daily(self):
        """일일 상태 리셋 (장 시작 시 호출)"""
        self._stop_loss_times.clear()
        self._tp_times.clear()
        self._daily_buy_count.clear()
        self._recent_prices.clear()
        logger.info("IntradayFilter 일일 상태 리셋")

    def record_stop_loss(self, code: str):
        """손절 발생 기록 (exit_monitor에서 호출)"""
        self._stop_loss_times[code] = datetime.now()
        logger.info(f"손절 쿨다운 시작: [{code}] {self.cooldown_minutes}분")

    def record_take_profit(self, code: str):
        """익절 발생 기록 — 고점 근처 재진입 방지"""
        self._tp_times[code] = datetime.now()
        logger.info(f"익절 쿨다운 시작: [{code}] {self.tp_cooldown_minutes}분")

    def record_buy(self, code: str):
        """매수 성공 기록"""
        self._daily_buy_count[code] += 1

    def update_price(self, code: str, price: float):
        """현재가 업데이트 (추세 추적용)"""
        prices = self._recent_prices[code]
        prices.append(price)
        # 최근 10개만 유지
        if len(prices) > 10:
            self._recent_prices[code] = prices[-10:]

    def check(
        self,
        code: str,
        name: str,
        current_price: float,
        bb20_upper: float,
        daily_pnl_pct: float,
    ) -> Optional[str]:
        """
        매수 진입 필터 체크

        Args:
            code: 종목 코드
            name: 종목명
            current_price: 현재가
            bb20_upper: BB(20) 상단
            daily_pnl_pct: 당일 실현 손익률

        Returns:
            None → 통과 (매수 허용)
            str  → 거부 사유
        """
        now = datetime.now()

        # 1a. 손절 후 쿨다운
        last_sl = self._stop_loss_times.get(code)
        if last_sl:
            elapsed = (now - last_sl).total_seconds() / 60
            if elapsed < self.cooldown_minutes:
                remaining = self.cooldown_minutes - elapsed
                return (
                    f"손절 쿨다운 중 ({remaining:.0f}분 남음, "
                    f"손절 후 {self.cooldown_minutes}분 대기)")

        # 1b. 익절 후 쿨다운 (고점 추격 재진입 방지)
        last_tp = self._tp_times.get(code)
        if last_tp:
            elapsed = (now - last_tp).total_seconds() / 60
            if elapsed < self.tp_cooldown_minutes:
                remaining = self.tp_cooldown_minutes - elapsed
                return (
                    f"익절 쿨다운 중 ({remaining:.0f}분 남음, "
                    f"익절 후 {self.tp_cooldown_minutes}분 대기)")

        # 2. 종목당 일일 최대 매수 횟수
        count = self._daily_buy_count[code]
        if count >= self.max_entries_per_stock:
            return (
                f"일일 매수 한도 초과 ({count}/{self.max_entries_per_stock}회)")

        # 3. 하락추세 매수 차단
        if self.trend_check_enabled:
            prices = self._recent_prices.get(code, [])
            if len(prices) >= 3:
                # 최근 3개 가격이 연속 하락이면 차단
                recent = prices[-3:]
                if recent[0] > recent[1] > recent[2]:
                    drop_pct = (recent[2] - recent[0]) / recent[0] * 100
                    return (
                        f"하락추세 감지 (최근 3회 연속 하락, "
                        f"{drop_pct:+.1f}%)")

        # 4. BB 과열 진입 차단
        if bb20_upper > 0:
            bb_excess = (current_price - bb20_upper) / bb20_upper * 100
            if bb_excess > self.max_bb_excess_pct:
                return (
                    f"BB 과열 진입 차단 (BB20 돌파 +{bb_excess:.1f}% > "
                    f"한도 +{self.max_bb_excess_pct:.0f}%)")

        # 5. 일일 손실 진행 시 경고 (차단은 안 함, 로그만)
        if daily_pnl_pct <= self.loss_reduce_threshold:
            logger.warning(
                f"일일 손실 진행 중 ({daily_pnl_pct:+.1f}%) 매수 주의: "
                f"[{code}] {name}")

        return None  # 통과

    def get_status(self) -> dict:
        """현재 필터 상태 반환"""
        now = datetime.now()
        active_sl_cooldowns = {}
        for code, sl_time in self._stop_loss_times.items():
            elapsed = (now - sl_time).total_seconds() / 60
            if elapsed < self.cooldown_minutes:
                active_sl_cooldowns[code] = round(
                    self.cooldown_minutes - elapsed, 1)

        active_tp_cooldowns = {}
        for code, tp_time in self._tp_times.items():
            elapsed = (now - tp_time).total_seconds() / 60
            if elapsed < self.tp_cooldown_minutes:
                active_tp_cooldowns[code] = round(
                    self.tp_cooldown_minutes - elapsed, 1)

        return {
            'active_sl_cooldowns': active_sl_cooldowns,
            'active_tp_cooldowns': active_tp_cooldowns,
            'daily_buy_counts': dict(self._daily_buy_count),
            'trend_tracked_stocks': len(self._recent_prices),
        }
