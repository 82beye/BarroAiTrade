"""
1분봉 캔들 데이터 기반 스캘핑 전략 백테스트 시뮬레이터

4/6 캔들 데이터(14종목)로 고도화된 전략 로직을 시뮬레이션.
실제 coordinator → agent 분석 → 진입/청산 흐름을 재현한다.

사용법:
  python3 scripts/backtest_candle_sim.py [날짜 YYYYMMDD]
"""

import json
import sys
import logging
from pathlib import Path
from datetime import datetime, time, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── 로깅 설정 ──
logging.basicConfig(level=logging.WARNING, format='%(message)s')
logger = logging.getLogger('sim')
logger.setLevel(logging.WARNING)

# 에이전트 로깅 억제
for mod in ['strategy.scalping_team', 'strategy.exit_signal',
            'strategy.scalping_team.coordinator',
            'strategy.scalping_team.pullback_agent',
            'strategy.scalping_team.momentum_burst_agent',
            'strategy.scalping_team.spread_tape_agent',
            'strategy.scalping_team.golden_time_agent',
            'strategy.scalping_team.breakout_confirm_agent',
            'strategy.scalping_team.candle_pattern_agent',
            'strategy.scalping_team.volume_profile_agent',
            'strategy.scalping_team.relative_strength_agent',
            'strategy.scalping_team.risk_reward_agent',
            'strategy.scalping_team.vwap_agent']:
    logging.getLogger(mod).setLevel(logging.CRITICAL)


# ── 캔들 로더 ──

def load_candles(date_str: str) -> Dict[str, dict]:
    """logs/candles/ 에서 해당 날짜 캔들 로드 + 전일종가 추출"""
    candle_dir = PROJECT_ROOT / "logs" / "candles"
    all_candles = {}

    for f in candle_dir.glob(f"candle_1m_*_{date_str}.json"):
        with open(f, 'r') as fh:
            data = json.load(fh)
        code = data.get('code', f.stem.split('_')[2])
        name = data.get('name', code)
        candles = data.get('candles', [])

        # 해당일 캔들
        day_candles = [
            c for c in candles
            if str(c.get('time', '')).startswith(date_str)
        ]
        # 전일 캔들 (전일종가 추출용)
        prev_candles = [
            c for c in candles
            if not str(c.get('time', '')).startswith(date_str)
        ]
        # 전일종가 = 전일 마지막 봉의 종가
        prev_close = 0
        if prev_candles:
            prev_candles.sort(key=lambda c: c['time'])
            prev_close = prev_candles[-1]['price']

        if day_candles:
            day_candles.sort(key=lambda c: c['time'])
            # 전일종가 못 구하면 시가의 90% (갭업 추정)
            if prev_close == 0:
                prev_close = int(day_candles[0]['open'] * 0.90)
            all_candles[code] = {
                'name': name,
                'candles': day_candles,
                'prev_close': prev_close,
            }

    return all_candles


def load_actual_trades(date_str: str) -> List[dict]:
    """trades.jsonl에서 해당일 실제 매매 로드"""
    formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    trades = []
    path = PROJECT_ROOT / "logs" / "trades.jsonl"
    if not path.exists():
        return trades
    with open(path, 'r') as f:
        for line in f:
            t = json.loads(line.strip())
            if t.get('timestamp', '').startswith(formatted):
                trades.append(t)
    return trades


# ── StockSnapshot 빌더 ──

def build_snapshot(code: str, name: str, candles: List[dict],
                   idx: int, prev_close: float):
    """캔들 데이터에서 StockSnapshot 빌드"""
    from strategy.scalping_team.base_agent import StockSnapshot

    current = candles[idx]
    price = current['price']
    high = max(c['high'] for c in candles[:idx + 1])
    low = min(c['low'] for c in candles[:idx + 1])
    open_price = candles[0]['open']
    volume = sum(c['volume'] for c in candles[:idx + 1])

    # 급등주는 보통 거래량 3~10배 → 고정 추정
    # (실제로는 20일 평균 대비, 캔들만으로는 정확히 알 수 없음)
    vol_ratio = 5.0  # 급등주 기본 거래량 비율

    change_pct = (price - prev_close) / prev_close * 100 if prev_close > 0 else 0

    trade_value = price * volume

    snapshot = StockSnapshot(
        code=code,
        name=name,
        price=price,
        open=open_price,
        high=high,
        low=low,
        prev_close=prev_close,
        volume=volume,
        change_pct=change_pct,
        trade_value=trade_value,
        volume_ratio=max(vol_ratio, 1.0),
        category="급등주",
        score=60.0,
    )

    return snapshot


# ── 시뮬레이션 포지션 ──

@dataclass
class SimPosition:
    code: str
    name: str
    entry_price: float
    qty: int
    entry_time: str  # HHMM
    entry_idx: int
    scalp_tp_pct: float = 2.5
    scalp_sl_pct: float = -1.5
    scalp_hold_minutes: int = 15
    high_watermark: float = 0.0
    trailing_active: bool = False
    min_price: float = 0.0

    def __post_init__(self):
        self.high_watermark = self.entry_price
        self.min_price = self.entry_price


@dataclass
class SimTrade:
    code: str
    name: str
    action: str
    price: float
    qty: int
    time: str
    reason: str = ""
    pnl_pct: float = 0.0
    entry_price: float = 0.0
    hold_minutes: int = 0


# ── 메인 시뮬레이터 ──

class CandleBacktester:
    """1분봉 기반 스캘핑 전략 백테스터"""

    # 수수료+세금
    FEE_PCT = 0.21  # 왕복 0.21%
    MAX_POSITIONS = 5
    MAX_SIMULTANEOUS = 2
    BUDGET_PER_TRADE = 1_500_000  # 종목당 150만원
    MIN_SCORE = 50  # 진입 최소 점수
    MIN_SCORE_10H = 60  # 10시대 최소 점수

    def __init__(self, date_str: str):
        self.date_str = date_str
        self.positions: Dict[str, SimPosition] = {}
        self.trades: List[SimTrade] = []
        self.daily_entries: Dict[str, int] = defaultdict(int)  # code → entry count

        # 전략 초기화
        import yaml
        config_path = PROJECT_ROOT / "config" / "settings.yaml"
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        from strategy.scalping_team.coordinator import ScalpingCoordinator
        from strategy.exit_signal import ExitSignalGenerator

        self.coordinator = ScalpingCoordinator(self.config)
        self.exit_gen = ExitSignalGenerator(self.config)

    def run(self, candle_data: Dict[str, dict]) -> dict:
        """전체 시뮬레이션 실행"""
        print(f"\n{'='*70}")
        print(f"  스캘핑 전략 백테스트 시뮬레이션 — {self.date_str}")
        print(f"  종목: {len(candle_data)}개 | 고도화 전략 적용")
        print(f"{'='*70}\n")

        # 각 종목의 전일종가 (캔들 데이터에서 추출)
        prev_closes = {}
        for code, data in candle_data.items():
            prev_closes[code] = data.get('prev_close', data['candles'][0]['open'])

        # 시간 순으로 모든 봉을 통합 스캔 (1분 단위)
        # 각 분봉마다: 1) 청산 체크 2) 진입 체크
        all_times = set()
        for code, data in candle_data.items():
            for c in data['candles']:
                all_times.add(c['time'])

        sorted_times = sorted(all_times)

        # 분봉별 코드→인덱스 매핑
        code_idx_map = {}  # {code: {time: idx}}
        for code, data in candle_data.items():
            idx_map = {}
            for i, c in enumerate(data['candles']):
                idx_map[c['time']] = i
            code_idx_map[code] = idx_map

        scan_interval = 1  # 1분마다 스캔 (최대 기회 포착)
        last_scan_idx = -scan_interval

        for time_idx, current_time in enumerate(sorted_times):
            # 시간 파싱
            hhmm = current_time[8:12]
            hh, mm = int(hhmm[:2]), int(hhmm[2:4])

            # 09:05 이전 스킵 (극초반 노이즈)
            if hh == 9 and mm < 5:
                continue
            # 14:50 이후 강제 청산
            if hh == 14 and mm >= 50:
                self._force_close_all(candle_data, code_idx_map, current_time)
                break
            # 14:00 이후 신규 진입 금지 (청산만)
            after_14 = hh >= 14

            # ── 1) 보유 포지션 청산 체크 (매 분) ──
            self._check_exits(candle_data, code_idx_map, current_time)

            # ── 2) 진입 스캔 (3분 간격) ──
            if time_idx - last_scan_idx >= scan_interval and not after_14:
                last_scan_idx = time_idx
                self._scan_entries(
                    candle_data, code_idx_map, current_time,
                    prev_closes, hh, mm)

        # 남은 포지션 강제 청산
        if self.positions:
            last_time = sorted_times[-1] if sorted_times else ""
            self._force_close_all(candle_data, code_idx_map, last_time)

        return self._generate_report(candle_data)

    def _scan_entries(self, candle_data, code_idx_map, current_time,
                      prev_closes, hh, mm):
        """진입 후보 스캔 + coordinator 분석"""
        from strategy.scalping_team.base_agent import StockSnapshot
        import pandas as pd

        if len(self.positions) >= self.MAX_SIMULTANEOUS:
            return

        # 데드존 (11:00-13:00) 스킵 — 골든타임에이전트 동기화
        if 11 <= hh < 13:
            return

        snapshots = []
        intraday_map = {}

        for code, data in candle_data.items():
            if code in self.positions:
                continue
            if self.daily_entries[code] >= 2:  # 재진입 제한
                continue

            idx_map = code_idx_map[code]
            if current_time not in idx_map:
                continue
            idx = idx_map[current_time]
            if idx < 10:  # 최소 10봉 필요
                continue

            candles = data['candles']
            prev_close = prev_closes[code]
            snapshot = build_snapshot(
                code, data['name'], candles, idx, prev_close)

            # 진입 구간 확인 (5~25%)
            if snapshot.change_pct < 5 or snapshot.change_pct > 25:
                continue

            snapshots.append(snapshot)
            # intraday_prices 구성 (최근 30봉)
            start = max(0, idx - 29)
            intraday_map[code] = [
                {'price': c['price'], 'volume': c['volume'],
                 'time': c['time'], 'open': c['open'],
                 'high': c['high'], 'low': c['low']}
                for c in candles[start:idx + 1]
            ]

        if not snapshots:
            return

        # coordinator 분석 실행
        results = self.coordinator.analyze(
            snapshots,
            cache_data={},  # OHLCV 없음 (당일 봉만)
            intraday_data=intraday_map,
        )

        # 10시대 최소 점수 필터
        min_score = self.MIN_SCORE_10H if hh == 10 else self.MIN_SCORE

        # 즉시 진입 시그널 처리
        for analysis in results:
            if len(self.positions) >= self.MAX_SIMULTANEOUS:
                break
            if analysis.timing != "즉시":
                continue
            if analysis.total_score < min_score:
                continue

            code = analysis.code
            idx_map = code_idx_map[code]
            if current_time not in idx_map:
                continue
            idx = idx_map[current_time]
            candle = candle_data[code]['candles'][idx]

            # 2026-04-07: 진입 캔들 양봉 필수 (음봉 = 하락 중 진입 금지)
            if candle['price'] < candle['open']:
                continue
            # 진입 캔들 윗꼬리 > 35% → 매도 압력 강함
            c_range = candle['high'] - candle['low']
            if c_range > 0:
                c_wick = (candle['high'] - candle['price']) / c_range * 100
                if c_wick > 35:
                    continue

            entry_price = candle['price']
            qty = max(1, int(self.BUDGET_PER_TRADE / entry_price))

            self.positions[code] = SimPosition(
                code=code,
                name=analysis.name,
                entry_price=entry_price,
                qty=qty,
                entry_time=current_time[8:12],
                entry_idx=idx,
                scalp_tp_pct=analysis.scalp_tp_pct,
                scalp_sl_pct=analysis.scalp_sl_pct,
                scalp_hold_minutes=analysis.hold_minutes or 15,
            )
            self.daily_entries[code] += 1
            self.coordinator.record_entry(code)

            self.trades.append(SimTrade(
                code=code, name=analysis.name, action='BUY',
                price=entry_price, qty=qty,
                time=current_time[8:12],
                reason=f"점수 {analysis.total_score:.0f} | "
                       f"{analysis.consensus_level} | "
                       f"TP +{analysis.scalp_tp_pct:.1f}% "
                       f"SL {analysis.scalp_sl_pct:.1f}%",
            ))

    def _check_exits(self, candle_data, code_idx_map, current_time):
        """보유 포지션 청산 체크"""
        to_close = []

        for code, pos in self.positions.items():
            if code not in candle_data:
                continue
            idx_map = code_idx_map[code]
            if current_time not in idx_map:
                continue

            idx = idx_map[current_time]
            candle = candle_data[code]['candles'][idx]
            current_price = candle['price']
            candle_low = candle['low']
            candle_high = candle['high']

            # 고점/저점 업데이트
            if candle_high > pos.high_watermark:
                pos.high_watermark = candle_high
            if candle_low < pos.min_price:
                pos.min_price = candle_low

            pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
            hold_minutes = idx - pos.entry_idx

            # 최소 보유 시간 (30초 → 약 1분봉 기준 0분은 패스)
            if hold_minutes < 1 and pnl_pct > -2.0:
                continue

            # 트레일링 활성화
            if pnl_pct >= 1.0:
                pos.trailing_active = True

            exit_reason = None

            # 1. 시간별 SL
            if not pos.trailing_active:
                if hold_minutes <= 2:
                    effective_sl = -0.5
                elif hold_minutes <= 5:
                    effective_sl = -1.0
                else:
                    effective_sl = pos.scalp_sl_pct
            else:
                effective_sl = pos.scalp_sl_pct

            # 봉 내 저가로 SL 체크 (실제로는 봉 내에서 SL 도달 가능)
            low_pnl = (candle_low - pos.entry_price) / pos.entry_price * 100
            if low_pnl <= effective_sl:
                exit_price = pos.entry_price * (1 + effective_sl / 100)
                exit_reason = f"손절 {effective_sl}% (봉저가 기반)"
                current_price = exit_price

            # 2. 트레일링 스톱
            if not exit_reason and pos.trailing_active:
                high_pnl = (pos.high_watermark - pos.entry_price) / pos.entry_price * 100
                drop_from_high = (current_price - pos.high_watermark) / pos.high_watermark * 100

                # 구간별 trail 폭 (고도화 버전)
                if high_pnl >= 3.0:
                    trail = -1.0
                elif high_pnl >= 2.0:
                    trail = -1.2
                else:
                    trail = -1.5

                if drop_from_high <= trail:
                    exit_reason = (
                        f"트레일링 고점 {pos.high_watermark:,.0f}"
                        f"(+{high_pnl:.1f}%) → {drop_from_high:+.1f}%")

                # 브레이크이븐
                if pnl_pct <= 0.5 and pos.trailing_active:
                    exit_reason = f"브레이크이븐 {pnl_pct:+.1f}%"

            # 3. TP
            if not exit_reason:
                high_pnl = (candle_high - pos.entry_price) / pos.entry_price * 100
                if high_pnl >= pos.scalp_tp_pct:
                    exit_price = pos.entry_price * (1 + pos.scalp_tp_pct / 100)
                    current_price = exit_price
                    exit_reason = f"익절 +{pos.scalp_tp_pct:.1f}%"

            # 4. 시간 초과
            if not exit_reason and hold_minutes >= pos.scalp_hold_minutes:
                exit_reason = f"시간초과 {hold_minutes}분"

            if exit_reason:
                final_pnl = (current_price - pos.entry_price) / pos.entry_price * 100
                net_pnl = final_pnl - self.FEE_PCT

                self.trades.append(SimTrade(
                    code=code, name=pos.name, action='SELL',
                    price=round(current_price),
                    qty=pos.qty,
                    time=current_time[8:12],
                    reason=exit_reason,
                    pnl_pct=round(net_pnl, 2),
                    entry_price=pos.entry_price,
                    hold_minutes=hold_minutes,
                ))

                result = 'win' if net_pnl > 0 else 'loss'
                self.coordinator.record_exit(code, result, net_pnl)
                to_close.append(code)

        for code in to_close:
            del self.positions[code]

    def _force_close_all(self, candle_data, code_idx_map, current_time):
        """잔여 포지션 강제 청산"""
        for code in list(self.positions.keys()):
            pos = self.positions[code]
            if code in candle_data:
                idx_map = code_idx_map[code]
                if current_time in idx_map:
                    idx = idx_map[current_time]
                    price = candle_data[code]['candles'][idx]['price']
                else:
                    price = candle_data[code]['candles'][-1]['price']
            else:
                price = pos.entry_price

            pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
            net_pnl = pnl_pct - self.FEE_PCT
            hold_min = 0

            self.trades.append(SimTrade(
                code=code, name=pos.name, action='SELL',
                price=price, qty=pos.qty,
                time=current_time[8:12] if len(current_time) >= 12 else "1450",
                reason="강제청산",
                pnl_pct=round(net_pnl, 2),
                entry_price=pos.entry_price,
                hold_minutes=hold_min,
            ))

        self.positions.clear()

    def _generate_report(self, candle_data) -> dict:
        """시뮬레이션 결과 리포트 생성"""
        buys = [t for t in self.trades if t.action == 'BUY']
        sells = [t for t in self.trades if t.action == 'SELL']

        # 매수-매도 페어링
        pairs = []
        buy_map = {}
        for t in self.trades:
            if t.action == 'BUY':
                buy_map[t.code] = buy_map.get(t.code, [])
                buy_map[t.code].append(t)
            elif t.action == 'SELL':
                buys_for_code = buy_map.get(t.code, [])
                if buys_for_code:
                    buy_t = buys_for_code.pop(0)
                    pairs.append((buy_t, t))

        wins = [s for _, s in pairs if s.pnl_pct > 0]
        losses = [s for _, s in pairs if s.pnl_pct <= 0]
        total_pnl = sum(s.pnl_pct for _, s in pairs)
        total_pnl_amount = sum(
            s.pnl_pct / 100 * b.price * b.qty
            for b, s in pairs
        )

        win_rate = len(wins) / len(pairs) * 100 if pairs else 0
        avg_win = sum(s.pnl_pct for s in wins) / len(wins) if wins else 0
        avg_loss = sum(s.pnl_pct for s in losses) / len(losses) if losses else 0
        gross_profit = sum(
            s.pnl_pct / 100 * b.price * b.qty for b, s in pairs if s.pnl_pct > 0)
        gross_loss = sum(
            s.pnl_pct / 100 * b.price * b.qty for b, s in pairs if s.pnl_pct <= 0)
        pf = abs(gross_profit / gross_loss) if gross_loss != 0 else float('inf')

        # 시간대별 분석
        time_windows = {
            '09:00-09:30': (900, 930),
            '09:30-10:00': (930, 1000),
            '10:00-11:00': (1000, 1100),
            '13:00-14:00': (1300, 1400),
        }
        tw_results = {}
        for label, (start, end) in time_windows.items():
            tw_pairs = [
                (b, s) for b, s in pairs
                if start <= int(b.time[:4]) < end
            ]
            if tw_pairs:
                tw_wins = sum(1 for _, s in tw_pairs if s.pnl_pct > 0)
                tw_pnl = sum(
                    s.pnl_pct / 100 * b.price * b.qty
                    for b, s in tw_pairs)
                tw_results[label] = {
                    'count': len(tw_pairs),
                    'wins': tw_wins,
                    'win_rate': tw_wins / len(tw_pairs) * 100,
                    'pnl': round(tw_pnl),
                }

        # ── 결과 출력 ──
        print(f"\n{'='*70}")
        print(f"  시뮬레이션 결과 ({self.date_str})")
        print(f"{'='*70}\n")

        print(f"  총 매매: {len(pairs)}쌍 (매수 {len(buys)}건, 매도 {len(sells)}건)")
        print(f"  승률: {win_rate:.1f}% ({len(wins)}승 / {len(losses)}패)")
        print(f"  총 손익: {total_pnl_amount:+,.0f}원 ({total_pnl:+.2f}%)")
        print(f"  평균 수익: {avg_win:+.2f}% | 평균 손실: {avg_loss:+.2f}%")
        print(f"  Profit Factor: {pf:.2f}")

        # 시간대별
        print(f"\n  {'시간대':<15} {'건수':>4} {'승':>3} {'승률':>6} {'손익':>12}")
        print(f"  {'-'*45}")
        for label, r in tw_results.items():
            print(f"  {label:<15} {r['count']:>4} {r['wins']:>3} "
                  f"{r['win_rate']:>5.1f}% {r['pnl']:>+11,}원")

        # 개별 매매 상세
        print(f"\n  {'No':>3} {'시간':>5} {'종목':>10} {'구분':>4} "
              f"{'가격':>10} {'수익률':>8} {'보유':>4} {'사유'}")
        print(f"  {'-'*80}")

        for i, (buy_t, sell_t) in enumerate(pairs, 1):
            icon = "✅" if sell_t.pnl_pct > 0 else "❌"
            print(f"  {i:>3} {buy_t.time[:2]}:{buy_t.time[2:4]} "
                  f"{buy_t.name:>10} 매수 {buy_t.price:>10,.0f}원 "
                  f"{'':>8} {'':>4} {buy_t.reason[:40]}")
            print(f"  {'':>3} {sell_t.time[:2]}:{sell_t.time[2:4]} "
                  f"{'':>10} 매도 {sell_t.price:>10,.0f}원 "
                  f"{sell_t.pnl_pct:>+7.2f}% {sell_t.hold_minutes:>3}분 "
                  f"{icon} {sell_t.reason[:35]}")

        print(f"\n{'='*70}")

        # 실제 매매와 비교
        actual = load_actual_trades(self.date_str)
        actual_sells = [t for t in actual
                        if t.get('action') == 'SELL'
                        and t.get('strategy_type') == 'scalping']
        if actual_sells:
            actual_pnl = sum(t.get('pnl_pct', 0) for t in actual_sells)
            actual_net = sum(t.get('net_pnl_pct', t.get('pnl_pct', 0))
                            for t in actual_sells)
            actual_wins = sum(1 for t in actual_sells
                              if t.get('pnl_pct', 0) > 0)
            actual_wr = actual_wins / len(actual_sells) * 100

            print(f"\n  📊 실제 매매 vs 시뮬레이션 비교")
            print(f"  {'-'*50}")
            print(f"  {'':>20} {'실제':>12} {'시뮬레이션':>12} {'차이':>10}")
            print(f"  {'매매 수':>20} {len(actual_sells):>12} {len(pairs):>12} "
                  f"{len(pairs)-len(actual_sells):>+10}")
            print(f"  {'승률':>20} {actual_wr:>11.1f}% {win_rate:>11.1f}% "
                  f"{win_rate-actual_wr:>+9.1f}%")
            print(f"  {'총 수익률':>20} {actual_pnl:>+11.2f}% {total_pnl:>+11.2f}% "
                  f"{total_pnl-actual_pnl:>+9.2f}%")
            print(f"  {'총 손익':>20} {'':>12} {total_pnl_amount:>+11,.0f}원")
            print(f"  {'PF':>20} {'':>12} {pf:>11.2f}")
            print(f"\n{'='*70}")

        return {
            'pairs': len(pairs),
            'win_rate': win_rate,
            'total_pnl_pct': total_pnl,
            'total_pnl_amount': total_pnl_amount,
            'profit_factor': pf,
        }


# ── 메인 ──

def main():
    date_str = "20260406"
    if len(sys.argv) > 1:
        date_str = sys.argv[1]

    candle_data = load_candles(date_str)
    if not candle_data:
        print(f"캔들 데이터 없음: logs/candles/*_{date_str}.json")
        print("먼저 fetch_candles_and_analyze.py로 캔들 데이터를 다운로드하세요.")
        return

    print(f"로드된 캔들: {len(candle_data)}종목")
    for code, data in sorted(candle_data.items()):
        pc = data.get('prev_close', 0)
        first_open = data['candles'][0]['open']
        gap = (first_open - pc) / pc * 100 if pc > 0 else 0
        print(f"  [{code}] {data['name']}: {len(data['candles'])}봉 | "
              f"전일종가 {pc:,} → 시가 {first_open:,} (갭 {gap:+.1f}%)")

    # ── 고도화 전략 시뮬레이션 ──
    sim = CandleBacktester(date_str)
    result = sim.run(candle_data)


if __name__ == '__main__':
    main()
