"""
진입 타이밍 최적화 에이전트

과거 매매 시간대별 승률을 분석하여
최적/최악 진입 시간대를 식별하고
진입 시간 필터 파라미터를 조정한다.

분석 항목:
  1. 15분 버킷별 승률/PnL
  2. 장 초반 노이즈 구간 탐지
  3. 골든 타임 (최적 진입 구간)
  4. 데드 타임 (최악 진입 구간)
  5. 시간대별 BB 돌파율 효과
"""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from strategy.strategy_team.base_agent import (
    BaseStrategyAgent, StrategySignal, TradeRecord,
)

logger = logging.getLogger(__name__)


class EntryTimingAgent(BaseStrategyAgent):

    AGENT_NAME = "entry_timing"

    def analyze(
        self,
        trades: List[TradeRecord],
        cache_data: Dict[str, pd.DataFrame],
        watchlist: List[dict],
    ) -> Optional[StrategySignal]:
        buys = [t for t in trades if t.action == 'BUY']
        sells = [t for t in trades if t.action == 'SELL']

        if len(buys) < 5 or len(sells) < 3:
            return None

        signal = StrategySignal(
            agent_name=self.AGENT_NAME,
            confidence=0.0,
        )

        # ── 1. 시간대별 매수 → 매도 매칭 ──
        time_pnl = self._match_buy_sell_by_time(buys, sells)

        # ── 2. 장 초반 노이즈 분석 (09:05 ~ 09:20) ──
        early_bucket = self._get_bucket_stats(time_pnl, 9, 0, 9, 30)
        if early_bucket and early_bucket['count'] >= 2:
            if early_bucket['win_rate'] < 30:
                signal.entry_start_delay_minutes = 25  # 09:00 + 25 = 09:25
                signal.reasons.append(
                    f"장초반(09:00~09:30) 승률 "
                    f"{early_bucket['win_rate']:.0f}% → "
                    f"09:25부터 매수 시작")
            elif early_bucket['win_rate'] < 50:
                signal.entry_start_delay_minutes = 15  # 09:15
                signal.reasons.append(
                    f"장초반 승률 {early_bucket['win_rate']:.0f}% → "
                    f"09:15부터 매수 시작")

        # ── 3. 골든 타임 탐색 (최고 승률 30분 구간) ──
        golden = self._find_best_window(time_pnl, min_trades=2)
        if golden:
            signal.reasons.append(
                f"골든타임: {golden['start']}~{golden['end']} "
                f"(승률 {golden['win_rate']:.0f}%, "
                f"평균PnL {golden['avg_pnl']:+.1f}%)")

        # ── 4. 데드 타임 탐색 (최악 승률 30분 구간) ──
        dead = self._find_worst_window(time_pnl, min_trades=2)
        if dead and dead['avg_pnl'] < -1.0:
            signal.reasons.append(
                f"데드타임: {dead['start']}~{dead['end']} "
                f"(승률 {dead['win_rate']:.0f}%, "
                f"평균PnL {dead['avg_pnl']:+.1f}%) → 매수 회피")

        # ── 5. BB 돌파율 vs 시간대 상관 분석 ──
        bb_timing = self._analyze_bb_by_time(buys, sells)
        if bb_timing:
            signal.reasons.append(bb_timing)

        # 신뢰도: 매매 횟수에 비례
        total_matched = sum(
            b['count'] for b in time_pnl.values())
        signal.confidence = min(total_matched / 20, 1.0)

        return signal if signal.reasons else None

    def _match_buy_sell_by_time(
        self,
        buys: List[TradeRecord],
        sells: List[TradeRecord],
    ) -> Dict[str, dict]:
        """
        매수 시점별 15분 버킷으로 그룹핑하고
        대응하는 매도의 PnL을 매칭

        Returns:
            {
                "09:00": {"count": 5, "pnl_list": [...], "win_rate": 40},
                "09:15": {...}, ...
            }
        """
        all_trades = sorted(
            buys + sells, key=lambda t: t.timestamp)

        # 종목별 매수 큐 (FIFO 매칭)
        buy_queue = defaultdict(list)
        buckets = defaultdict(lambda: {'count': 0, 'pnl_list': []})

        for t in all_trades:
            if t.action == 'BUY':
                buy_queue[t.code].append(t)
            elif t.action == 'SELL' and buy_queue.get(t.code):
                matched_buy = buy_queue[t.code].pop(0)
                try:
                    ts = datetime.fromisoformat(matched_buy.timestamp)
                    bucket = f"{ts.hour:02d}:{ts.minute // 15 * 15:02d}"
                    buckets[bucket]['count'] += 1
                    buckets[bucket]['pnl_list'].append(t.pnl_pct)
                except (ValueError, TypeError):
                    pass

        # 승률 계산
        for key, data in buckets.items():
            pnl_list = data['pnl_list']
            if pnl_list:
                wins = sum(1 for p in pnl_list if p > 0)
                data['win_rate'] = wins / len(pnl_list) * 100
                data['avg_pnl'] = sum(pnl_list) / len(pnl_list)
            else:
                data['win_rate'] = 0
                data['avg_pnl'] = 0

        return dict(buckets)

    def _get_bucket_stats(
        self,
        time_pnl: Dict[str, dict],
        start_h: int, start_m: int,
        end_h: int, end_m: int,
    ) -> Optional[dict]:
        """특정 시간 범위의 통합 통계"""
        combined_pnl = []
        total_count = 0

        for key, data in time_pnl.items():
            try:
                h, m = map(int, key.split(':'))
                bucket_minutes = h * 60 + m
                start_minutes = start_h * 60 + start_m
                end_minutes = end_h * 60 + end_m

                if start_minutes <= bucket_minutes < end_minutes:
                    combined_pnl.extend(data['pnl_list'])
                    total_count += data['count']
            except (ValueError, TypeError):
                pass

        if not combined_pnl:
            return None

        wins = sum(1 for p in combined_pnl if p > 0)
        return {
            'count': total_count,
            'win_rate': wins / len(combined_pnl) * 100,
            'avg_pnl': sum(combined_pnl) / len(combined_pnl),
        }

    def _find_best_window(
        self, time_pnl: Dict[str, dict], min_trades: int = 2,
    ) -> Optional[dict]:
        """최고 승률 30분 구간 탐색 (15분 슬라이딩)"""
        buckets = sorted(time_pnl.keys())
        best = None

        for i in range(len(buckets)):
            # 현재 버킷 + 다음 버킷 (약 30분)
            combined_pnl = list(time_pnl[buckets[i]]['pnl_list'])
            end_key = buckets[i]

            if i + 1 < len(buckets):
                combined_pnl.extend(time_pnl[buckets[i + 1]]['pnl_list'])
                end_key = buckets[i + 1]

            if len(combined_pnl) < min_trades:
                continue

            wins = sum(1 for p in combined_pnl if p > 0)
            win_rate = wins / len(combined_pnl) * 100
            avg_pnl = sum(combined_pnl) / len(combined_pnl)

            if best is None or avg_pnl > best['avg_pnl']:
                # 끝 버킷에 +15분
                try:
                    eh, em = map(int, end_key.split(':'))
                    em += 15
                    if em >= 60:
                        eh += 1
                        em -= 60
                    end_str = f"{eh:02d}:{em:02d}"
                except (ValueError, TypeError):
                    end_str = end_key

                best = {
                    'start': buckets[i],
                    'end': end_str,
                    'win_rate': win_rate,
                    'avg_pnl': avg_pnl,
                    'count': len(combined_pnl),
                }

        return best

    def _find_worst_window(
        self, time_pnl: Dict[str, dict], min_trades: int = 2,
    ) -> Optional[dict]:
        """최악 승률 30분 구간 탐색"""
        buckets = sorted(time_pnl.keys())
        worst = None

        for i in range(len(buckets)):
            combined_pnl = list(time_pnl[buckets[i]]['pnl_list'])
            end_key = buckets[i]

            if i + 1 < len(buckets):
                combined_pnl.extend(time_pnl[buckets[i + 1]]['pnl_list'])
                end_key = buckets[i + 1]

            if len(combined_pnl) < min_trades:
                continue

            wins = sum(1 for p in combined_pnl if p > 0)
            win_rate = wins / len(combined_pnl) * 100
            avg_pnl = sum(combined_pnl) / len(combined_pnl)

            if worst is None or avg_pnl < worst['avg_pnl']:
                try:
                    eh, em = map(int, end_key.split(':'))
                    em += 15
                    if em >= 60:
                        eh += 1
                        em -= 60
                    end_str = f"{eh:02d}:{em:02d}"
                except (ValueError, TypeError):
                    end_str = end_key

                worst = {
                    'start': buckets[i],
                    'end': end_str,
                    'win_rate': win_rate,
                    'avg_pnl': avg_pnl,
                    'count': len(combined_pnl),
                }

        return worst

    def _analyze_bb_by_time(
        self,
        buys: List[TradeRecord],
        sells: List[TradeRecord],
    ) -> Optional[str]:
        """BB 돌파율과 시간대의 상관 분석"""
        early_bb = []   # 09:05~09:30 매수 BB 돌파율
        mid_bb = []     # 09:30~11:00 매수 BB 돌파율

        for b in buys:
            if 'BB20 상한 돌파' not in b.reason:
                continue
            try:
                parts = b.reason.split('BB20 상한 돌파 ')
                bb_pct = float(
                    parts[1].split('%')[0].replace('+', ''))
                ts = datetime.fromisoformat(b.timestamp)
                h, m = ts.hour, ts.minute

                if h == 9 and m < 30:
                    early_bb.append(bb_pct)
                elif (h == 9 and m >= 30) or (h == 10):
                    mid_bb.append(bb_pct)
            except (ValueError, IndexError, TypeError):
                pass

        if early_bb and mid_bb:
            avg_early = sum(early_bb) / len(early_bb)
            avg_mid = sum(mid_bb) / len(mid_bb)

            if avg_early > avg_mid + 2:
                return (
                    f"장초반 BB 과열 진입: 평균 +{avg_early:.1f}% "
                    f"vs 장중 +{avg_mid:.1f}% "
                    f"→ 09:30 이후 안정적 진입 권장")

        return None
