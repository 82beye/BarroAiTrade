"""
2026-03-27(금) 시나리오 백테스트
현재 스캘핑 전략의 개선된 필터/파라미터를 3/27 캔들 데이터에 적용하여
실제 매매 결과와 비교 분석한다.

분석 구성:
  1부: 실제 매매 결과 요약 (trades.jsonl 기준)
  2부: 현재 전략 필터로 3/27 후보 재선별
  3부: 시나리오 시뮬레이션 (일봉 기반 최선/최악/현실 시나리오)
  4부: 비교 분석 및 인사이트
"""

import json
import os
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import numpy as np

# ─── 프로젝트 루트 설정 ───
ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

CACHE_DIR = ROOT / 'data' / 'ohlcv_cache'
TRADE_LOG = ROOT / 'logs' / 'trades.jsonl'

# ─── 현재 전략 설정값 (settings.yaml 기준) ───
SCALP_CONFIG = {
    'min_change_pct': 5.0,
    'max_change_pct': 25.0,
    'min_trade_value': 50_000_000_000,   # 500억
    'min_score': 55,
    'rr_min': 1.25,
    'daily_loss_limit_pct': -3.0,
    'initial_entry_ratio': 0.6,
    'max_per_stock_pct': 5.0,
    'max_positions': 5,
    'default_tp_pct': 2.5,
    'default_sl_pct': -1.5,
    # 구간별 TP/SL
    'zone_mid': {'tp_pct': 2.5, 'sl_pct': -1.5},    # +5~15%
    'zone_late': {'tp_pct': 1.5, 'sl_pct': -1.0},    # +15~25%
    # 왕복 수수료+세금
    'round_trip_fee_pct': 0.21,
    'slippage_pct': 0.3,
}

TOTAL_EQUITY = 50_000_000  # 5천만원 기준


@dataclass
class CandleData:
    code: str
    name: str
    prev_close: float
    open: float
    high: float
    low: float
    close: float
    volume: int
    change_pct: float      # 고가 기준
    close_change_pct: float
    open_gap_pct: float
    vol_ratio: float
    trade_value: float
    surge_type: str         # gap_up / intraday / mixed


@dataclass
class SimTrade:
    code: str
    name: str
    entry_price: float
    tp_price: float
    sl_price: float
    tp_pct: float
    sl_pct: float
    zone: str
    surge_type: str
    # 시나리오 결과
    best_pnl_pct: float = 0.0
    worst_pnl_pct: float = 0.0
    realistic_pnl_pct: float = 0.0
    realistic_exit: str = ''
    passed_filters: bool = True
    filter_reason: str = ''


def load_candle_data() -> Dict[str, CandleData]:
    """3/27 캔들 데이터 로드 + 필터 메트릭 계산"""
    stocks = {}

    for f in CACHE_DIR.glob('*.json'):
        if f.name == 'meta.json':
            continue
        code = f.stem
        try:
            with open(f) as fh:
                data = json.load(fh)['data']
            if len(data) < 22:
                continue
            last = data[-1]
            if last['date'] != '20260327':
                continue
            prev = data[-2]
            prev_close = prev['close']
            if prev_close <= 0:
                continue

            o, h, l, c, v = last['open'], last['high'], last['low'], last['close'], last['volume']
            change_pct = (h - prev_close) / prev_close * 100
            close_change = (c - prev_close) / prev_close * 100
            open_gap = (o - prev_close) / prev_close * 100

            # 20일 평균 거래량
            vols = [d['volume'] for d in data[-22:-1]]
            avg_vol = sum(vols) / len(vols) if vols else 1
            vol_ratio = v / avg_vol if avg_vol > 0 else 0

            trade_value = c * v

            # 급등 유형 분류
            if open_gap >= 15:
                surge_type = 'gap_up'
            elif open_gap < 10:
                surge_type = 'intraday'
            else:
                surge_type = 'mixed'

            stocks[code] = CandleData(
                code=code, name=code,
                prev_close=prev_close, open=o, high=h, low=l, close=c, volume=v,
                change_pct=round(change_pct, 1),
                close_change_pct=round(close_change, 1),
                open_gap_pct=round(open_gap, 1),
                vol_ratio=round(vol_ratio, 1),
                trade_value=trade_value,
                surge_type=surge_type,
            )
        except Exception:
            continue

    return stocks


def load_actual_trades() -> List[dict]:
    """3/27 실제 매매 로그"""
    trades = []
    with open(TRADE_LOG) as f:
        for line in f:
            t = json.loads(line)
            if '2026-03-27' in t.get('timestamp', ''):
                trades.append(t)
    return trades


def get_name_map(trades: List[dict]) -> Dict[str, str]:
    """종목코드 → 이름 매핑"""
    m = {}
    for t in trades:
        m[t['code']] = t['name']
    return m


def apply_filters(candle: CandleData) -> tuple:
    """현재 전략 필터 적용 → (통과여부, 사유)"""
    cfg = SCALP_CONFIG

    # 1. 진입구간 (+5~25%)
    if candle.change_pct < cfg['min_change_pct']:
        return False, f"상승률 미달: +{candle.change_pct:.1f}% < +{cfg['min_change_pct']}%"
    if candle.change_pct > cfg['max_change_pct']:
        return False, f"과열: +{candle.change_pct:.1f}% > +{cfg['max_change_pct']}%"

    # 2. 거래대금 (500억)
    if candle.trade_value < cfg['min_trade_value']:
        tv = candle.trade_value / 1e8
        return False, f"거래대금 부족: {tv:.0f}억 < 500억"

    # 3. 거래량 비율 (최소 2배)
    if candle.vol_ratio < 2.0:
        return False, f"거래량 부족: {candle.vol_ratio:.1f}배 < 2.0배"

    return True, ""


def get_zone_params(candle: CandleData) -> dict:
    """구간별 + 급등유형별 TP/SL 계산"""
    cfg = SCALP_CONFIG

    if candle.change_pct < 15:
        tp = cfg['zone_mid']['tp_pct']
        sl = cfg['zone_mid']['sl_pct']
        zone = 'mid(+5~15%)'
    else:
        tp = cfg['zone_late']['tp_pct']
        sl = cfg['zone_late']['sl_pct']
        zone = 'late(+15~25%)'

    # 급등 유형 보정
    if candle.surge_type == 'gap_up':
        tp = min(tp + 0.5, 5.0)
    elif candle.surge_type == 'intraday':
        tp = max(tp - 0.3, 1.0)
        sl = max(sl, -1.2)

    # 거래대금 규모 보정
    tv_억 = candle.trade_value / 1e8
    if tv_억 >= 1000:
        tp = min(tp + 1.0, 5.0)
    elif tv_억 < 500:
        tp = min(tp, 2.0)

    # R:R 체크
    rr = tp / abs(sl) if sl != 0 else 0
    if rr < cfg['rr_min']:
        return {'tp': tp, 'sl': sl, 'zone': zone, 'rr': rr, 'rr_pass': False}

    return {'tp': tp, 'sl': sl, 'zone': zone, 'rr': rr, 'rr_pass': True}


def simulate_trade(candle: CandleData, params: dict) -> SimTrade:
    """일봉 기반 시나리오 시뮬레이션

    진입가 가정:
      - 시가 + (고가-시가) × 30% 지점 (초기 상승 후 눌림목 진입)
      - 또는 시가 기준 (갭상승 시)

    시나리오:
      최선: 진입 → TP 도달 (고가까지 여유 있는지)
      최악: 진입 → SL 도달 (저가까지 관통하는지)
      현실: 캔들 구조 기반 확률적 판단
    """
    tp_pct = params['tp']
    sl_pct = params['sl']

    # 진입가 추정: 눌림목 진입 (시가 + 상승폭의 30%)
    if candle.surge_type == 'gap_up':
        # 갭상승: 시가 근처 진입
        entry = candle.open * 1.005
    else:
        # 장중급등: 시가→고가 사이 30% 지점
        entry = candle.open + (candle.high - candle.open) * 0.3

    tp_price = entry * (1 + tp_pct / 100)
    sl_price = entry * (1 + sl_pct / 100)

    trade = SimTrade(
        code=candle.code, name=candle.name,
        entry_price=round(entry),
        tp_price=round(tp_price),
        sl_price=round(sl_price),
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        zone=params['zone'],
        surge_type=candle.surge_type,
    )

    # 최선: 고가 도달 시 수익률
    best = (candle.high - entry) / entry * 100
    trade.best_pnl_pct = round(best, 2)

    # 최악: 저가 도달 시 손실률
    worst = (candle.low - entry) / entry * 100
    trade.worst_pnl_pct = round(worst, 2)

    fee = SCALP_CONFIG['round_trip_fee_pct'] + SCALP_CONFIG['slippage_pct']

    # 현실적 시나리오 판단
    if candle.high >= tp_price and candle.low <= sl_price:
        # TP와 SL 모두 도달 → 시가→저가→고가 or 시가→고가→저가
        # 장중급등형은 고점 먼저, 갭상승 후 하락형은 저점 먼저
        if candle.close > entry:
            # 종가 > 진입가 → TP 먼저 도달 확률 높음
            trade.realistic_pnl_pct = round(tp_pct - fee, 2)
            trade.realistic_exit = 'TP'
        else:
            # 종가 < 진입가 → SL 먼저 도달 확률 높음
            trade.realistic_pnl_pct = round(sl_pct - fee, 2)
            trade.realistic_exit = 'SL'
    elif candle.high >= tp_price:
        # TP만 도달
        trade.realistic_pnl_pct = round(tp_pct - fee, 2)
        trade.realistic_exit = 'TP'
    elif candle.low <= sl_price:
        # SL만 도달
        trade.realistic_pnl_pct = round(sl_pct - fee, 2)
        trade.realistic_exit = 'SL'
    else:
        # 둘 다 미도달 → 시간초과 종가 청산
        close_pnl = (candle.close - entry) / entry * 100
        trade.realistic_pnl_pct = round(close_pnl - fee, 2)
        trade.realistic_exit = 'TIMEOUT'

    return trade


def print_header(title: str):
    print()
    print("=" * 80)
    print(f"  {title}")
    print("=" * 80)


def main():
    print_header("3/27(금) 스캘핑 전략 시나리오 백테스트")
    print(f"  기준 자산: {TOTAL_EQUITY:,}원")
    print(f"  종목당 비중: {SCALP_CONFIG['max_per_stock_pct']}%")
    print(f"  초기 투입: {SCALP_CONFIG['initial_entry_ratio']*100:.0f}%")
    print(f"  최대 포지션: {SCALP_CONFIG['max_positions']}개")

    # ─── 1부: 실제 매매 결과 ───
    actual = load_actual_trades()
    name_map = get_name_map(actual)

    scalp_sells = [t for t in actual
                   if t['action'] == 'SELL' and t.get('strategy_type') == 'scalping']
    reg_sells = [t for t in actual
                 if t['action'] == 'SELL' and t.get('strategy_type') == 'regular']

    print_header("1부: 3/27 실제 매매 결과")

    print("\n[스캘핑 매매]")
    print(f"{'종목':>12} {'진입가':>8} {'청산가':>8} {'수익률':>7} {'순수익률':>8} {'청산사유':>14}")
    print("-" * 65)
    scalp_total = 0
    for t in scalp_sells:
        pnl = t.get('net_pnl_pct', t.get('pnl_pct', 0))
        scalp_total += pnl
        print(f"{t['name']:>12} {t['entry_price']:>8,} {t['price']:>8,} "
              f"{t['pnl_pct']:>+7.1f}% {pnl:>+8.2f}% {t['exit_type']:>14}")
    print("-" * 65)
    print(f"{'스캘핑 합계':>12} {'':>8} {'':>8} {'':>7} {scalp_total:>+8.2f}% ({len(scalp_sells)}건)")

    print("\n[일반 매매]")
    print(f"{'종목':>12} {'진입가':>8} {'청산가':>8} {'수익률':>7} {'순수익률':>8} {'청산사유':>14}")
    print("-" * 65)
    reg_total = 0
    for t in reg_sells:
        pnl = t.get('net_pnl_pct', t.get('pnl_pct', 0))
        reg_total += pnl
        print(f"{t['name']:>12} {t['entry_price']:>8,} {t['price']:>8,} "
              f"{t['pnl_pct']:>+7.1f}% {pnl:>+8.2f}% {t['exit_type']:>14}")
    print("-" * 65)
    print(f"{'일반 합계':>12} {'':>8} {'':>8} {'':>7} {reg_total:>+8.2f}% ({len(reg_sells)}건)")
    print(f"\n{'전체 합계':>12} {'':>8} {'':>8} {'':>7} {scalp_total+reg_total:>+8.2f}%")

    # ─── 2부: 현재 필터로 후보 재선별 ───
    print_header("2부: 현재 전략 필터로 3/27 후보 재선별")

    candles = load_candle_data()
    # +5% 이상 상승 종목만
    movers = {k: v for k, v in candles.items() if v.change_pct >= 5}
    movers_sorted = sorted(movers.values(), key=lambda x: x.trade_value, reverse=True)

    passed = []
    filtered = []
    for c in movers_sorted:
        ok, reason = apply_filters(c)
        c.name = name_map.get(c.code, c.code)
        if ok:
            params = get_zone_params(c)
            if params['rr_pass']:
                passed.append((c, params))
            else:
                filtered.append((c, f"R:R {params['rr']:.2f} < 1.25"))
        else:
            filtered.append((c, reason))

    print(f"\n3/27 장중 +5% 이상: {len(movers)}종목")
    print(f"필터 통과: {len(passed)}종목 / 차단: {len(filtered)}종목")

    print(f"\n[통과 종목]")
    surge_label = {'gap_up': '갭상승', 'intraday': '장중', 'mixed': '혼합'}
    print(f"{'종목':>12} {'고가%':>6} {'종가%':>6} {'갭%':>5} {'유형':>4} {'거래대금':>8} {'구간':>14} {'TP':>5} {'SL':>5} {'R:R':>5}")
    print("-" * 90)
    for c, p in passed:
        tv = c.trade_value / 1e8
        print(f"{c.name:>12} {c.change_pct:>+6.1f} {c.close_change_pct:>+6.1f} "
              f"{c.open_gap_pct:>+5.1f} {surge_label.get(c.surge_type,'?'):>4} "
              f"{tv:>7.0f}억 {p['zone']:>14} {p['tp']:>+5.1f} {p['sl']:>5.1f} {p['rr']:>5.2f}")

    print(f"\n[주요 차단 종목 (거래대금 상위)]")
    filtered_top = sorted(filtered, key=lambda x: x[0].trade_value, reverse=True)[:15]
    for c, reason in filtered_top:
        tv = c.trade_value / 1e8
        print(f"  {c.name:>12} +{c.change_pct:.1f}% | {tv:.0f}억 | 차단: {reason}")

    # ─── 3부: 시나리오 시뮬레이션 ───
    print_header("3부: 시나리오 시뮬레이션 (상위 10종목)")

    # 거래대금 기준 상위 종목으로 시뮬
    sim_targets = passed[:10]  # 거래대금 순 상위 10
    trades = []

    print(f"\n{'종목':>12} {'진입가':>8} {'TP가':>8} {'SL가':>8} "
          f"{'최선':>6} {'최악':>6} {'현실':>6} {'결과':>8} {'유형':>4}")
    print("-" * 90)

    for c, p in sim_targets:
        trade = simulate_trade(c, p)
        trade.name = c.name
        trades.append(trade)

        print(f"{trade.name:>12} {trade.entry_price:>8,} {trade.tp_price:>8,} "
              f"{trade.sl_price:>8,} {trade.best_pnl_pct:>+6.1f} "
              f"{trade.worst_pnl_pct:>+6.1f} {trade.realistic_pnl_pct:>+6.1f} "
              f"{trade.realistic_exit:>8} {surge_label.get(trade.surge_type,'?'):>4}")

    # 포지션 제한 적용 (최대 5개)
    max_pos = SCALP_CONFIG['max_positions']
    active_trades = trades[:max_pos]

    print(f"\n[포지션 제한 적용: 상위 {max_pos}개]")
    print("-" * 60)

    # 시나리오별 합산
    best_total = sum(t.best_pnl_pct for t in active_trades)
    worst_total = sum(t.worst_pnl_pct for t in active_trades)
    realistic_total = sum(t.realistic_pnl_pct for t in active_trades)

    per_stock_amount = TOTAL_EQUITY * SCALP_CONFIG['max_per_stock_pct'] / 100
    entry_amount = per_stock_amount * SCALP_CONFIG['initial_entry_ratio']

    for t in active_trades:
        est_pnl = entry_amount * t.realistic_pnl_pct / 100
        print(f"  {t.name:>12} | 투입 {entry_amount/10000:,.0f}만 | "
              f"현실 {t.realistic_pnl_pct:>+5.1f}% ({t.realistic_exit}) | "
              f"추정 손익 {est_pnl:>+,.0f}원")

    total_invested = entry_amount * len(active_trades)
    realistic_pnl_won = sum(entry_amount * t.realistic_pnl_pct / 100 for t in active_trades)
    best_pnl_won = sum(entry_amount * t.best_pnl_pct / 100 for t in active_trades)
    worst_pnl_won = sum(entry_amount * t.worst_pnl_pct / 100 for t in active_trades)

    # 일일 손실한도 체크
    cum_pnl = 0
    stopped_at = None
    for i, t in enumerate(active_trades):
        cum_pnl += t.realistic_pnl_pct
        if cum_pnl <= SCALP_CONFIG['daily_loss_limit_pct']:
            stopped_at = i + 1
            break

    # ─── 4부: 비교 분석 ───
    print_header("4부: 비교 분석")

    print("\n[실제 vs 시뮬레이션 비교]")
    print(f"{'지표':>20} {'3/27 실제':>15} {'시뮬(현실)':>15} {'시뮬(최선)':>15}")
    print("-" * 70)

    actual_scalp_count = len(scalp_sells)
    sim_count = len(active_trades)

    print(f"{'스캘핑 매매 수':>20} {actual_scalp_count:>14}건 {sim_count:>14}건 {sim_count:>14}건")
    print(f"{'스캘핑 누적 수익률':>20} {scalp_total:>+14.2f}% {realistic_total:>+14.2f}% {best_total:>+14.2f}%")
    print(f"{'추정 손익(원)':>20} {'':>15} {realistic_pnl_won:>+14,.0f} {best_pnl_won:>+14,.0f}")

    avg_actual = scalp_total / actual_scalp_count if actual_scalp_count > 0 else 0
    avg_sim = realistic_total / sim_count if sim_count > 0 else 0
    print(f"{'건당 평균 수익률':>20} {avg_actual:>+14.2f}% {avg_sim:>+14.2f}% {'':>15}")

    # 승률 계산
    actual_wins = sum(1 for t in scalp_sells if t.get('pnl_pct', 0) > 0)
    sim_wins = sum(1 for t in active_trades if t.realistic_pnl_pct > 0)
    actual_wr = actual_wins / actual_scalp_count * 100 if actual_scalp_count > 0 else 0
    sim_wr = sim_wins / sim_count * 100 if sim_count > 0 else 0
    print(f"{'승률':>20} {actual_wr:>13.0f}% {sim_wr:>13.0f}% {'':>15}")

    if stopped_at:
        print(f"\n⚠ 일일 손실한도 발동: {stopped_at}번째 매매 후 누적 {cum_pnl:+.1f}% ≤ {SCALP_CONFIG['daily_loss_limit_pct']}%")
        print(f"  → 이후 매매 중단으로 추가 손실 방지")

    # ─── 인사이트 ───
    print_header("5부: 핵심 인사이트")

    tp_count = sum(1 for t in active_trades if t.realistic_exit == 'TP')
    sl_count = sum(1 for t in active_trades if t.realistic_exit == 'SL')
    to_count = sum(1 for t in active_trades if t.realistic_exit == 'TIMEOUT')

    insights = []

    # 거래대금 필터 효과
    actual_codes = set(t['code'] for t in scalp_sells)
    sim_codes = set(t.code for t in active_trades)
    new_codes = sim_codes - actual_codes
    removed_codes = actual_codes - sim_codes

    insights.append(
        f"1. 거래대금 500억 필터: 실제 {len(actual_codes)}종목 → 시뮬 {len(sim_codes)}종목 "
        f"(추가 {len(new_codes)}, 제거 {len(removed_codes)})")

    insights.append(
        f"2. 매매 빈도: 실제 {actual_scalp_count}건 → 시뮬 {sim_count}건 "
        f"(과매매 {actual_scalp_count - sim_count}건 감소)")

    insights.append(
        f"3. 청산 분포: TP {tp_count}건 / SL {sl_count}건 / TIMEOUT {to_count}건")

    # 실제 대비 개선 여부
    if realistic_total > scalp_total:
        diff = realistic_total - scalp_total
        insights.append(
            f"4. 수익률 개선: 실제 {scalp_total:+.2f}% → 시뮬 {realistic_total:+.2f}% "
            f"(+{diff:.2f}%p 개선)")
    else:
        diff = scalp_total - realistic_total
        insights.append(
            f"4. 수익률 비교: 실제 {scalp_total:+.2f}% → 시뮬 {realistic_total:+.2f}% "
            f"(-{diff:.2f}%p)")

    # 급등유형 분석
    gap_trades = [t for t in active_trades if t.surge_type == 'gap_up']
    intra_trades = [t for t in active_trades if t.surge_type == 'intraday']
    if gap_trades:
        gap_avg = sum(t.realistic_pnl_pct for t in gap_trades) / len(gap_trades)
        insights.append(f"5. 갭상승형 평균: {gap_avg:+.2f}% ({len(gap_trades)}건)")
    if intra_trades:
        intra_avg = sum(t.realistic_pnl_pct for t in intra_trades) / len(intra_trades)
        insights.append(f"   장중급등형 평균: {intra_avg:+.2f}% ({len(intra_trades)}건)")

    for ins in insights:
        print(f"  {ins}")

    # 최종 요약
    print_header("최종 요약")
    print(f"""
  3/27 실제 스캘핑: {scalp_total:+.2f}% ({actual_scalp_count}건, 승률 {actual_wr:.0f}%)
  시뮬 현실 시나리오: {realistic_total:+.2f}% ({sim_count}건, 승률 {sim_wr:.0f}%)
  시뮬 최선 시나리오: {best_total:+.2f}%
  시뮬 최악 시나리오: {worst_total:+.2f}%

  투입 자본: {total_invested:,.0f}원 (종목당 {entry_amount:,.0f}원 × {len(active_trades)}종목)
  추정 손익 (현실): {realistic_pnl_won:+,.0f}원
  추정 손익 (최선): {best_pnl_won:+,.0f}원
  추정 손익 (최악): {worst_pnl_won:+,.0f}원
""")


if __name__ == '__main__':
    main()
