"""
스캘핑 전략 백테스트 — 일봉 캔들 기반 매수/매도 시뮬레이션

일봉 OHLCV 데이터를 사용하여 스캘핑 전략의 실제 수익률을 산출한다.

시뮬레이션 방법:
  1. 대상일(D)에서 상승 종목 추출 → ScalpingCoordinator 10 에이전트 분석
  2. "즉시 매수" 시그널 종목에 대해 매수 진입
  3. 매수가: 눌림목 진입가 = high - 0.382 × (high - open) [38.2% 피보나치 되돌림]
  4. 매도 시뮬레이션 (일봉 범위 내):
     - TP 도달 여부: high >= entry × (1 + tp%)
     - SL 도달 여부: low <= entry × (1 + sl%)
     - 양쪽 모두 가능: close > entry → TP 우선, 아니면 SL 우선
     - 미도달: 종가 청산 (시간 초과)
  5. 일일 정산 수익률 산출

Usage:
    python scripts/scalping_backtest.py                       # 최근 20거래일
    python scripts/scalping_backtest.py --date 20260330       # 특정일
    python scripts/scalping_backtest.py --days 30             # 최근 30거래일
    python scripts/scalping_backtest.py --start 20260301 --end 20260331
"""

import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime, time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / "config" / ".env")

from strategy.scalping_team.coordinator import ScalpingCoordinator
from strategy.scalping_team.base_agent import StockSnapshot
from main import load_config

logging.basicConfig(level=logging.WARNING, format='%(message)s')

# ── 수수료/세금 ──
BUY_FEE_PCT = 0.015       # 매수 수수료 0.015%
SELL_FEE_PCT = 0.015       # 매도 수수료 0.015%
SELL_TAX_PCT = 0.18        # 거래세 0.18%
TOTAL_COST_PCT = BUY_FEE_PCT + SELL_FEE_PCT + SELL_TAX_PCT  # 0.21%


@dataclass
class TradeResult:
    """단일 매매 결과"""
    date: str
    code: str
    name: str
    entry_price: int
    exit_price: int
    qty: int
    tp_pct: float
    sl_pct: float
    exit_type: str          # TP / SL / TRAILING / TIME_EXIT
    gross_pnl_pct: float    # 수수료 전 수익률
    net_pnl_pct: float      # 수수료 후 수익률
    net_pnl_amount: int     # 순수익 금액
    score: float
    hold_minutes: float
    change_pct: float       # 당일 등락률


@dataclass
class DailySummary:
    """일일 정산"""
    date: str
    total_trades: int
    tp_count: int
    sl_count: int
    trail_count: int
    time_exit_count: int
    win_count: int
    loss_count: int
    win_rate: float
    gross_pnl_pct: float
    net_pnl_pct: float
    net_pnl_amount: int
    trades: List[TradeResult] = field(default_factory=list)


def load_ohlcv_cache(cache_dir: str) -> dict:
    """캐시 디렉토리에서 전 종목 OHLCV 로드"""
    cache_path = Path(cache_dir)
    data = {}
    for f in cache_path.glob("*.json"):
        code = f.stem
        try:
            with open(f, 'r') as fh:
                raw = json.load(fh)
            records = raw.get('data', raw) if isinstance(raw, dict) else raw
            if isinstance(records, list):
                df = pd.DataFrame(records)
            elif isinstance(records, dict):
                df = pd.DataFrame(records)
            else:
                continue
            if 'date' in df.columns:
                df['date'] = df['date'].astype(str)
                df = df.sort_values('date').reset_index(drop=True)
            data[code] = df
        except Exception:
            continue
    return data


def load_stock_names(config: dict) -> dict:
    """종목명 캐시 로드"""
    cache_path = Path(config.get('scanner', {}).get(
        'cache_dir', './data/ohlcv_cache')).parent / 'stock_names.json'
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)
    return {}


def get_trading_dates(all_ohlcv: dict, start: str = None, end: str = None,
                      days: int = None) -> List[str]:
    """거래일 목록 추출"""
    all_dates = set()
    for code, df in list(all_ohlcv.items())[:200]:
        if 'date' in df.columns:
            all_dates.update(df['date'].tolist())
    dates = sorted(all_dates)

    if start and end:
        dates = [d for d in dates if start <= d <= end]
    elif days:
        dates = dates[-days:]

    return dates


def find_surge_stocks(all_ohlcv: dict, target_date: str,
                      min_change: float = 5.0,
                      max_change: float = 30.0) -> list:
    """대상일 상승 종목 추출 + 일봉 데이터 포함"""
    candidates = []
    for code, df in all_ohlcv.items():
        if 'date' not in df.columns or len(df) < 2:
            continue
        row = df[df['date'] == target_date]
        if row.empty:
            continue
        idx = row.index[0]
        if idx == 0:
            continue

        prev_row = df.iloc[idx - 1]
        cur = row.iloc[0]

        prev_close = int(prev_row['close'])
        cur_open = int(cur['open'])
        cur_high = int(cur['high'])
        cur_low = int(cur['low'])
        cur_close = int(cur['close'])
        cur_volume = int(cur['volume'])

        if prev_close <= 0 or cur_high <= cur_low:
            continue

        change_pct = (cur_close - prev_close) / prev_close * 100
        if change_pct < min_change or change_pct > max_change:
            continue

        # 20일 평균 거래량
        start_idx = max(0, idx - 20)
        avg_vol_20 = df.iloc[start_idx:idx]['volume'].mean()
        vol_ratio = cur_volume / avg_vol_20 if avg_vol_20 > 0 else 0
        trade_value = cur_close * cur_volume

        # 거래대금 최소 50억 이상만
        if trade_value < 5_000_000_000:
            continue

        candidates.append({
            'code': code,
            'prev_close': prev_close,
            'open': cur_open,
            'high': cur_high,
            'low': cur_low,
            'close': cur_close,
            'volume': cur_volume,
            'change_pct': round(change_pct, 2),
            'volume_ratio': round(vol_ratio, 2),
            'trade_value': trade_value,
        })

    candidates.sort(key=lambda x: x['change_pct'], reverse=True)
    return candidates


def simulate_trade(entry_price: int, tp_pct: float, sl_pct: float,
                   high: int, low: int, close: int,
                   trailing_activation: float = 1.5,
                   trailing_trail: float = -1.2) -> Tuple[str, int, float]:
    """
    일봉 범위 내 매매 시뮬레이션

    Returns: (exit_type, exit_price, gross_pnl_pct)

    시뮬레이션 로직:
    - 매수가(entry)에서 TP/SL/트레일링 체크
    - 고가에서 TP 도달 가능한지, 저가에서 SL 도달 가능한지 확인
    - 트레일링: 고가에서 activation 이상 상승 후, 고가 대비 trail% 하락 시 매도
    """
    if entry_price <= 0:
        return 'SKIP', entry_price, 0.0

    tp_price = int(entry_price * (1 + tp_pct / 100))
    sl_price = int(entry_price * (1 + sl_pct / 100))

    can_tp = high >= tp_price
    can_sl = low <= sl_price

    # 트레일링 스탑 시뮬레이션
    # 고점이 activation% 이상이면, 고점에서 trail% 하락한 가격으로 매도
    high_from_entry_pct = (high - entry_price) / entry_price * 100
    trailing_exit = None
    if high_from_entry_pct >= trailing_activation:
        trailing_price = int(high * (1 + trailing_trail / 100))
        # 트레일링 매도가가 TP보다 높으면 트레일링 우선
        if trailing_price > tp_price:
            trailing_exit = trailing_price

    if can_tp and not can_sl:
        # TP만 도달
        exit_price = trailing_exit if trailing_exit else tp_price
        exit_type = 'TRAILING' if trailing_exit else 'TP'
    elif can_sl and not can_tp:
        # SL만 도달
        exit_price = sl_price
        exit_type = 'SL'
    elif can_tp and can_sl:
        # 양쪽 도달 가능 → 종가 방향으로 추정
        if close >= entry_price:
            # 종가가 매수가 이상 → TP 먼저 도달했을 가능성 높음
            exit_price = trailing_exit if trailing_exit else tp_price
            exit_type = 'TRAILING' if trailing_exit else 'TP'
        else:
            # 종가가 매수가 이하 → SL 먼저 도달 추정
            exit_price = sl_price
            exit_type = 'SL'
    else:
        # 미도달 → 종가 청산 (시간 초과)
        exit_price = close
        exit_type = 'TIME_EXIT'

    gross_pnl_pct = (exit_price - entry_price) / entry_price * 100
    return exit_type, exit_price, round(gross_pnl_pct, 3)


def run_backtest_day(date: str, candidates: list, all_ohlcv: dict,
                     stock_names: dict, config: dict,
                     total_equity: int = 50_000_000) -> Optional[DailySummary]:
    """단일 거래일 백테스트"""
    if not candidates:
        return None

    scalp_cfg = config.get('strategy', {}).get('scalping', {})
    max_per_stock_pct = scalp_cfg.get('max_per_stock_pct', 5.0) / 100
    initial_ratio = scalp_cfg.get('initial_entry_ratio', 0.6)
    trailing_activation = scalp_cfg.get('trailing_stop', {}).get('activation_pct', 1.5)
    trailing_trail = scalp_cfg.get('trailing_stop', {}).get('trail_pct', -1.2)

    year = int(date[:4])
    month = int(date[4:6])
    day = int(date[6:8])

    # StockSnapshot 생성
    snapshots = []
    ohlcv_cache = {}
    candidate_map = {}

    for c in candidates[:50]:
        code = c['code']
        name = stock_names.get(code, code)
        snap = StockSnapshot(
            code=code, name=name,
            price=c['close'], open=c['open'],
            high=c['high'], low=c['low'],
            prev_close=c['prev_close'],
            volume=c['volume'],
            change_pct=c['change_pct'],
            trade_value=c['trade_value'],
            volume_ratio=c['volume_ratio'],
            category='급등주' if c['change_pct'] >= 15 else '강세주',
            score=c['change_pct'] * 3,
        )
        snapshots.append(snap)
        if code in all_ohlcv:
            ohlcv_cache[code] = all_ohlcv[code]
        candidate_map[code] = c

    if not snapshots:
        return None

    # 시간 오버라이드 (오전 10시 기준)
    coordinator = ScalpingCoordinator(config)
    sim_dt = datetime(year, month, day, 10, 0, 0)

    with patch('strategy.scalping_team.coordinator.datetime') as mock_dt:
        mock_dt.now.return_value = sim_dt
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        mock_dt.strptime = datetime.strptime
        results = coordinator.analyze(
            snapshots, cache_data=ohlcv_cache, intraday_data={})

    # 즉시 매수 + 대기 시그널 모두 포함 (매수 가능 시그널)
    buy_signals = [r for r in results
                   if r.timing in ('즉시',) and r.total_score >= 50]

    if not buy_signals:
        return DailySummary(
            date=date, total_trades=0,
            tp_count=0, sl_count=0, trail_count=0, time_exit_count=0,
            win_count=0, loss_count=0, win_rate=0.0,
            gross_pnl_pct=0.0, net_pnl_pct=0.0, net_pnl_amount=0,
        )

    # 매매 시뮬레이션
    trades = []
    for sig in buy_signals:
        code = sig.code
        c = candidate_map.get(code)
        if not c:
            continue

        name = stock_names.get(code, code)
        o, h, l, cl = c['open'], c['high'], c['low'], c['close']

        # 매수가: 눌림목 진입 (38.2% 피보나치 되돌림)
        # entry = high - 0.382 × (high - open)
        rally = h - o
        if rally <= 0:
            continue
        entry_price = int(h - 0.382 * rally)
        if entry_price <= 0:
            continue

        # 포지션 사이징
        max_amount = int(total_equity * max_per_stock_pct * initial_ratio)
        qty = max_amount // entry_price
        if qty <= 0:
            continue

        tp_pct = sig.scalp_tp_pct
        sl_pct = sig.scalp_sl_pct

        # 매도 시뮬레이션
        exit_type, exit_price, gross_pnl_pct = simulate_trade(
            entry_price, tp_pct, sl_pct,
            h, l, cl,
            trailing_activation, trailing_trail,
        )

        net_pnl_pct = round(gross_pnl_pct - TOTAL_COST_PCT, 3)
        invested = entry_price * qty
        net_pnl_amount = int(invested * net_pnl_pct / 100)

        trades.append(TradeResult(
            date=date, code=code, name=name,
            entry_price=entry_price, exit_price=exit_price,
            qty=qty, tp_pct=tp_pct, sl_pct=sl_pct,
            exit_type=exit_type,
            gross_pnl_pct=gross_pnl_pct, net_pnl_pct=net_pnl_pct,
            net_pnl_amount=net_pnl_amount,
            score=sig.total_score,
            hold_minutes=sig.hold_minutes,
            change_pct=c['change_pct'],
        ))

    if not trades:
        return DailySummary(
            date=date, total_trades=0,
            tp_count=0, sl_count=0, trail_count=0, time_exit_count=0,
            win_count=0, loss_count=0, win_rate=0.0,
            gross_pnl_pct=0.0, net_pnl_pct=0.0, net_pnl_amount=0,
        )

    tp_count = sum(1 for t in trades if t.exit_type == 'TP')
    sl_count = sum(1 for t in trades if t.exit_type == 'SL')
    trail_count = sum(1 for t in trades if t.exit_type == 'TRAILING')
    time_count = sum(1 for t in trades if t.exit_type == 'TIME_EXIT')
    win_count = sum(1 for t in trades if t.net_pnl_pct > 0)
    loss_count = sum(1 for t in trades if t.net_pnl_pct <= 0)
    win_rate = win_count / len(trades) * 100 if trades else 0

    total_invested = sum(t.entry_price * t.qty for t in trades)
    total_net_pnl = sum(t.net_pnl_amount for t in trades)
    gross_pnl_pct = sum(t.gross_pnl_pct for t in trades) / len(trades)
    net_pnl_pct = total_net_pnl / total_invested * 100 if total_invested > 0 else 0

    return DailySummary(
        date=date, total_trades=len(trades),
        tp_count=tp_count, sl_count=sl_count,
        trail_count=trail_count, time_exit_count=time_count,
        win_count=win_count, loss_count=loss_count,
        win_rate=round(win_rate, 1),
        gross_pnl_pct=round(gross_pnl_pct, 3),
        net_pnl_pct=round(net_pnl_pct, 3),
        net_pnl_amount=total_net_pnl,
        trades=trades,
    )


def main():
    parser = argparse.ArgumentParser(description='스캘핑 백테스트')
    parser.add_argument('--date', type=str, default=None, help='특정일 (YYYYMMDD)')
    parser.add_argument('--start', type=str, default=None, help='시작일')
    parser.add_argument('--end', type=str, default=None, help='종료일')
    parser.add_argument('--days', type=int, default=20, help='최근 N거래일 (기본: 20)')
    parser.add_argument('--equity', type=int, default=50_000_000, help='총자산 (기본: 5천만)')
    args = parser.parse_args()

    config = load_config('simulation')
    cache_dir = config.get('scanner', {}).get('cache_dir', './data/ohlcv_cache')

    print("=" * 90)
    print("스캘핑 전략 백테스트 — 일봉 캔들 기반 매수/매도 시뮬레이션")
    print("=" * 90)
    print(f"  총자산: {args.equity:,}원 | 수수료+세금: {TOTAL_COST_PCT:.2f}%")
    print(f"  매수가 산정: 고가→시가 38.2% 피보나치 되돌림")
    print(f"  매도 판정: TP/SL/트레일링/종가청산")

    # 1. 데이터 로드
    print(f"\n[1] OHLCV 캐시 로드...")
    all_ohlcv = load_ohlcv_cache(cache_dir)
    stock_names = load_stock_names(config)
    print(f"    → {len(all_ohlcv)}종목, 종목명 {len(stock_names)}개")

    # 2. 거래일 결정
    if args.date:
        dates = [args.date]
    elif args.start and args.end:
        dates = get_trading_dates(all_ohlcv, start=args.start, end=args.end)
    else:
        dates = get_trading_dates(all_ohlcv, days=args.days)

    print(f"    → 백테스트 기간: {dates[0]} ~ {dates[-1]} ({len(dates)}거래일)")

    # 3. 일별 백테스트
    print(f"\n[2] 백테스트 실행 중...")
    print("-" * 90)

    all_summaries: List[DailySummary] = []
    all_trades: List[TradeResult] = []

    for i, date in enumerate(dates):
        candidates = find_surge_stocks(all_ohlcv, date)
        summary = run_backtest_day(
            date, candidates, all_ohlcv, stock_names, config, args.equity)

        if summary:
            all_summaries.append(summary)
            all_trades.extend(summary.trades)

            if summary.total_trades > 0:
                status = f"{'✅' if summary.net_pnl_pct > 0 else '❌'}"
                print(f"  {date} | 상승{len(candidates):>3}종목 | "
                      f"매매{summary.total_trades:>2}건 "
                      f"(TP:{summary.tp_count} SL:{summary.sl_count} "
                      f"TR:{summary.trail_count} EX:{summary.time_exit_count}) | "
                      f"승률 {summary.win_rate:>5.1f}% | "
                      f"수익 {summary.net_pnl_pct:>+6.2f}% "
                      f"({summary.net_pnl_amount:>+10,}원) {status}")
            else:
                print(f"  {date} | 상승{len(candidates):>3}종목 | 매매 0건 (시그널 없음)")

    # 4. 종합 결과
    print("\n" + "=" * 90)
    print("종합 결과")
    print("=" * 90)

    trading_days = [s for s in all_summaries if s.total_trades > 0]
    if not trading_days:
        print("\n  매매 발생 없음")
        return

    total_trades = sum(s.total_trades for s in trading_days)
    total_tp = sum(s.tp_count for s in trading_days)
    total_sl = sum(s.sl_count for s in trading_days)
    total_trail = sum(s.trail_count for s in trading_days)
    total_time = sum(s.time_exit_count for s in trading_days)
    total_win = sum(s.win_count for s in trading_days)
    total_loss = sum(s.loss_count for s in trading_days)
    total_pnl = sum(s.net_pnl_amount for s in trading_days)
    win_rate = total_win / total_trades * 100 if total_trades > 0 else 0

    win_days = sum(1 for s in trading_days if s.net_pnl_pct > 0)
    loss_days = sum(1 for s in trading_days if s.net_pnl_pct <= 0)

    # 개별 거래 수익률 통계
    pnl_list = [t.net_pnl_pct for t in all_trades]
    win_pnls = [p for p in pnl_list if p > 0]
    loss_pnls = [p for p in pnl_list if p <= 0]

    print(f"""
  ┌─────────────────────────────────────────────────────┐
  │  백테스트 기간: {dates[0]} ~ {dates[-1]} ({len(dates)}거래일)   │
  │  총 매매일: {len(trading_days)}일 (무거래: {len(dates) - len(trading_days)}일)                    │
  ├─────────────────────────────────────────────────────┤
  │  총 매매 건수: {total_trades:>4}건                                │
  │    TP 익절:    {total_tp:>4}건                                │
  │    SL 손절:    {total_sl:>4}건                                │
  │    트레일링:   {total_trail:>4}건                                │
  │    종가청산:   {total_time:>4}건                                │
  ├─────────────────────────────────────────────────────┤
  │  승률: {win_rate:>5.1f}%  ({total_win}승 {total_loss}패)                        │
  │  승리일: {win_days}일 / 패배일: {loss_days}일                          │
  ├─────────────────────────────────────────────────────┤
  │  총 순수익: {total_pnl:>+12,}원                          │
  │  일 평균:   {total_pnl // len(trading_days):>+12,}원                          │
  └─────────────────────────────────────────────────────┘""")

    if win_pnls:
        print(f"\n  ▶ 수익 거래 ({len(win_pnls)}건)")
        print(f"    평균 수익률: +{np.mean(win_pnls):.2f}%")
        print(f"    최대 수익률: +{max(win_pnls):.2f}%")

    if loss_pnls:
        print(f"\n  ▶ 손실 거래 ({len(loss_pnls)}건)")
        print(f"    평균 손실률: {np.mean(loss_pnls):.2f}%")
        print(f"    최대 손실률: {min(loss_pnls):.2f}%")

    if win_pnls and loss_pnls:
        profit_factor = abs(sum(win_pnls)) / abs(sum(loss_pnls)) if loss_pnls else float('inf')
        print(f"\n  ▶ 위험 지표")
        print(f"    Profit Factor: {profit_factor:.2f}")
        print(f"    평균 R:R = {abs(np.mean(win_pnls)):.2f}% : {abs(np.mean(loss_pnls)):.2f}%")

    # 5. 일별 수익률 상세
    print(f"\n{'='*90}")
    print("일별 수익률 상세")
    print("=" * 90)
    print(f"{'날짜':>10} {'매매':>4} {'TP':>3} {'SL':>3} {'TR':>3} {'EX':>3} "
          f"{'승률':>6} {'수익률':>8} {'순수익':>12} {'누적':>12}")
    print("-" * 90)

    cumulative = 0
    for s in trading_days:
        cumulative += s.net_pnl_amount
        bar = '█' * max(1, int(abs(s.net_pnl_pct) * 5))
        sign = '+' if s.net_pnl_pct >= 0 else '-'
        print(f"  {s.date} {s.total_trades:>4} {s.tp_count:>3} {s.sl_count:>3} "
              f"{s.trail_count:>3} {s.time_exit_count:>3} "
              f"{s.win_rate:>5.1f}% {s.net_pnl_pct:>+7.2f}% "
              f"{s.net_pnl_amount:>+11,} {cumulative:>+11,}")

    # 6. 개별 매매 상세 (최근 5거래일)
    recent_days = trading_days[-5:]
    if recent_days:
        print(f"\n{'='*90}")
        print("최근 매매 상세")
        print("=" * 90)
        for s in recent_days:
            if not s.trades:
                continue
            print(f"\n  ── {s.date} ──")
            print(f"  {'종목':<14} {'등락률':>6} {'점수':>4} {'매수가':>9} {'매도가':>9} "
                  f"{'수량':>5} {'유형':>6} {'TP':>5} {'SL':>5} "
                  f"{'순수익률':>7} {'순수익':>10}")
            print("  " + "-" * 88)
            for t in s.trades:
                icon = {'TP': '✅', 'SL': '❌', 'TRAILING': '🔄', 'TIME_EXIT': '⏰'}.get(
                    t.exit_type, '?')
                print(f"  {t.name:<12} {t.change_pct:>+5.1f}% {t.score:>4.0f} "
                      f"{t.entry_price:>9,} {t.exit_price:>9,} "
                      f"{t.qty:>5} {icon}{t.exit_type:>4} "
                      f"+{t.tp_pct:.1f} {t.sl_pct:.1f} "
                      f"{t.net_pnl_pct:>+6.2f}% {t.net_pnl_amount:>+9,}")

    print(f"\n{'='*90}")
    print("백테스트 완료")
    print("=" * 90)


if __name__ == '__main__':
    main()
