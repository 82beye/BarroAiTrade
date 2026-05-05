"""
매수 종목 1분봉 경로 분석 & 전략 튜닝

백테스트에서 매수된 종목들의 진입 이후 1분봉 데이터를 추적하여:
  1. MFE (Maximum Favorable Excursion) — 진입 후 최대 수익
  2. MAE (Maximum Adverse Excursion) — 진입 후 최대 손실
  3. 시간별 가격 경로 — 분단위 수익률 추적
  4. 최적 TP/SL/트레일링 파라미터 도출
  5. 전략 튜닝 권고안 출력

Usage:
    python3 scripts/trade_path_analysis.py
"""

import sys
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime, time, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / "config" / ".env")

from execution.kiwoom_api import KiwoomRestAPI
from strategy.scalping_team.coordinator import ScalpingCoordinator
from strategy.scalping_team.base_agent import StockSnapshot
from main import load_config

logging.basicConfig(level=logging.WARNING, format='%(message)s')

BUY_FEE_PCT = 0.015
SELL_FEE_PCT = 0.015
SELL_TAX_PCT = 0.18
TOTAL_COST_PCT = BUY_FEE_PCT + SELL_FEE_PCT + SELL_TAX_PCT


@dataclass
class CandlePath:
    """진입 후 분봉별 경로"""
    minutes_after: int
    price: int
    high: int
    low: int
    pct_from_entry: float  # 종가 기준 수익률
    high_pct: float        # 고가 기준 수익률
    low_pct: float         # 저가 기준 수익률
    cumul_mfe: float       # 누적 MFE
    cumul_mae: float       # 누적 MAE


@dataclass
class TradeAnalysis:
    """개별 매매 경로 분석"""
    date: str
    code: str
    name: str
    entry_time: str
    entry_price: int
    score: float
    tp_pct: float
    sl_pct: float

    # 경로 데이터
    path: List[CandlePath] = field(default_factory=list)

    # MFE/MAE
    mfe_pct: float = 0.0         # 최대 수익 (고가 기준)
    mae_pct: float = 0.0         # 최대 손실 (저가 기준)
    mfe_time_min: int = 0        # MFE 도달 시간 (분)
    mae_time_min: int = 0        # MAE 도달 시간 (분)

    # 실제 청산 결과
    actual_exit_type: str = ""
    actual_exit_pct: float = 0.0
    actual_exit_min: int = 0

    # 최적 파라미터 (사후 분석)
    optimal_tp: float = 0.0
    optimal_sl: float = 0.0

    # 5분/10분/15분 후 수익률
    pct_5m: float = 0.0
    pct_10m: float = 0.0
    pct_15m: float = 0.0
    pct_20m: float = 0.0


def load_stock_names(config: dict) -> dict:
    cache_path = Path(config.get('scanner', {}).get(
        'cache_dir', './data/ohlcv_cache')).parent / 'stock_names.json'
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)
    return {}


def load_ohlcv_cache(cache_dir: str) -> dict:
    cache_path = Path(cache_dir)
    data = {}
    for f in cache_path.glob("*.json"):
        code = f.stem
        try:
            with open(f, 'r') as fh:
                raw = json.load(fh)
            records = raw.get('data', raw) if isinstance(raw, dict) else raw
            df = pd.DataFrame(records) if isinstance(records, (list, dict)) else None
            if df is not None and 'date' in df.columns:
                df['date'] = df['date'].astype(str)
                df = df.sort_values('date').reset_index(drop=True)
                data[code] = df
        except Exception:
            continue
    return data


def find_surge_stocks(all_ohlcv: dict, target_date: str) -> list:
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

        prev = df.iloc[idx - 1]
        cur = row.iloc[0]
        prev_close = int(prev['close'])
        if prev_close <= 0:
            continue

        change_pct = (int(cur['close']) - prev_close) / prev_close * 100
        if change_pct < 5.0 or change_pct > 30.0:
            continue

        trade_value = int(cur['close']) * int(cur['volume'])
        if trade_value < 5_000_000_000:
            continue

        start_idx = max(0, idx - 20)
        avg_vol = df.iloc[start_idx:idx]['volume'].mean()
        vol_ratio = int(cur['volume']) / avg_vol if avg_vol > 0 else 0

        candidates.append({
            'code': code,
            'name': code,
            'prev_close': prev_close,
            'open': int(cur['open']),
            'high': int(cur['high']),
            'low': int(cur['low']),
            'close': int(cur['close']),
            'volume': int(cur['volume']),
            'change_pct': round(change_pct, 2),
            'volume_ratio': round(vol_ratio, 2),
            'trade_value': trade_value,
        })

    candidates.sort(key=lambda x: x['trade_value'], reverse=True)
    return candidates


def parse_candle_time(time_str: str) -> Optional[datetime]:
    try:
        return datetime.strptime(time_str, '%Y%m%d%H%M%S')
    except (ValueError, TypeError):
        return None


async def collect_minute_candles(api: KiwoomRestAPI, codes: List[str],
                                  target_date: str) -> Dict[str, List[dict]]:
    all_candles = {}
    total = len(codes)

    for i, code in enumerate(codes):
        try:
            candles = await api.get_intraday_chart(code, tick_scope=1, count=900)
            day_candles = [c for c in candles if c['time'].startswith(target_date)]
            if day_candles:
                day_candles.sort(key=lambda x: x['time'])
                all_candles[code] = day_candles
        except Exception as e:
            pass

        if (i + 1) % 10 == 0 or i == total - 1:
            print(f"    수집: {i+1}/{total} ({len(all_candles)}종목)")

        if api.is_quota_exhausted('ka10080'):
            print(f"    ⚠ API 한도 — 중단 ({len(all_candles)}종목)")
            break

    return all_candles


def find_entry_signals(
    candles_by_code: Dict[str, List[dict]],
    candidates: list,
    stock_names: dict,
    all_ohlcv: dict,
    config: dict,
    target_date: str,
) -> List[dict]:
    """매수 시그널 발생 종목과 진입 시점 추출 (백테스트와 동일 로직)"""

    scalp_cfg = config.get('strategy', {}).get('scalping', {})
    min_score = scalp_cfg.get('min_score', 50)

    cand_map = {c['code']: c for c in candidates}

    timeline = []
    for code, candles in candles_by_code.items():
        for c in candles:
            dt = parse_candle_time(c['time'])
            if dt and time(9, 1) <= dt.time() <= time(15, 30):
                timeline.append((dt, code, c))
    timeline.sort(key=lambda x: x[0])

    stock_state = {}
    cumul_volume = {}
    entered_codes = set()
    last_analysis_time = {}
    entries = []

    for dt, code, candle in timeline:
        name = stock_names.get(code, code)
        cand = cand_map.get(code)
        if not cand:
            continue

        prev_close = cand['prev_close']
        price = candle['price']
        cumul_volume[code] = cumul_volume.get(code, 0) + candle['volume']

        if code not in stock_state:
            stock_state[code] = {
                'high': candle['high'], 'low': candle['low'],
                'open': candle['open'], 'price': price,
                'volume': cumul_volume[code],
            }
        else:
            s = stock_state[code]
            s['high'] = max(s['high'], candle['high'])
            s['low'] = min(s['low'], candle['low'])
            s['price'] = price
            s['volume'] = cumul_volume[code]

        # 매수 조건 체크
        if dt.time() < time(9, 10) or dt.time() >= time(14, 0):
            continue
        if code in entered_codes:
            continue
        if time(11, 0) <= dt.time() < time(11, 30):
            continue

        if prev_close <= 0:
            continue
        cur_change = (price - prev_close) / prev_close * 100
        if cur_change < 5.0 or cur_change > 30.0:
            continue

        cur_trade_value = price * cumul_volume.get(code, 0)
        min_tv = 5_000_000_000
        if dt.time() >= time(9, 30):
            min_tv = 10_000_000_000
        if dt.time() >= time(11, 30):
            min_tv = 20_000_000_000
        if cur_trade_value < min_tv:
            continue

        last_t = last_analysis_time.get(code)
        if last_t and (dt - last_t).total_seconds() < 300:
            continue

        s = stock_state[code]
        snap = StockSnapshot(
            code=code, name=name,
            price=price, open=s['open'],
            high=s['high'], low=s['low'],
            prev_close=prev_close,
            volume=cumul_volume.get(code, 0),
            change_pct=round(cur_change, 2),
            trade_value=cur_trade_value,
            volume_ratio=cand['volume_ratio'],
            category='급등주' if cur_change >= 15 else '강세주',
            score=cur_change * 3,
        )

        coordinator = ScalpingCoordinator(config)
        ohlcv_cache = {code: all_ohlcv[code]} if code in all_ohlcv else {}

        with patch('strategy.scalping_team.coordinator.datetime') as mock_dt:
            mock_dt.now.return_value = dt
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.strptime = datetime.strptime
            results = coordinator.analyze([snap], cache_data=ohlcv_cache)

        last_analysis_time[code] = dt

        if not results:
            continue

        analysis = results[0]
        if analysis.timing != '즉시' or analysis.total_score < min_score:
            continue

        entries.append({
            'code': code,
            'name': name,
            'entry_time': candle['time'],
            'entry_price': price,
            'entry_dt': dt,
            'score': analysis.total_score,
            'tp_pct': analysis.scalp_tp_pct,
            'sl_pct': analysis.scalp_sl_pct,
            'hold_minutes': analysis.hold_minutes,
            'change_pct': cur_change,
        })
        entered_codes.add(code)

    return entries


def analyze_trade_path(
    entry: dict,
    candles: List[dict],
    trailing_cfg: dict,
) -> TradeAnalysis:
    """진입 이후 1분봉 경로를 분석"""

    entry_price = entry['entry_price']
    entry_time = entry['entry_time']
    entry_dt = entry['entry_dt']

    ta = TradeAnalysis(
        date=entry_time[:8],
        code=entry['code'],
        name=entry['name'],
        entry_time=entry_time,
        entry_price=entry_price,
        score=entry['score'],
        tp_pct=entry['tp_pct'],
        sl_pct=entry['sl_pct'],
    )

    # 진입 이후 캔들만 추출
    post_entry = []
    for c in candles:
        cdt = parse_candle_time(c['time'])
        if cdt and cdt > entry_dt:
            post_entry.append((cdt, c))

    if not post_entry:
        return ta

    cumul_mfe = 0.0
    cumul_mae = 0.0
    mfe_time = 0
    mae_time = 0

    # 트레일링 시뮬레이션 상태
    activation_pct = trailing_cfg.get('activation_pct', 1.5)
    trail_pct = trailing_cfg.get('trail_pct', -1.2)
    be_pct = trailing_cfg.get('breakeven_pct', 0.3)
    high_watermark = entry_price
    trailing_active = False
    exited = False

    for cdt, candle in post_entry:
        minutes = int((cdt - entry_dt).total_seconds() / 60)
        if minutes <= 0:
            continue

        high = candle['high']
        low = candle['low']
        close = candle['price']

        pct = (close - entry_price) / entry_price * 100
        high_pct = (high - entry_price) / entry_price * 100
        low_pct = (low - entry_price) / entry_price * 100

        # MFE/MAE 갱신
        if high_pct > cumul_mfe:
            cumul_mfe = high_pct
            mfe_time = minutes
        if low_pct < cumul_mae:
            cumul_mae = low_pct
            mae_time = minutes

        # 고점 갱신
        if high > high_watermark:
            high_watermark = high

        # 트레일링 활성화 체크
        hwm_pct = (high_watermark - entry_price) / entry_price * 100
        if not trailing_active and hwm_pct >= activation_pct:
            trailing_active = True

        ta.path.append(CandlePath(
            minutes_after=minutes,
            price=close,
            high=high,
            low=low,
            pct_from_entry=round(pct, 3),
            high_pct=round(high_pct, 3),
            low_pct=round(low_pct, 3),
            cumul_mfe=round(cumul_mfe, 3),
            cumul_mae=round(cumul_mae, 3),
        ))

        # 실제 청산 시뮬레이션 (현재 파라미터 기준)
        if not exited:
            tp_price = int(entry_price * (1 + ta.tp_pct / 100))
            sl_price_raw = int(entry_price * (1 + ta.sl_pct / 100))

            if trailing_active:
                effective_sl = max(sl_price_raw, int(entry_price * (1 + be_pct / 100)))
                trail_price = int(high_watermark * (1 + trail_pct / 100))
                effective_sl = max(effective_sl, trail_price)
                if low <= effective_sl:
                    ta.actual_exit_type = 'TRAILING'
                    ta.actual_exit_pct = round((min(effective_sl, close) - entry_price) / entry_price * 100 - TOTAL_COST_PCT, 3)
                    ta.actual_exit_min = minutes
                    exited = True
            else:
                if low <= sl_price_raw:
                    ta.actual_exit_type = 'SL'
                    ta.actual_exit_pct = round(ta.sl_pct - TOTAL_COST_PCT, 3)
                    ta.actual_exit_min = minutes
                    exited = True

            if not exited and high >= tp_price:
                ta.actual_exit_type = 'TP'
                ta.actual_exit_pct = round(ta.tp_pct - TOTAL_COST_PCT, 3)
                ta.actual_exit_min = minutes
                exited = True

            if not exited and minutes >= entry.get('hold_minutes', 15):
                ta.actual_exit_type = 'TIME_EXIT'
                ta.actual_exit_pct = round(pct - TOTAL_COST_PCT, 3)
                ta.actual_exit_min = minutes
                exited = True

            if not exited and cdt.time() >= time(14, 50):
                ta.actual_exit_type = 'FORCE_EXIT'
                ta.actual_exit_pct = round(pct - TOTAL_COST_PCT, 3)
                ta.actual_exit_min = minutes
                exited = True

        # 시간 스냅샷
        if minutes == 5:
            ta.pct_5m = round(pct, 3)
        elif minutes == 10:
            ta.pct_10m = round(pct, 3)
        elif minutes == 15:
            ta.pct_15m = round(pct, 3)
        elif minutes == 20:
            ta.pct_20m = round(pct, 3)

    ta.mfe_pct = round(cumul_mfe, 3)
    ta.mae_pct = round(cumul_mae, 3)
    ta.mfe_time_min = mfe_time
    ta.mae_time_min = mae_time

    if not exited and ta.path:
        ta.actual_exit_type = 'MARKET_CLOSE'
        ta.actual_exit_pct = round(ta.path[-1].pct_from_entry - TOTAL_COST_PCT, 3)
        ta.actual_exit_min = ta.path[-1].minutes_after

    return ta


def simulate_with_params(ta: TradeAnalysis, tp: float, sl: float,
                          activation: float, trail: float, be: float,
                          hold_min: int) -> dict:
    """특정 파라미터로 매매 결과 시뮬레이션"""
    entry_price = ta.entry_price
    high_watermark = entry_price
    trailing_active = False

    for cp in ta.path:
        high = cp.high
        low = cp.low
        close = cp.price

        if high > high_watermark:
            high_watermark = high

        hwm_pct = (high_watermark - entry_price) / entry_price * 100
        if not trailing_active and hwm_pct >= activation:
            trailing_active = True

        tp_price = int(entry_price * (1 + tp / 100))
        sl_price = int(entry_price * (1 + sl / 100))

        if trailing_active:
            effective_sl = max(sl_price, int(entry_price * (1 + be / 100)))
            trail_price = int(high_watermark * (1 + trail / 100))
            effective_sl = max(effective_sl, trail_price)
            if low <= effective_sl:
                exit_price = min(effective_sl, close)
                pnl = (exit_price - entry_price) / entry_price * 100 - TOTAL_COST_PCT
                return {'type': 'TRAILING', 'pnl': round(pnl, 3), 'min': cp.minutes_after}
        else:
            if low <= sl_price:
                pnl = sl - TOTAL_COST_PCT
                return {'type': 'SL', 'pnl': round(pnl, 3), 'min': cp.minutes_after}

        if high >= tp_price:
            pnl = tp - TOTAL_COST_PCT
            return {'type': 'TP', 'pnl': round(pnl, 3), 'min': cp.minutes_after}

        if cp.minutes_after >= hold_min:
            pnl = cp.pct_from_entry - TOTAL_COST_PCT
            return {'type': 'TIME', 'pnl': round(pnl, 3), 'min': cp.minutes_after}

    if ta.path:
        pnl = ta.path[-1].pct_from_entry - TOTAL_COST_PCT
        return {'type': 'CLOSE', 'pnl': round(pnl, 3), 'min': ta.path[-1].minutes_after}

    return {'type': 'NONE', 'pnl': 0, 'min': 0}


def grid_search_params(analyses: List[TradeAnalysis]) -> dict:
    """TP/SL/트레일링 파라미터 그리드 서치"""

    tp_range = np.arange(1.0, 4.1, 0.5)
    sl_range = np.arange(-2.0, -0.4, 0.2)
    activation_range = [0.8, 1.0, 1.2, 1.5, 2.0]
    trail_range = [-0.8, -1.0, -1.2, -1.5]
    be_range = [0.2, 0.3, 0.5]
    hold_range = [10, 15, 20, 30]

    best_result = None
    best_score = -999

    # Phase 1: TP/SL 서치 (트레일링/홀딩 고정)
    print("\n  [Phase 1] TP/SL 그리드 서치...")
    best_tp_sl = None
    for tp in tp_range:
        for sl in sl_range:
            if tp / abs(sl) < 1.0:  # 최소 R:R 1:1
                continue
            results = []
            for ta in analyses:
                r = simulate_with_params(ta, tp, sl, 1.5, -1.2, 0.3, 15)
                results.append(r)

            pnls = [r['pnl'] for r in results]
            wins = sum(1 for p in pnls if p > 0)
            total_pnl = sum(pnls)
            win_rate = wins / len(pnls) * 100

            # 스코어: 총수익 * 승률 가중
            score = total_pnl * (1 + win_rate / 100)
            if score > best_score:
                best_score = score
                best_tp_sl = {'tp': tp, 'sl': sl, 'total_pnl': total_pnl,
                              'win_rate': win_rate, 'results': results}

    if not best_tp_sl:
        best_tp_sl = {'tp': 2.0, 'sl': -1.2, 'total_pnl': 0, 'win_rate': 0, 'results': []}

    # Phase 2: 트레일링 파라미터 서치
    print("  [Phase 2] 트레일링 파라미터 서치...")
    best_trail = None
    best_trail_score = -999
    for act in activation_range:
        for trail in trail_range:
            for be in be_range:
                results = []
                for ta in analyses:
                    r = simulate_with_params(ta, best_tp_sl['tp'], best_tp_sl['sl'],
                                              act, trail, be, 15)
                    results.append(r)

                pnls = [r['pnl'] for r in results]
                wins = sum(1 for p in pnls if p > 0)
                total_pnl = sum(pnls)
                win_rate = wins / len(pnls) * 100
                score = total_pnl * (1 + win_rate / 100)

                if score > best_trail_score:
                    best_trail_score = score
                    best_trail = {'activation': act, 'trail': trail, 'be': be,
                                  'total_pnl': total_pnl, 'win_rate': win_rate,
                                  'results': results}

    if not best_trail:
        best_trail = {'activation': 1.5, 'trail': -1.2, 'be': 0.3,
                      'total_pnl': 0, 'win_rate': 0, 'results': []}

    # Phase 3: 홀딩 시간 서치
    print("  [Phase 3] 홀딩 시간 서치...")
    best_hold = None
    best_hold_score = -999
    for hold in hold_range:
        results = []
        for ta in analyses:
            r = simulate_with_params(ta, best_tp_sl['tp'], best_tp_sl['sl'],
                                      best_trail['activation'], best_trail['trail'],
                                      best_trail['be'], hold)
            results.append(r)

        pnls = [r['pnl'] for r in results]
        wins = sum(1 for p in pnls if p > 0)
        total_pnl = sum(pnls)
        win_rate = wins / len(pnls) * 100
        score = total_pnl * (1 + win_rate / 100)

        if score > best_hold_score:
            best_hold_score = score
            best_hold = hold
            best_result = {
                'tp': best_tp_sl['tp'],
                'sl': best_tp_sl['sl'],
                'activation': best_trail['activation'],
                'trail': best_trail['trail'],
                'be': best_trail['be'],
                'hold': hold,
                'total_pnl': total_pnl,
                'win_rate': win_rate,
                'results': results,
            }

    return best_result


def print_analysis_report(analyses: List[TradeAnalysis], best_params: dict):
    """상세 분석 리포트 출력"""

    print("\n" + "=" * 100)
    print("매수 종목 1분봉 경로 분석 & 전략 튜닝 리포트")
    print("=" * 100)

    # ── 1. 개별 매매 경로 분석 ──
    print(f"\n{'─' * 100}")
    print(f"  [1] 개별 매매 MFE/MAE 분석 ({len(analyses)}건)")
    print(f"{'─' * 100}")
    print(f"  {'날짜':>8}  {'시간':>4}  {'종목':<12}  {'진입가':>8}  {'점수':>4}  "
          f"{'MFE':>6}  {'MFE분':>4}  {'MAE':>6}  {'MAE분':>4}  "
          f"{'5분':>6}  {'10분':>6}  {'15분':>6}  {'20분':>6}  "
          f"{'청산':>8}  {'결과':>7}")
    print(f"  {'─' * 96}")

    for ta in analyses:
        entry_t = ta.entry_time[8:12]
        icon = {'TP': '✅', 'SL': '❌', 'TRAILING': '🔄',
                'TIME_EXIT': '⏰', 'FORCE_EXIT': '🔚',
                'MARKET_CLOSE': '🔚'}.get(ta.actual_exit_type, '?')

        print(f"  {ta.date}  {entry_t}  {ta.name:<10}  "
              f"{ta.entry_price:>8,}  {ta.score:>4.0f}  "
              f"{ta.mfe_pct:>+5.2f}%  {ta.mfe_time_min:>3}분  "
              f"{ta.mae_pct:>+5.2f}%  {ta.mae_time_min:>3}분  "
              f"{ta.pct_5m:>+5.2f}%  {ta.pct_10m:>+5.2f}%  "
              f"{ta.pct_15m:>+5.2f}%  {ta.pct_20m:>+5.2f}%  "
              f"{icon}{ta.actual_exit_type:<7}  {ta.actual_exit_pct:>+5.2f}%")

    # ── 1.5 개별 매매 분봉 가격 경로 ──
    print(f"\n{'─' * 100}")
    print(f"  [1.5] 개별 매매 진입 후 분봉별 가격 경로 (최대 30분)")
    print(f"{'─' * 100}")

    for ta in analyses:
        entry_t = ta.entry_time[8:12]
        print(f"\n  ▶ {ta.date} {entry_t} {ta.name} (진입가 {ta.entry_price:,}원, "
              f"TP +{ta.tp_pct:.1f}% SL {ta.sl_pct:.1f}%)")

        # 분봉별 수익률 바 차트
        path_data = ta.path[:30]  # 최대 30분
        if not path_data:
            print("    데이터 없음")
            continue

        for cp in path_data:
            bar_len = int(abs(cp.pct_from_entry) * 10)
            if cp.pct_from_entry >= 0:
                bar = '█' * min(bar_len, 40)
                print(f"    {cp.minutes_after:>3}분  {cp.pct_from_entry:>+6.2f}%  "
                      f"(H:{cp.high_pct:>+5.2f}% L:{cp.low_pct:>+5.2f}%) "
                      f"│{'':>1}{bar}")
            else:
                bar = '█' * min(bar_len, 40)
                print(f"    {cp.minutes_after:>3}분  {cp.pct_from_entry:>+6.2f}%  "
                      f"(H:{cp.high_pct:>+5.2f}% L:{cp.low_pct:>+5.2f}%) "
                      f"{bar}│")

        # TP/SL 라인 표시
        print(f"    {'':>6} TP +{ta.tp_pct:.1f}% ── MFE +{ta.mfe_pct:.2f}% ({ta.mfe_time_min}분) "
              f"── MAE {ta.mae_pct:.2f}% ({ta.mae_time_min}분) "
              f"── 청산: {ta.actual_exit_type} {ta.actual_exit_pct:+.2f}% ({ta.actual_exit_min}분)")

    # ── 2. MFE/MAE 통계 ──
    print(f"\n{'─' * 100}")
    print(f"  [2] MFE/MAE 통계 분석")
    print(f"{'─' * 100}")

    mfes = [ta.mfe_pct for ta in analyses]
    maes = [ta.mae_pct for ta in analyses]
    mfe_times = [ta.mfe_time_min for ta in analyses]
    mae_times = [ta.mae_time_min for ta in analyses]

    print(f"  MFE (최대 수익 도달):")
    print(f"    평균: +{np.mean(mfes):.2f}%  |  중앙값: +{np.median(mfes):.2f}%  |  "
          f"최소: +{min(mfes):.2f}%  |  최대: +{max(mfes):.2f}%")
    print(f"    MFE 도달 시간: 평균 {np.mean(mfe_times):.0f}분  |  "
          f"중앙값 {np.median(mfe_times):.0f}분")

    print(f"  MAE (최대 손실 도달):")
    print(f"    평균: {np.mean(maes):.2f}%  |  중앙값: {np.median(maes):.2f}%  |  "
          f"최소: {min(maes):.2f}%  |  최대: {max(maes):.2f}%")
    print(f"    MAE 도달 시간: 평균 {np.mean(mae_times):.0f}분  |  "
          f"중앙값 {np.median(mae_times):.0f}분")

    # MFE 분포
    print(f"\n  MFE 분포:")
    for threshold in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
        count = sum(1 for m in mfes if m >= threshold)
        pct = count / len(mfes) * 100
        bar = '█' * int(pct / 2)
        print(f"    +{threshold:.1f}% 이상 도달: {count:>2}/{len(mfes)} ({pct:>5.1f}%) {bar}")

    # MAE 분포
    print(f"\n  MAE 분포:")
    for threshold in [-0.5, -1.0, -1.5, -2.0, -2.5]:
        count = sum(1 for m in maes if m <= threshold)
        pct = count / len(maes) * 100
        bar = '█' * int(pct / 2)
        print(f"    {threshold:.1f}% 이하 도달: {count:>2}/{len(maes)} ({pct:>5.1f}%) {bar}")

    # MFE가 MAE보다 먼저 도달한 비율
    mfe_first = sum(1 for ta in analyses if ta.mfe_time_min <= ta.mae_time_min and ta.mfe_pct > 0)
    print(f"\n  MFE가 MAE보다 먼저 도달: {mfe_first}/{len(analyses)} "
          f"({mfe_first/len(analyses)*100:.0f}%)")

    # ── 3. 시간별 수익률 패턴 ──
    print(f"\n{'─' * 100}")
    print(f"  [3] 진입 후 시간별 평균 수익률")
    print(f"{'─' * 100}")

    for min_mark in [1, 2, 3, 5, 7, 10, 15, 20]:
        pcts = []
        for ta in analyses:
            matching = [cp for cp in ta.path if cp.minutes_after == min_mark]
            if matching:
                pcts.append(matching[0].pct_from_entry)
        if pcts:
            avg = np.mean(pcts)
            bar_len = int(abs(avg) * 20)
            if avg >= 0:
                bar = '█' * min(bar_len, 40)
                print(f"    {min_mark:>2}분 후: {avg:>+6.3f}%  (n={len(pcts)})  │{bar}")
            else:
                bar = '█' * min(bar_len, 40)
                print(f"    {min_mark:>2}분 후: {avg:>+6.3f}%  (n={len(pcts)})  {bar}│")

    # ── 4. 현재 파라미터 vs 최적 파라미터 ──
    print(f"\n{'─' * 100}")
    print(f"  [4] 현재 파라미터 vs 최적 파라미터 (그리드 서치)")
    print(f"{'─' * 100}")

    # 현재 파라미터 결과
    current_results = []
    for ta in analyses:
        r = simulate_with_params(ta, ta.tp_pct, abs(ta.sl_pct) * -1,
                                  1.5, -1.2, 0.3, 15)
        current_results.append(r)

    cur_pnls = [r['pnl'] for r in current_results]
    cur_wins = sum(1 for p in cur_pnls if p > 0)

    opt_pnls = [r['pnl'] for r in best_params['results']]
    opt_wins = sum(1 for p in opt_pnls if p > 0)

    print(f"\n  {'':>20}  {'현재 설정':>12}  {'최적 설정':>12}  {'변경':>10}")
    print(f"  {'─' * 60}")
    print(f"  {'TP (익절)':>20}  +{analyses[0].tp_pct:>5.1f}%       "
          f"+{best_params['tp']:>5.1f}%       "
          f"{'→ 변경' if abs(analyses[0].tp_pct - best_params['tp']) > 0.1 else '유지'}")
    print(f"  {'SL (손절)':>20}  {analyses[0].sl_pct:>+5.1f}%       "
          f"{best_params['sl']:>+5.1f}%       "
          f"{'→ 변경' if abs(analyses[0].sl_pct - best_params['sl']) > 0.1 else '유지'}")
    print(f"  {'트레일링 활성화':>20}  +{1.5:>5.1f}%       "
          f"+{best_params['activation']:>5.1f}%       "
          f"{'→ 변경' if abs(1.5 - best_params['activation']) > 0.1 else '유지'}")
    print(f"  {'트레일링 폭':>20}  {-1.2:>+5.1f}%       "
          f"{best_params['trail']:>+5.1f}%       "
          f"{'→ 변경' if abs(-1.2 - best_params['trail']) > 0.1 else '유지'}")
    print(f"  {'본전 이동':>20}  +{0.3:>5.1f}%       "
          f"+{best_params['be']:>5.1f}%       "
          f"{'→ 변경' if abs(0.3 - best_params['be']) > 0.1 else '유지'}")
    print(f"  {'홀딩 시간':>20}  {15:>5}분        "
          f"{best_params['hold']:>5}분        "
          f"{'→ 변경' if 15 != best_params['hold'] else '유지'}")

    print(f"\n  {'':>20}  {'현재':>12}  {'최적':>12}  {'개선':>10}")
    print(f"  {'─' * 60}")
    print(f"  {'승률':>20}  {cur_wins/len(cur_pnls)*100:>5.1f}%       "
          f"{opt_wins/len(opt_pnls)*100:>5.1f}%       "
          f"{(opt_wins/len(opt_pnls) - cur_wins/len(cur_pnls))*100:>+5.1f}%p")
    print(f"  {'총 수익률':>20}  {sum(cur_pnls):>+6.2f}%       "
          f"{sum(opt_pnls):>+6.2f}%       "
          f"{sum(opt_pnls) - sum(cur_pnls):>+6.2f}%")
    print(f"  {'평균 수익률':>20}  {np.mean(cur_pnls):>+6.3f}%      "
          f"{np.mean(opt_pnls):>+6.3f}%      "
          f"{np.mean(opt_pnls) - np.mean(cur_pnls):>+6.3f}%")

    if opt_pnls:
        win_pnls = [p for p in opt_pnls if p > 0]
        loss_pnls = [p for p in opt_pnls if p <= 0]
        if win_pnls and loss_pnls:
            pf = abs(sum(win_pnls)) / abs(sum(loss_pnls))
            print(f"  {'PF (최적)':>20}  {'':>12}  {pf:>6.2f}       ")

    # ── 5. 개별 매매 비교 ──
    print(f"\n{'─' * 100}")
    print(f"  [5] 개별 매매 — 현재 vs 최적 파라미터 비교")
    print(f"{'─' * 100}")
    print(f"  {'날짜':>8}  {'종목':<12}  {'현재 청산':>8}  {'현재 PnL':>8}  "
          f"{'최적 청산':>8}  {'최적 PnL':>8}  {'차이':>7}")
    print(f"  {'─' * 75}")

    for i, ta in enumerate(analyses):
        cur_r = current_results[i]
        opt_r = best_params['results'][i]
        diff = opt_r['pnl'] - cur_r['pnl']

        print(f"  {ta.date}  {ta.name:<10}  "
              f"{cur_r['type']:>8}  {cur_r['pnl']:>+6.2f}%  "
              f"{opt_r['type']:>8}  {opt_r['pnl']:>+6.2f}%  "
              f"{diff:>+5.2f}%")

    # ── 6. 핵심 인사이트 ──
    print(f"\n{'─' * 100}")
    print(f"  [6] 핵심 인사이트 & 튜닝 권고")
    print(f"{'─' * 100}")

    avg_mfe = np.mean(mfes)
    avg_mae = np.mean(maes)
    median_mfe = np.median(mfes)

    print(f"""
  1. MFE 분석:
     평균 MFE +{avg_mfe:.2f}%는 현재 TP 대비 {'도달 가능' if avg_mfe >= analyses[0].tp_pct else '부족'}
     → 최적 TP = MFE 중앙값({median_mfe:.2f}%)의 70~80% = +{median_mfe * 0.75:.1f}%

  2. MAE 분석:
     평균 MAE {avg_mae:.2f}%
     → MAE 90th percentile = {np.percentile(maes, 10):.2f}%
     → 최적 SL = MAE 90th + 여유(-0.2%) = {np.percentile(maes, 10) - 0.2:.1f}%

  3. 시간 패턴:
     MFE 평균 도달 시간: {np.mean(mfe_times):.0f}분
     MAE 평균 도달 시간: {np.mean(mae_times):.0f}분
     → {'MFE가 먼저 도달하므로 빠른 익절이 유리' if np.mean(mfe_times) < np.mean(mae_times) else 'MAE가 먼저 도달하므로 SL 확대 검토'}

  4. 트레일링 효과:
     트레일링으로 청산된 건: {sum(1 for ta in analyses if ta.actual_exit_type == 'TRAILING')}건
     TP로 청산된 건: {sum(1 for ta in analyses if ta.actual_exit_type == 'TP')}건
     → {'트레일링이 TP보다 효과적 — 트레일링 중심 전략 유지' if sum(1 for ta in analyses if ta.actual_exit_type == 'TRAILING') >= sum(1 for ta in analyses if ta.actual_exit_type == 'TP') else 'TP 도달률 개선 필요 — TP 하향 또는 트레일링 활성화 조건 완화'}

  5. 최적 파라미터 권고:
     TP: +{best_params['tp']:.1f}%  (현재: +{analyses[0].tp_pct:.1f}%)
     SL: {best_params['sl']:.1f}%  (현재: {analyses[0].sl_pct:.1f}%)
     트레일링 활성: +{best_params['activation']:.1f}%  (현재: +1.5%)
     트레일링 폭: {best_params['trail']:.1f}%  (현재: -1.2%)
     본전 이동: +{best_params['be']:.1f}%  (현재: +0.3%)
     홀딩 시간: {best_params['hold']}분  (현재: 15분)
""")

    return best_params


async def main():
    config = load_config('simulation')
    cache_dir = config.get('scanner', {}).get('cache_dir', './data/ohlcv_cache')
    trailing_cfg = config.get('strategy', {}).get('scalping', {}).get('trailing_stop', {})

    print("=" * 100)
    print("매수 종목 1분봉 경로 분석 & 전략 튜닝")
    print("=" * 100)

    # 데이터 준비
    print("\n[1] 데이터 로드...")
    all_ohlcv = load_ohlcv_cache(cache_dir)
    stock_names = load_stock_names(config)
    print(f"    {len(all_ohlcv)}종목 캐시, {len(stock_names)}개 종목명")

    # 3일간 분석
    dates = ['20260327', '20260330', '20260331']
    api = KiwoomRestAPI(config)
    await api.initialize()

    all_analyses = []

    for target_date in dates:
        print(f"\n[{target_date}] 상승 종목 추출...")
        candidates = find_surge_stocks(all_ohlcv, target_date)
        for c in candidates:
            c['name'] = stock_names.get(c['code'], c['code'])
        print(f"    {len(candidates)}종목 추출")

        target_codes = [c['code'] for c in candidates[:30]]
        print(f"    1분봉 수집 ({len(target_codes)}종목)...")
        candles_by_code = await collect_minute_candles(api, target_codes, target_date)
        total_candles = sum(len(v) for v in candles_by_code.values())
        print(f"    {len(candles_by_code)}종목, {total_candles:,}개 1분봉")

        if not candles_by_code:
            print(f"    데이터 없음 — 건너뜀")
            continue

        # 매수 시그널 추출
        print(f"    매수 시그널 탐색...")
        entries = find_entry_signals(
            candles_by_code, candidates, stock_names,
            all_ohlcv, config, target_date)
        print(f"    {len(entries)}건 매수 시그널")

        # 경로 분석
        for entry in entries:
            code = entry['code']
            candles = candles_by_code.get(code, [])
            ta = analyze_trade_path(entry, candles, trailing_cfg)
            all_analyses.append(ta)
            print(f"      {entry['name']}: MFE +{ta.mfe_pct:.2f}% ({ta.mfe_time_min}분) "
                  f"MAE {ta.mae_pct:.2f}% ({ta.mae_time_min}분) "
                  f"→ {ta.actual_exit_type} {ta.actual_exit_pct:+.2f}%")

    await api.close()

    if not all_analyses:
        print("\n  매매 건이 없어 분석 불가")
        return

    # 그리드 서치
    print(f"\n[그리드 서치] {len(all_analyses)}건 매매 대상 최적 파라미터 탐색...")
    best_params = grid_search_params(all_analyses)

    # 리포트
    print_analysis_report(all_analyses, best_params)

    # API 호출 통계
    counts = api.get_api_call_count()
    if counts:
        print(f"\n  API 호출: {counts}")

    print(f"\n{'=' * 100}")
    print("분석 완료")
    print("=" * 100)


if __name__ == '__main__':
    asyncio.run(main())
