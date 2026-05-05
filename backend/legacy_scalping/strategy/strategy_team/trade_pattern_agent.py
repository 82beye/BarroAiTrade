"""
매매 패턴 학습 에이전트

과거 매매 기록에서 반복되는 실수 패턴을 식별하고
동일 패턴 반복을 방지하기 위한 파라미터를 권고한다.

분석 항목:
  1. 동일 종목 반복 매수 → max_entries_per_stock 조정
  2. 손절 후 즉시 재진입 → cooldown_minutes 조정
  3. 연속 손절 발생 종목 → 블랙리스트 등록
  4. 일일 손실 누적 속도 → 포지션 비중 축소
  5. 손절/익절 비율 분석 → 손절폭 조정
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

from strategy.strategy_team.base_agent import (
    BaseStrategyAgent, StrategySignal, TradeRecord,
)

logger = logging.getLogger(__name__)


class TradePatternAgent(BaseStrategyAgent):

    AGENT_NAME = "trade_pattern"

    def analyze(
        self,
        trades: List[TradeRecord],
        cache_data: Dict[str, pd.DataFrame],
        watchlist: List[dict],
    ) -> Optional[StrategySignal]:
        if not trades:
            return None

        signal = StrategySignal(
            agent_name=self.AGENT_NAME,
            confidence=0.0,
        )
        checks_passed = 0
        total_checks = 5

        # ── 1. 동일 종목 반복 매수 패턴 ──
        buy_counts = defaultdict(int)
        for t in trades:
            if t.action == 'BUY':
                buy_counts[t.code] += 1

        max_buys = max(buy_counts.values(), default=0)
        if max_buys > 5:
            # 심각한 반복 매수: 3회로 제한
            signal.max_entries_per_stock = 3
            worst_code = max(buy_counts, key=buy_counts.get)
            signal.reasons.append(
                f"반복매수 감지: [{worst_code}] {max_buys}회 "
                f"→ 종목당 3회 제한")
            checks_passed += 1
        elif max_buys > 3:
            signal.max_entries_per_stock = 3
            signal.reasons.append(
                f"반복매수 경고: 최대 {max_buys}회 → 3회 제한")
            checks_passed += 1

        # ── 2. 손절 후 즉시 재진입 패턴 ──
        rapid_re_entries = self._count_rapid_re_entries(trades)
        if rapid_re_entries >= 3:
            signal.cooldown_minutes = 15
            signal.reasons.append(
                f"급속 재진입 {rapid_re_entries}회 → 쿨다운 15분")
            checks_passed += 1
        elif rapid_re_entries >= 1:
            signal.cooldown_minutes = 10
            signal.reasons.append(
                f"재진입 {rapid_re_entries}회 → 쿨다운 10분")
            checks_passed += 1

        # ── 3. 연속 손절 종목 블랙리스트 ──
        sl_counts = defaultdict(int)
        for t in trades:
            if t.action == 'SELL' and '손절' in t.exit_type:
                sl_counts[t.code] += 1

        for code, count in sl_counts.items():
            if count >= 3:
                signal.blacklist_codes.append(code)
                name = self._find_name(trades, code)
                signal.reasons.append(
                    f"[{code}] {name}: 손절 {count}회 → 블랙리스트")
                checks_passed += 1

        # ── 4. 일일 손실 누적 속도 분석 ──
        loss_speed = self._analyze_loss_speed(trades)
        if loss_speed == 'fast':
            signal.position_size_multiplier = 0.5
            signal.reasons.append(
                "급속 손실 누적 → 포지션 비중 50% 축소")
            checks_passed += 1
        elif loss_speed == 'moderate':
            signal.position_size_multiplier = 0.7
            signal.reasons.append(
                "손실 누적 중 → 포지션 비중 30% 축소")
            checks_passed += 1

        # ── 5. 손절/익절 비율 분석 ──
        sells = [t for t in trades if t.action == 'SELL']
        if len(sells) >= 3:
            sl_count = sum(1 for s in sells if s.pnl_pct < 0)
            tp_count = sum(1 for s in sells if s.pnl_pct > 0)
            win_rate = tp_count / len(sells) * 100

            if win_rate < 30:
                # 승률 매우 낮으면 진입 횟수를 줄여서 리스크 관리
                # (손절폭은 settings.yaml 기준 유지 — 타이트한 손절이 오히려 승률 하락 유발)
                signal.max_entries_per_stock = 2
                signal.reasons.append(
                    f"승률 {win_rate:.0f}%: 종목당 진입 2회로 제한 (손절폭 유지)")
                checks_passed += 1
            elif win_rate < 50:
                avg_win = sum(
                    s.pnl_pct for s in sells if s.pnl_pct > 0
                ) / max(tp_count, 1)
                avg_loss = abs(sum(
                    s.pnl_pct for s in sells if s.pnl_pct < 0
                ) / max(sl_count, 1))

                if avg_win < avg_loss:
                    signal.reasons.append(
                        f"승률 {win_rate:.0f}%, "
                        f"평균이익 {avg_win:.1f}% < "
                        f"평균손실 {avg_loss:.1f}%: "
                        f"진입 정확도 개선 필요")
                    checks_passed += 1

        signal.confidence = min(checks_passed / total_checks, 1.0)
        return signal if signal.reasons else None

    def _count_rapid_re_entries(self, trades: List[TradeRecord]) -> int:
        """손절 후 5분 이내 동일 종목 재진입 횟수"""
        sorted_trades = sorted(trades, key=lambda t: t.timestamp)
        last_sl = {}  # code -> timestamp str
        rapid_count = 0

        for t in sorted_trades:
            if t.action == 'SELL' and '손절' in t.exit_type:
                last_sl[t.code] = t.timestamp
            elif t.action == 'BUY' and t.code in last_sl:
                try:
                    sl_time = datetime.fromisoformat(last_sl[t.code])
                    buy_time = datetime.fromisoformat(t.timestamp)
                    gap = (buy_time - sl_time).total_seconds()
                    if gap < 300:  # 5분
                        rapid_count += 1
                except (ValueError, TypeError):
                    pass
        return rapid_count

    def _analyze_loss_speed(self, trades: List[TradeRecord]) -> str:
        """
        일일 손실 누적 속도 분석

        Returns:
            'fast' : 1시간 내 -3% 이상 손실
            'moderate' : 2시간 내 -3% 이상 손실
            'normal' : 정상
        """
        sells = sorted(
            [t for t in trades if t.action == 'SELL' and t.pnl_pct < 0],
            key=lambda t: t.timestamp,
        )
        if len(sells) < 2:
            return 'normal'

        try:
            # 날짜별 그룹핑하여 각 날짜의 손실 속도 분석
            from collections import defaultdict
            daily_sells = defaultdict(list)
            for s in sells:
                sell_dt = datetime.fromisoformat(s.timestamp)
                daily_sells[sell_dt.date()].append(s)

            # 가장 최근 날짜부터 확인
            for day in sorted(daily_sells.keys(), reverse=True):
                day_sells = daily_sells[day]
                if len(day_sells) < 2:
                    continue

                first_loss_time = datetime.fromisoformat(
                    day_sells[0].timestamp)
                cumulative_loss = 0.0

                for s in day_sells:
                    sell_time = datetime.fromisoformat(s.timestamp)
                    elapsed_hours = (
                        sell_time - first_loss_time).total_seconds() / 3600
                    cumulative_loss += s.pnl_pct

                    if cumulative_loss <= -3.0:
                        if elapsed_hours <= 1.0:
                            return 'fast'
                        elif elapsed_hours <= 2.0:
                            return 'moderate'
        except (ValueError, TypeError):
            pass

        return 'normal'

    @staticmethod
    def _find_name(trades: List[TradeRecord], code: str) -> str:
        for t in trades:
            if t.code == code and t.name:
                return t.name
        return code
