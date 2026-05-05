"""
스캘핑 타이밍 팀 에이전트 일일 성과 분석 리포트

매일 장 마감 후 당일 스캘핑 매매 이력을 10명 전문가 에이전트 기준으로
성과 분석하고 최적 타이밍 패턴을 산출한다.

활용:
  - 에이전트 가중치 재조정 근거
  - 시간대별 최적 진입 윈도우 계산
  - 슬리피지/합의수준/점수대별 승률 산출
  - 코드 설정값 자동 조정 제안
"""

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ScalpingDailyReport:
    """스캘핑 타이밍 팀 에이전트 일일 성과 분석"""

    # 10명 에이전트 (coordinator.py 와 동일)
    AGENTS = [
        'VWAP전략가', '모멘텀폭발전문가', '눌림목전문가', '돌파확인전문가',
        '캔들패턴전문가', '거래량프로파일전문가', '골든타임전문가',
        '상대강도전문가', '리스크보상전문가', '호가테이프전문가',
    ]

    CURRENT_WEIGHTS = {
        'VWAP전략가': 0.04,
        '모멘텀폭발전문가': 0.06,
        '눌림목전문가': 0.14,
        '돌파확인전문가': 0.12,
        '캔들패턴전문가': 0.10,
        '거래량프로파일전문가': 0.16,
        '골든타임전문가': 0.04,
        '상대강도전문가': 0.18,
        '리스크보상전문가': 0.04,
        '호가테이프전문가': 0.12,
    }

    # 시간대 구간 정의 (골든타임 에이전트 기준)
    TIME_WINDOWS = [
        ('09:00', '09:15', '개장직후(노이즈)'),
        ('09:15', '09:30', '방향탐색'),
        ('09:30', '10:00', '골든타임'),
        ('10:00', '11:00', '추세지속'),
        ('11:00', '13:00', '점심침체'),
        ('13:00', '14:00', '오후변동'),
        ('14:00', '15:30', '마감임박'),
    ]

    # 왕복 수수료+세금
    ROUND_TRIP_FEE_PCT = 0.21

    def __init__(self, config: dict):
        self.config = config
        self.trade_log_path = config.get('logging', {}).get(
            'trade_log', './logs/trades.jsonl')
        self.report_dir = Path(
            config.get('logging', {}).get('dir', './logs')) / 'scalping_reports'
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, target_date: str = None) -> dict:
        """
        일일 스캘핑 성과 분석 리포트 생성

        Args:
            target_date: YYYY-MM-DD (None이면 오늘)

        Returns:
            분석 리포트 dict (JSON 저장 + 텍스트 리포트용)
        """
        if target_date is None:
            target_date = date.today().isoformat()

        trades = self._load_scalping_trades(target_date)
        if not trades:
            report = {
                'date': target_date,
                'total_scalping_trades': 0,
                'message': '당일 스캘핑 매매 없음',
            }
            self._save_report(report, target_date)
            return report

        buys = [t for t in trades if t['action'] == 'BUY']
        sells = [t for t in trades if t['action'] == 'SELL']

        # 매수-매도 매칭 (종목+시간 기반)
        pairs = self._match_buy_sell(buys, sells)

        report = {
            'date': target_date,
            'total_scalping_trades': len(trades),
            'buy_count': len(buys),
            'sell_count': len(sells),
            'matched_pairs': len(pairs),

            # 1. 종합 성과
            'performance': self._calc_performance(pairs, sells),

            # 2. 시간대별 분석
            'time_window_analysis': self._analyze_time_windows(pairs),

            # 3. 점수대별 승률
            'score_analysis': self._analyze_score_bands(pairs),

            # 4. 합의수준별 승률
            'consensus_analysis': self._analyze_consensus(pairs),

            # 5. 슬리피지 분석
            'slippage_analysis': self._analyze_slippage(buys),

            # 6. 종목별 상세
            'stock_details': self._analyze_stocks(pairs),

            # 7. 미청산 포지션
            'unsettled': self._find_unsettled(buys, sells),

            # 8. 최적 타이밍 패턴 산출
            'optimal_timing': self._calc_optimal_timing(pairs),

            # 9. 코드 설정값 조정 제안
            'parameter_suggestions': self._suggest_parameters(pairs, buys),
        }

        self._save_report(report, target_date)
        return report

    # ─── 매수-매도 매칭 ───

    def _match_buy_sell(
        self, buys: List[dict], sells: List[dict],
    ) -> List[dict]:
        """매수-매도를 시간순으로 매칭하여 라운드트립 생성"""
        pairs = []
        sell_used = set()

        for buy in sorted(buys, key=lambda x: x['timestamp']):
            code = buy['code']
            buy_ts = datetime.fromisoformat(buy['timestamp'])

            # 같은 종목의 매도 중 가장 가까운 이후 매도 매칭
            best_sell = None
            best_idx = -1
            for idx, sell in enumerate(sells):
                if idx in sell_used:
                    continue
                if sell['code'] != code:
                    continue
                sell_ts = datetime.fromisoformat(sell['timestamp'])
                if sell_ts > buy_ts:
                    if best_sell is None or sell_ts < datetime.fromisoformat(
                            best_sell['timestamp']):
                        best_sell = sell
                        best_idx = idx

            if best_sell is not None:
                sell_used.add(best_idx)
                hold_sec = (
                    datetime.fromisoformat(best_sell['timestamp'])
                    - buy_ts
                ).total_seconds()

                signal_price = buy.get('signal_price', buy['price'])
                fill_price = buy['price']
                slip_pct = (
                    (fill_price - signal_price) / signal_price * 100
                    if signal_price > 0 else 0
                )

                # reason 파싱: "스캘핑 57점 (의견분분) | TP +2.9% SL -2.5% 11분"
                reason = buy.get('reason', '')
                score, consensus, tp, sl = self._parse_reason(reason)

                entry_p = best_sell.get('entry_price', fill_price)
                exit_p = best_sell['price']
                gross_pnl_pct = (exit_p - entry_p) / entry_p * 100 if entry_p else 0
                net_pnl_pct = best_sell.get('net_pnl_pct', gross_pnl_pct)
                pnl_amount = (exit_p - entry_p) * best_sell['qty']
                commission = best_sell.get('commission', 0)
                tax = best_sell.get('tax', 0)
                net_amount = pnl_amount - commission - tax

                pairs.append({
                    'code': code,
                    'name': buy['name'],
                    'buy_time': buy['timestamp'],
                    'sell_time': best_sell['timestamp'],
                    'buy_price': fill_price,
                    'sell_price': exit_p,
                    'qty': best_sell['qty'],
                    'signal_price': signal_price,
                    'slippage_pct': round(slip_pct, 2),
                    'score': score,
                    'consensus': consensus,
                    'target_tp': tp,
                    'target_sl': sl,
                    'gross_pnl_pct': round(gross_pnl_pct, 2),
                    'net_pnl_pct': round(net_pnl_pct, 2),
                    'net_amount': round(net_amount),
                    'hold_seconds': int(hold_sec),
                    'hold_minutes': round(hold_sec / 60, 1),
                    'exit_type': best_sell.get('exit_type', ''),
                    'buy_hour': buy_ts.strftime('%H:%M'),
                })

        return pairs

    def _parse_reason(self, reason: str) -> Tuple[float, str, float, float]:
        """reason 문자열에서 score, consensus, tp, sl 추출"""
        import re
        score = 0.0
        consensus = '의견분분'
        tp = 3.0
        sl = -2.5

        m = re.search(r'스캘핑\s+(\d+)점', reason)
        if m:
            score = float(m.group(1))

        m = re.search(r'\((만장일치|다수합의|소수합의|의견분분)\)', reason)
        if m:
            consensus = m.group(1)

        m = re.search(r'TP\s+\+?([\d.]+)%', reason)
        if m:
            tp = float(m.group(1))

        m = re.search(r'SL\s+([-\d.]+)%', reason)
        if m:
            sl = float(m.group(1))

        return score, consensus, tp, sl

    # ─── 분석 모듈 ───

    def _calc_performance(
        self, pairs: List[dict], sells: List[dict],
    ) -> dict:
        """종합 성과 계산"""
        if not pairs:
            return {}

        wins = [p for p in pairs if p['net_pnl_pct'] > 0]
        losses = [p for p in pairs if p['net_pnl_pct'] < 0]
        be = [p for p in pairs if p['net_pnl_pct'] == 0]

        total_net = sum(p['net_amount'] for p in pairs)
        avg_win = (
            sum(p['net_pnl_pct'] for p in wins) / len(wins) if wins else 0
        )
        avg_loss = (
            sum(p['net_pnl_pct'] for p in losses) / len(losses) if losses else 0
        )
        win_rate = len(wins) / len(pairs) * 100 if pairs else 0

        # 수익 팩터 = 총 이익 / 총 손실
        gross_profit = sum(p['net_amount'] for p in wins) if wins else 0
        gross_loss = abs(sum(p['net_amount'] for p in losses)) if losses else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # 평균 보유 시간
        avg_hold = (
            sum(p['hold_minutes'] for p in pairs) / len(pairs) if pairs else 0
        )

        return {
            'total_pairs': len(pairs),
            'wins': len(wins),
            'losses': len(losses),
            'breakeven': len(be),
            'win_rate_pct': round(win_rate, 1),
            'total_net_pnl': total_net,
            'avg_win_pct': round(avg_win, 2),
            'avg_loss_pct': round(avg_loss, 2),
            'profit_factor': round(profit_factor, 2),
            'avg_hold_minutes': round(avg_hold, 1),
            'gross_profit': gross_profit,
            'gross_loss': -abs(sum(p['net_amount'] for p in losses)),
        }

    def _analyze_time_windows(self, pairs: List[dict]) -> List[dict]:
        """시간대별 진입 성과 분석"""
        window_stats = []

        for start, end, label in self.TIME_WINDOWS:
            in_window = [
                p for p in pairs
                if start <= p['buy_hour'] < end
            ]
            if not in_window:
                window_stats.append({
                    'window': f'{start}-{end}',
                    'label': label,
                    'count': 0,
                })
                continue

            wins = sum(1 for p in in_window if p['net_pnl_pct'] > 0)
            total_pnl = sum(p['net_amount'] for p in in_window)
            avg_pnl = sum(p['net_pnl_pct'] for p in in_window) / len(in_window)

            window_stats.append({
                'window': f'{start}-{end}',
                'label': label,
                'count': len(in_window),
                'wins': wins,
                'losses': len(in_window) - wins,
                'win_rate_pct': round(wins / len(in_window) * 100, 1),
                'total_pnl': total_pnl,
                'avg_pnl_pct': round(avg_pnl, 2),
            })

        return window_stats

    def _analyze_score_bands(self, pairs: List[dict]) -> List[dict]:
        """점수대별 승률 분석"""
        bands = [
            (45, 50, '45-50점'),
            (50, 55, '50-55점'),
            (55, 60, '55-60점'),
            (60, 65, '60-65점'),
            (65, 70, '65-70점'),
            (70, 80, '70-80점'),
            (80, 100, '80-100점'),
        ]

        results = []
        for low, high, label in bands:
            in_band = [p for p in pairs if low <= p['score'] < high]
            if not in_band:
                results.append({'band': label, 'count': 0})
                continue

            wins = sum(1 for p in in_band if p['net_pnl_pct'] > 0)
            total_pnl = sum(p['net_amount'] for p in in_band)
            avg_pnl = sum(p['net_pnl_pct'] for p in in_band) / len(in_band)

            results.append({
                'band': label,
                'count': len(in_band),
                'wins': wins,
                'win_rate_pct': round(wins / len(in_band) * 100, 1),
                'total_pnl': total_pnl,
                'avg_pnl_pct': round(avg_pnl, 2),
            })

        return results

    def _analyze_consensus(self, pairs: List[dict]) -> List[dict]:
        """합의수준별 승률 분석"""
        levels = ['만장일치', '다수합의', '소수합의', '의견분분']
        results = []

        for level in levels:
            in_level = [p for p in pairs if p['consensus'] == level]
            if not in_level:
                results.append({'level': level, 'count': 0})
                continue

            wins = sum(1 for p in in_level if p['net_pnl_pct'] > 0)
            total_pnl = sum(p['net_amount'] for p in in_level)
            avg_pnl = (
                sum(p['net_pnl_pct'] for p in in_level) / len(in_level)
            )

            results.append({
                'level': level,
                'count': len(in_level),
                'wins': wins,
                'win_rate_pct': round(wins / len(in_level) * 100, 1),
                'total_pnl': total_pnl,
                'avg_pnl_pct': round(avg_pnl, 2),
            })

        return results

    def _analyze_slippage(self, buys: List[dict]) -> dict:
        """슬리피지 분석"""
        slippages = []
        for b in buys:
            sig = b.get('signal_price', b['price'])
            fill = b['price']
            slip = (fill - sig) / sig * 100 if sig > 0 else 0
            slippages.append({
                'code': b['code'],
                'name': b['name'],
                'time': b['timestamp'][11:19],
                'signal_price': sig,
                'fill_price': fill,
                'slippage_pct': round(slip, 1),
            })

        if not slippages:
            return {}

        slips = [s['slippage_pct'] for s in slippages]
        over_2pct = [s for s in slippages if s['slippage_pct'] > 2.0]

        return {
            'total_buys': len(slippages),
            'avg_slippage_pct': round(sum(slips) / len(slips), 2),
            'max_slippage_pct': round(max(slips), 1),
            'min_slippage_pct': round(min(slips), 1),
            'over_2pct_count': len(over_2pct),
            'over_2pct_ratio': round(
                len(over_2pct) / len(slippages) * 100, 1),
            'details': sorted(
                slippages, key=lambda x: x['slippage_pct'], reverse=True),
        }

    def _analyze_stocks(self, pairs: List[dict]) -> List[dict]:
        """종목별 상세 분석"""
        by_stock = defaultdict(list)
        for p in pairs:
            by_stock[p['code']].append(p)

        results = []
        for code, ps in by_stock.items():
            wins = sum(1 for p in ps if p['net_pnl_pct'] > 0)
            total_pnl = sum(p['net_amount'] for p in ps)
            avg_hold = sum(p['hold_minutes'] for p in ps) / len(ps)
            avg_slip = sum(p['slippage_pct'] for p in ps) / len(ps)

            results.append({
                'code': code,
                'name': ps[0]['name'],
                'trade_count': len(ps),
                'wins': wins,
                'win_rate_pct': round(wins / len(ps) * 100, 1),
                'total_pnl': total_pnl,
                'avg_hold_minutes': round(avg_hold, 1),
                'avg_slippage_pct': round(avg_slip, 1),
                'trades': ps,
            })

        return sorted(results, key=lambda x: x['total_pnl'], reverse=True)

    def _find_unsettled(
        self, buys: List[dict], sells: List[dict],
    ) -> List[dict]:
        """미청산 포지션 (매수 > 매도)"""
        buy_qty = defaultdict(int)
        sell_qty = defaultdict(int)
        buy_info = {}

        for b in buys:
            code = b['code']
            buy_qty[code] += b['qty']
            if code not in buy_info:
                buy_info[code] = {
                    'name': b['name'], 'avg_price': b['price']}

        for s in sells:
            sell_qty[s['code']] += s['qty']

        unsettled = []
        for code, bq in buy_qty.items():
            remain = bq - sell_qty.get(code, 0)
            if remain > 0:
                info = buy_info.get(code, {})
                price = info.get('avg_price', 0)
                unsettled.append({
                    'code': code,
                    'name': info.get('name', code),
                    'buy_total': bq,
                    'sell_total': sell_qty.get(code, 0),
                    'remaining_qty': remain,
                    'estimated_value': remain * price,
                })

        return unsettled

    def _calc_optimal_timing(self, pairs: List[dict]) -> dict:
        """최적 타이밍 패턴 산출"""
        if not pairs:
            return {}

        # 승리 거래만 분석
        wins = [p for p in pairs if p['net_pnl_pct'] > 0]
        if not wins:
            return {'message': '당일 수익 거래 없음 — 타이밍 패턴 산출 불가'}

        # 최적 진입 시간대
        win_hours = [p['buy_hour'] for p in wins]
        loss_hours = [p['buy_hour'] for p in pairs if p['net_pnl_pct'] <= 0]

        # 최적 점수대
        win_scores = [p['score'] for p in wins]
        all_scores = [p['score'] for p in pairs]

        # 최적 보유 시간
        win_hold = [p['hold_minutes'] for p in wins]

        # 최적 슬리피지 허용
        win_slip = [p['slippage_pct'] for p in wins]

        return {
            'best_entry_windows': self._rank_windows_by_profit(pairs),
            'optimal_score_threshold': {
                'win_avg_score': round(
                    sum(win_scores) / len(win_scores), 1) if win_scores else 0,
                'all_avg_score': round(
                    sum(all_scores) / len(all_scores), 1) if all_scores else 0,
                'min_profitable_score': min(win_scores) if win_scores else 0,
            },
            'optimal_hold_minutes': {
                'win_avg': round(
                    sum(win_hold) / len(win_hold), 1) if win_hold else 0,
                'best_range': self._find_best_hold_range(pairs),
            },
            'slippage_impact': {
                'win_avg_slip': round(
                    sum(win_slip) / len(win_slip), 2) if win_slip else 0,
                'recommended_max': round(
                    max(win_slip), 1) if win_slip else 2.0,
            },
        }

    def _rank_windows_by_profit(self, pairs: List[dict]) -> List[dict]:
        """시간대별 수익성 순위"""
        ranked = []
        for start, end, label in self.TIME_WINDOWS:
            in_w = [p for p in pairs if start <= p['buy_hour'] < end]
            if not in_w:
                continue
            pnl = sum(p['net_amount'] for p in in_w)
            wr = sum(1 for p in in_w if p['net_pnl_pct'] > 0) / len(in_w)
            ranked.append({
                'window': f'{start}-{end}',
                'label': label,
                'pnl': pnl,
                'win_rate': round(wr * 100, 1),
                'count': len(in_w),
            })
        return sorted(ranked, key=lambda x: x['pnl'], reverse=True)

    def _find_best_hold_range(self, pairs: List[dict]) -> str:
        """수익 거래의 최적 보유 시간 범위"""
        wins = [p for p in pairs if p['net_pnl_pct'] > 0]
        if not wins:
            return 'N/A'
        holds = sorted(p['hold_minutes'] for p in wins)
        return f'{holds[0]:.0f}-{holds[-1]:.0f}분'

    def _suggest_parameters(
        self, pairs: List[dict], buys: List[dict],
    ) -> dict:
        """코드 설정값 자동 조정 제안"""
        suggestions = {}

        if not pairs:
            return suggestions

        perf = self._calc_performance(pairs, [])

        # 1. min_score 조정
        win_scores = [p['score'] for p in pairs if p['net_pnl_pct'] > 0]
        loss_scores = [p['score'] for p in pairs if p['net_pnl_pct'] <= 0]
        if win_scores and loss_scores:
            avg_win_score = sum(win_scores) / len(win_scores)
            avg_loss_score = sum(loss_scores) / len(loss_scores)
            suggested_min = round(
                (avg_win_score + avg_loss_score) / 2, 0)
            suggestions['min_score'] = {
                'current': self.config.get(
                    'strategy', {}).get('scalping', {}).get('min_score', 45),
                'suggested': int(suggested_min),
                'reason': (
                    f'수익 평균 {avg_win_score:.0f}점, '
                    f'손실 평균 {avg_loss_score:.0f}점 → '
                    f'중간값 {suggested_min:.0f}점 제안'
                ),
            }

        # 2. max_slippage_pct 조정
        slippages = []
        for b in buys:
            sig = b.get('signal_price', b['price'])
            fill = b['price']
            slip = (fill - sig) / sig * 100 if sig > 0 else 0
            slippages.append(slip)

        if slippages:
            over2 = sum(1 for s in slippages if s > 2.0)
            suggestions['max_slippage_pct'] = {
                'current': self.config.get(
                    'strategy', {}).get('scalping', {}).get(
                        'max_slippage_pct', 2.0),
                'blocked_by_2pct': over2,
                'total_buys': len(slippages),
                'pass_ratio': round(
                    (len(slippages) - over2) / len(slippages) * 100, 1),
                'reason': (
                    f'{over2}/{len(slippages)}건 슬리피지 >2% → '
                    f'사전차단 시 {len(slippages)-over2}건만 통과'
                ),
            }

        # 3. entry_start / entry_end 조정
        time_perf = self._rank_windows_by_profit(pairs)
        if time_perf:
            profitable = [t for t in time_perf if t['pnl'] > 0]
            if profitable:
                best = profitable[0]
                suggestions['entry_window'] = {
                    'best_window': best['window'],
                    'best_label': best['label'],
                    'pnl': best['pnl'],
                    'reason': (
                        f'최고 수익 시간대: {best["window"]} '
                        f'({best["label"]}) +{best["pnl"]:,}원'
                    ),
                }

        # 4. TP/SL 조정
        tp_hit = [p for p in pairs if '익절' in p.get('exit_type', '')]
        timeout = [p for p in pairs if '시간초과' in p.get('exit_type', '')]
        sl_hit = [p for p in pairs if '손절' in p.get('exit_type', '')]

        suggestions['exit_analysis'] = {
            'tp_hit': len(tp_hit),
            'timeout': len(timeout),
            'sl_hit': len(sl_hit),
            'timeout_avg_pnl': round(
                sum(p['net_pnl_pct'] for p in timeout) / len(timeout), 2
            ) if timeout else 0,
            'reason': (
                f'익절 {len(tp_hit)}건, 시간초과 {len(timeout)}건 '
                f'(시간초과 평균 {sum(p["net_pnl_pct"] for p in timeout)/len(timeout):+.2f}%)'
                if timeout else f'익절 {len(tp_hit)}건'
            ),
        }

        # 5. hold_minutes 조정
        if timeout:
            timeout_with_profit = [
                p for p in timeout if p['net_pnl_pct'] > 0]
            timeout_with_loss = [
                p for p in timeout if p['net_pnl_pct'] <= 0]
            if timeout_with_loss:
                avg_loss_hold = sum(
                    p['hold_minutes'] for p in timeout_with_loss
                ) / len(timeout_with_loss)
                suggestions['hold_minutes'] = {
                    'current_default': self.config.get(
                        'strategy', {}).get('scalping', {}).get(
                            'default_hold_minutes', 10),
                    'timeout_profit_count': len(timeout_with_profit),
                    'timeout_loss_count': len(timeout_with_loss),
                    'avg_loss_hold': round(avg_loss_hold, 1),
                    'reason': (
                        f'시간초과 수익 {len(timeout_with_profit)}건 vs '
                        f'손실 {len(timeout_with_loss)}건 → '
                        f'손실 평균 {avg_loss_hold:.0f}분'
                    ),
                }

        # 6. poll_interval_seconds 조정
        if perf.get('win_rate_pct', 0) < 40:
            suggestions['poll_interval'] = {
                'current': self.config.get(
                    'strategy', {}).get('scalping', {}).get(
                        'poll_interval_seconds', 45),
                'reason': (
                    f'승률 {perf["win_rate_pct"]}% < 40% → '
                    f'스캔 주기 늘려 노이즈 줄이기 권장'
                ),
            }

        return suggestions

    # ─── 텍스트 리포트 포매팅 ───

    def format_text(self, report: dict) -> str:
        """사람이 읽을 수 있는 텍스트 리포트"""
        lines = []
        d = report['date']
        lines.append(f'{"="*60}')
        lines.append(f' SCALPING TIMING TEAM DAILY REPORT ({d})')
        lines.append(f'{"="*60}')

        if report.get('total_scalping_trades', 0) == 0:
            lines.append('당일 스캘핑 매매 없음')
            return '\n'.join(lines)

        # 종합 성과
        p = report.get('performance', {})
        lines.append('')
        lines.append('[1] 종합 성과')
        lines.append(f'  매칭 거래: {p.get("total_pairs",0)}건 '
                      f'(승 {p.get("wins",0)} / 패 {p.get("losses",0)} / '
                      f'무 {p.get("breakeven",0)})')
        lines.append(f'  승률: {p.get("win_rate_pct",0)}%')
        lines.append(f'  총 순손익: {p.get("total_net_pnl",0):+,}원')
        lines.append(f'  수익 팩터: {p.get("profit_factor",0):.2f}')
        lines.append(f'  평균 수익: +{p.get("avg_win_pct",0):.2f}% | '
                      f'평균 손실: {p.get("avg_loss_pct",0):.2f}%')
        lines.append(f'  평균 보유: {p.get("avg_hold_minutes",0):.1f}분')

        # 시간대별
        tw = report.get('time_window_analysis', [])
        lines.append('')
        lines.append('[2] 시간대별 진입 성과')
        lines.append(f'  {"시간대":<16} {"건수":>4} {"승률":>6} {"순손익":>12}')
        lines.append(f'  {"-"*42}')
        for w in tw:
            if w.get('count', 0) == 0:
                continue
            lines.append(
                f'  {w["window"]} {w["label"]:<8} '
                f'{w["count"]:>3}건 '
                f'{w.get("win_rate_pct",0):>5.1f}% '
                f'{w.get("total_pnl",0):>+10,}원'
            )

        # 점수대별
        sb = report.get('score_analysis', [])
        lines.append('')
        lines.append('[3] 점수대별 승률')
        for s in sb:
            if s.get('count', 0) == 0:
                continue
            lines.append(
                f'  {s["band"]:<10} {s["count"]:>3}건 '
                f'승률 {s.get("win_rate_pct",0):>5.1f}% '
                f'평균 {s.get("avg_pnl_pct",0):>+5.2f}% '
                f'{s.get("total_pnl",0):>+10,}원'
            )

        # 합의수준별
        ca = report.get('consensus_analysis', [])
        lines.append('')
        lines.append('[4] 합의수준별 승률')
        for c in ca:
            if c.get('count', 0) == 0:
                continue
            lines.append(
                f'  {c["level"]:<8} {c["count"]:>3}건 '
                f'승률 {c.get("win_rate_pct",0):>5.1f}% '
                f'평균 {c.get("avg_pnl_pct",0):>+5.2f}% '
                f'{c.get("total_pnl",0):>+10,}원'
            )

        # 슬리피지
        sa = report.get('slippage_analysis', {})
        if sa:
            lines.append('')
            lines.append('[5] 슬리피지 분석')
            lines.append(
                f'  평균: {sa.get("avg_slippage_pct",0):.1f}% | '
                f'최대: {sa.get("max_slippage_pct",0):.1f}% | '
                f'최소: {sa.get("min_slippage_pct",0):.1f}%')
            lines.append(
                f'  2% 초과: {sa.get("over_2pct_count",0)}/'
                f'{sa.get("total_buys",0)}건 '
                f'({sa.get("over_2pct_ratio",0):.0f}%)')

        # 미청산
        us = report.get('unsettled', [])
        if us:
            lines.append('')
            lines.append('[6] 미청산 포지션 (익일 이월)')
            for u in us:
                lines.append(
                    f'  [{u["code"]}] {u["name"]} | '
                    f'잔량: {u["remaining_qty"]:,}주 | '
                    f'추정가: {u["estimated_value"]:,}원')

        # 최적 타이밍
        ot = report.get('optimal_timing', {})
        if ot and 'message' not in ot:
            lines.append('')
            lines.append('[7] 최적 타이밍 패턴')

            bw = ot.get('best_entry_windows', [])
            if bw:
                best = bw[0]
                lines.append(
                    f'  최고 수익 시간대: {best["window"]} '
                    f'({best["label"]}) '
                    f'+{best["pnl"]:,}원 (승률 {best["win_rate"]}%)')

            sc = ot.get('optimal_score_threshold', {})
            if sc:
                lines.append(
                    f'  수익 거래 평균 점수: {sc.get("win_avg_score",0):.0f}점 | '
                    f'최소 수익 점수: {sc.get("min_profitable_score",0):.0f}점')

            hr = ot.get('optimal_hold_minutes', {})
            if hr:
                lines.append(
                    f'  수익 평균 보유: {hr.get("win_avg",0):.0f}분 | '
                    f'범위: {hr.get("best_range","N/A")}')

        # 설정 제안
        ps = report.get('parameter_suggestions', {})
        if ps:
            lines.append('')
            lines.append('[8] settings.yaml 조정 제안')
            for key, val in ps.items():
                reason = val.get('reason', '')
                if reason:
                    lines.append(f'  [{key}] {reason}')

        # 종목별 상세
        sd = report.get('stock_details', [])
        if sd:
            lines.append('')
            lines.append('[9] 종목별 상세')
            for s in sd:
                lines.append(
                    f'  [{s["code"]}] {s["name"]} | '
                    f'{s["trade_count"]}건 '
                    f'승률 {s["win_rate_pct"]:.0f}% | '
                    f'순손익 {s["total_pnl"]:+,}원 | '
                    f'평균보유 {s["avg_hold_minutes"]:.0f}분 | '
                    f'슬리피지 {s["avg_slippage_pct"]:.1f}%')
                for t in s.get('trades', []):
                    lines.append(
                        f'    {t["buy_time"][11:16]}→{t["sell_time"][11:16]} '
                        f'{t["buy_price"]:,}→{t["sell_price"]:,} '
                        f'{t["net_pnl_pct"]:+.2f}% ({t["net_amount"]:+,}원) '
                        f'[{t["exit_type"]}]')

        lines.append('')
        lines.append(f'{"="*60}')
        return '\n'.join(lines)

    def format_telegram(self, report: dict) -> str:
        """텔레그램 HTML 형식 리포트"""
        d = report['date']

        if report.get('total_scalping_trades', 0) == 0:
            return f'<b>스캘핑 리포트 ({d})</b>\n당일 스캘핑 매매 없음'

        p = report.get('performance', {})
        ot = report.get('optimal_timing', {})

        lines = [
            f'<b>🎯 스캘핑 타이밍 팀 일일 리포트 ({d})</b>',
            '',
            f'<b>종합</b>: {p.get("total_pairs",0)}건 | '
            f'승률 {p.get("win_rate_pct",0):.0f}% | '
            f'순손익 {p.get("total_net_pnl",0):+,}원',
            f'수익팩터 {p.get("profit_factor",0):.2f} | '
            f'평균보유 {p.get("avg_hold_minutes",0):.0f}분',
            '',
        ]

        # 시간대별
        tw = report.get('time_window_analysis', [])
        active = [w for w in tw if w.get('count', 0) > 0]
        if active:
            lines.append('<b>시간대별</b>')
            for w in active:
                icon = '🟢' if w.get('total_pnl', 0) > 0 else '🔴'
                lines.append(
                    f'{icon} {w["window"]}: {w["count"]}건 '
                    f'승률{w.get("win_rate_pct",0):.0f}% '
                    f'{w.get("total_pnl",0):+,}원')
            lines.append('')

        # 최적 타이밍
        if ot and 'message' not in ot:
            bw = ot.get('best_entry_windows', [])
            if bw:
                lines.append('<b>최적 패턴</b>')
                lines.append(
                    f'진입: {bw[0]["window"]} ({bw[0]["label"]})')
                sc = ot.get('optimal_score_threshold', {})
                if sc:
                    lines.append(
                        f'점수: 최소 {sc.get("min_profitable_score",0):.0f}점')
                lines.append('')

        # 미청산
        us = report.get('unsettled', [])
        if us:
            lines.append('<b>⚠️ 미청산 이월</b>')
            for u in us:
                lines.append(
                    f'[{u["code"]}] {u["name"]} '
                    f'{u["remaining_qty"]:,}주 '
                    f'({u["estimated_value"]:,}원)')
            lines.append('')

        # 핵심 제안
        ps = report.get('parameter_suggestions', {})
        if ps:
            lines.append('<b>💡 조정 제안</b>')
            for key, val in ps.items():
                reason = val.get('reason', '')
                if reason:
                    lines.append(f'• {reason}')

        return '\n'.join(lines)

    # ─── 멀티데이 트렌드 ───

    def generate_weekly_trend(self, end_date: str = None) -> dict:
        """최근 5거래일 트렌드 분석 (가중치 최적화 근거)"""
        if end_date is None:
            end_date = date.today().isoformat()

        end = date.fromisoformat(end_date)
        daily_reports = []

        for i in range(7):  # 7일 검색 (주말 포함)
            d = (end - timedelta(days=i)).isoformat()
            trades = self._load_scalping_trades(d)
            if trades:
                rpt = self.generate(d)
                daily_reports.append(rpt)
            if len(daily_reports) >= 5:
                break

        if not daily_reports:
            return {'message': '최근 스캘핑 이력 없음'}

        # 집계
        all_pairs = []
        for rpt in daily_reports:
            # 리포트에서 stock_details의 trades를 모두 수집
            for sd in rpt.get('stock_details', []):
                all_pairs.extend(sd.get('trades', []))

        return {
            'period': f'{daily_reports[-1]["date"]} ~ {daily_reports[0]["date"]}',
            'trading_days': len(daily_reports),
            'total_pairs': len(all_pairs),
            'time_window_trend': self._analyze_time_windows(all_pairs),
            'score_trend': self._analyze_score_bands(all_pairs),
            'consensus_trend': self._analyze_consensus(all_pairs),
            'optimal_timing': self._calc_optimal_timing(all_pairs),
            'parameter_suggestions': self._suggest_parameters(
                all_pairs,
                [],  # buys not available in aggregated view
            ),
        }

    # ─── I/O ───

    def _load_scalping_trades(self, target_date: str) -> List[dict]:
        """당일 스캘핑 매매만 로드"""
        trades = []
        try:
            with open(self.trade_log_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    t = json.loads(line)
                    ts = t.get('timestamp', '')
                    st = t.get('strategy_type', '')
                    reason = t.get('reason', '')
                    exit_type = t.get('exit_type', '')
                    if (ts.startswith(target_date)
                            and (st == 'scalping'
                                 or '스캘핑' in reason
                                 or '스캘핑' in exit_type)):
                        trades.append(t)
        except FileNotFoundError:
            pass
        return trades

    def _save_report(self, report: dict, target_date: str):
        filepath = self.report_dir / f'scalping_{target_date}.json'
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"스캘핑 리포트 저장: {filepath}")
        except Exception as e:
            logger.error(f"스캘핑 리포트 저장 실패: {e}")
