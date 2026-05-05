"""
스캘핑 전략 인트라데이 백테스트 — 1분봉 기반

특정일의 1분봉 데이터를 API에서 수집하여, 시간 순서대로 매수/매도를
시뮬레이션한다. 실제 장중 흐름을 재현하여 정확한 수익률을 산출.

시뮬레이션 흐름:
  1. 대상일 상승 종목 추출 (일봉 캐시 기반)
  2. 해당 종목의 1분봉 데이터 API 수집
  3. 시간 순서대로 캔들을 순회하며:
     - 상승률 +5% 이상 감지 → ScalpingCoordinator 분석 → 매수 시그널
     - 매수 후 TP/SL/트레일링/시간초과 매도 체크
  4. 전체 매매 리포트 출력

Usage:
    python scripts/scalping_intraday_backtest.py --date 20260331
    python scripts/scalping_intraday_backtest.py --date 20260330 --equity 50000000
"""

import sys
import json
import asyncio
import argparse
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
logger = logging.getLogger(__name__)

# ── 수수료/세금 ──
BUY_FEE_PCT = 0.015
SELL_FEE_PCT = 0.015
SELL_TAX_PCT = 0.18
TOTAL_COST_PCT = BUY_FEE_PCT + SELL_FEE_PCT + SELL_TAX_PCT


@dataclass
class Position:
    """보유 포지션"""
    code: str
    name: str
    entry_price: int
    entry_time: str
    qty: int
    tp_pct: float
    sl_pct: float
    hold_minutes: float
    score: float
    high_watermark: int = 0  # 트레일링용 고점
    trailing_active: bool = False
    # 분할 매수 (개선 #4)
    add_buy_stage: int = 1   # 현재 매수 단계 (1=초기, 2=2차, 3=3차)
    total_invested: int = 0  # 총 투자금
    surge_type: str = ''     # gap_up / intraday (개선 #3)

    def tp_price(self) -> int:
        return int(self.entry_price * (1 + self.tp_pct / 100))

    def sl_price(self) -> int:
        return int(self.entry_price * (1 + self.sl_pct / 100))

    def breakeven_price(self, be_pct: float = 0.3) -> int:
        return int(self.entry_price * (1 + be_pct / 100))

    def avg_price(self) -> int:
        """평균 매수가"""
        return int(self.total_invested / self.qty) if self.qty > 0 else self.entry_price


@dataclass
class TradeResult:
    """매매 결과"""
    code: str
    name: str
    entry_price: int
    entry_time: str
    exit_price: int
    exit_time: str
    qty: int
    exit_type: str
    tp_pct: float
    sl_pct: float
    gross_pnl_pct: float
    net_pnl_pct: float
    net_pnl_amount: int
    score: float
    hold_seconds: int


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
    """대상일 상승 종목 추출"""
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
    """분봉 시간 파싱: 'YYYYMMDDHHmmss' → datetime"""
    try:
        return datetime.strptime(time_str, '%Y%m%d%H%M%S')
    except (ValueError, TypeError):
        return None


def check_exit(pos: Position, candle: dict, now_dt: datetime,
               trailing_cfg: dict) -> Optional[Tuple[str, int]]:
    """
    1분봉 단위로 매도 조건 체크

    Returns: (exit_type, exit_price) or None
    """
    high = candle['high']
    low = candle['low']
    close = candle['price']

    activation_pct = trailing_cfg.get('activation_pct', 1.5)
    trail_pct = trailing_cfg.get('trail_pct', -1.2)
    be_pct = trailing_cfg.get('breakeven_pct', 0.3)

    # 고점 갱신
    if high > pos.high_watermark:
        pos.high_watermark = high

    # 트레일링 활성화 체크
    if not pos.trailing_active:
        hwm_pct = (pos.high_watermark - pos.entry_price) / pos.entry_price * 100
        if hwm_pct >= activation_pct:
            pos.trailing_active = True

    # 1. SL 체크 (트레일링 활성 시 본전 이동)
    if pos.trailing_active:
        # 본전+0.3% SL
        effective_sl = max(pos.sl_price(), pos.breakeven_price(be_pct))
        # 트레일링: 고점 대비 trail% 하락
        trail_price = int(pos.high_watermark * (1 + trail_pct / 100))
        effective_sl = max(effective_sl, trail_price)

        if low <= effective_sl:
            return 'TRAILING', min(effective_sl, close)
    else:
        if low <= pos.sl_price():
            return 'SL', pos.sl_price()

    # 2. TP 체크
    if high >= pos.tp_price():
        return 'TP', pos.tp_price()

    # 3. 시간 초과
    entry_dt = parse_candle_time(pos.entry_time)
    if entry_dt:
        elapsed = (now_dt - entry_dt).total_seconds() / 60
        if elapsed >= pos.hold_minutes:
            return 'TIME_EXIT', close

    # 4. 장 마감 강제 청산 (14:50)
    if now_dt.time() >= time(14, 50):
        return 'FORCE_EXIT', close

    return None


async def collect_minute_candles(api: KiwoomRestAPI, codes: List[str],
                                  target_date: str) -> Dict[str, List[dict]]:
    """대상 종목의 1분봉 수집"""
    all_candles = {}
    total = len(codes)

    for i, code in enumerate(codes):
        try:
            candles = await api.get_intraday_chart(code, tick_scope=1, count=900)
            # 대상일 필터링
            day_candles = [c for c in candles if c['time'].startswith(target_date)]
            if day_candles:
                # 시간 오름차순 정렬
                day_candles.sort(key=lambda x: x['time'])
                all_candles[code] = day_candles
        except Exception as e:
            logger.debug(f"1분봉 수집 실패 [{code}]: {e}")

        if (i + 1) % 10 == 0 or i == total - 1:
            print(f"    1분봉 수집: {i+1}/{total} ({len(all_candles)}종목 성공)")

        # API 한도 보호
        if api.is_quota_exhausted('ka10080'):
            print(f"    ⚠ ka10080 API 한도 초과 — 수집 중단 ({len(all_candles)}종목)")
            break

    return all_candles


def _check_pullback(code, price, candle, cur_change,
                    surge_detected, intraday_high,
                    min_drop_pct, min_bounce, pullback_cfg) -> bool:
    """눌림목 진입 조건 확인. True면 진입 허용."""
    # 급등 미감지 → 불허
    if code not in surge_detected:
        if cur_change < 5.0:
            return False
        surge_detected[code] = True
        intraday_high[code] = candle['high']
        return False  # 첫 감지 시에는 고점 추적만 시작

    high = intraday_high.get(code, price)
    drop_pct = (high - price) / high * 100 if high > 0 else 0
    candle_pct = (candle['price'] - candle['open']) / candle['open'] * 100 if candle['open'] > 0 else 0

    if drop_pct < min_drop_pct or candle_pct < min_bounce:
        return False

    min_change_after = pullback_cfg.get('min_change_after_pullback', 3.0)
    if cur_change < min_change_after or cur_change > 30.0:
        return False

    return True


def run_intraday_simulation(
    candles_by_code: Dict[str, List[dict]],
    candidates: list,
    stock_names: dict,
    all_ohlcv: dict,
    config: dict,
    target_date: str,
    total_equity: int = 50_000_000,
) -> List[TradeResult]:
    """1분봉 시간순 매매 시뮬레이션"""

    scalp_cfg = config.get('strategy', {}).get('scalping', {})
    max_per_stock_pct = scalp_cfg.get('max_per_stock_pct', 5.0) / 100
    initial_ratio = scalp_cfg.get('initial_entry_ratio', 0.6)
    trailing_cfg = scalp_cfg.get('trailing_stop', {})
    min_score = scalp_cfg.get('min_score', 50)

    year, month, day = int(target_date[:4]), int(target_date[4:6]), int(target_date[6:8])

    # 후보 종목 맵
    cand_map = {c['code']: c for c in candidates}

    # 전체 1분봉을 시간 순서로 병합
    timeline = []
    for code, candles in candles_by_code.items():
        for c in candles:
            dt = parse_candle_time(c['time'])
            if dt and time(9, 1) <= dt.time() <= time(15, 30):
                timeline.append((dt, code, c))
    timeline.sort(key=lambda x: x[0])

    if not timeline:
        return []

    # 종목별 현재 상태 추적
    stock_state = {}  # {code: {'high': int, 'low': int, 'price': int, 'volume': int, ...}}
    positions: Dict[str, Position] = {}  # {code: Position}
    trades: List[TradeResult] = []
    entered_codes = set()  # 당일 진입한 종목
    last_analysis_time = {}  # {code: datetime} 마지막 분석 시간

    # 종목별 누적 거래량
    cumul_volume = {}

    # ── 눌림목 진입 추적 ──
    pullback_cfg = scalp_cfg.get('pullback_entry', {})
    pullback_enabled = pullback_cfg.get('enabled', True)
    pullback_min_drop_pct = pullback_cfg.get('min_drop_pct', 2.0)
    pullback_min_bounce = pullback_cfg.get('min_bounce_pct', 0.3)
    surge_detected = {}   # {code: True}
    intraday_high = {}    # {code: int}

    # ── [개선#1] 거래량 확인: 반등 시 직전 N분 평균 대비 배수 ──
    vol_confirm_cfg = scalp_cfg.get('volume_confirmation', {})
    vol_confirm_enabled = vol_confirm_cfg.get('enabled', True)
    vol_confirm_ratio = vol_confirm_cfg.get('min_ratio', 1.5)  # 직전 5분 평균 대비 1.5배
    vol_confirm_window = vol_confirm_cfg.get('window', 5)
    recent_volumes = {}   # {code: [vol1, vol2, ...]} 최근 N분 거래량

    # ── [개선#2] ATR 기반 동적 SL ──
    dynamic_sl_cfg = scalp_cfg.get('dynamic_sl', {})
    dynamic_sl_enabled = dynamic_sl_cfg.get('enabled', True)
    dynamic_sl_atr_mult = dynamic_sl_cfg.get('atr_multiplier', 2.0)
    dynamic_sl_atr_period = dynamic_sl_cfg.get('atr_period', 14)
    dynamic_sl_floor = dynamic_sl_cfg.get('floor_pct', -4.0)   # SL 최대 하한
    dynamic_sl_ceil = dynamic_sl_cfg.get('ceil_pct', -1.5)      # SL 최소 상한
    candle_history = {}   # {code: [(high, low, close), ...]} ATR 계산용

    # ── [개선#3] surge_type 감지 (gap_up vs intraday) ──
    stock_surge_type = {}  # {code: 'gap_up' | 'intraday'}

    # ── [개선#4] 분할 매수 ──
    split_entry_cfg = scalp_cfg.get('split_entry', {})
    split_entry_enabled = split_entry_cfg.get('enabled', True)
    split_ratios = split_entry_cfg.get('ratios', [0.3, 0.3, 0.4])
    split_triggers = split_entry_cfg.get('trigger_pcts', [0.0, 0.5, 1.0])

    # ── [개선#5] 일일 매매 횟수 제한 ──
    max_daily_trades = scalp_cfg.get('max_daily_trades', 5)
    daily_trade_count = 0

    for dt, code, candle in timeline:
        name = stock_names.get(code, code)
        cand = cand_map.get(code)
        if not cand:
            continue

        prev_close = cand['prev_close']
        price = candle['price']

        # 누적 거래량 업데이트
        cumul_volume[code] = cumul_volume.get(code, 0) + candle['volume']

        # 종목 상태 업데이트
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

        # [개선#1] 최근 거래량 히스토리 (반등 시 거래량 확인용)
        if code not in recent_volumes:
            recent_volumes[code] = []
        recent_volumes[code].append(candle['volume'])
        if len(recent_volumes[code]) > vol_confirm_window + 1:
            recent_volumes[code] = recent_volumes[code][-(vol_confirm_window + 1):]

        # [개선#2] 캔들 히스토리 (ATR 계산용)
        if code not in candle_history:
            candle_history[code] = []
        candle_history[code].append((candle['high'], candle['low'], candle['price']))
        if len(candle_history[code]) > dynamic_sl_atr_period + 1:
            candle_history[code] = candle_history[code][-(dynamic_sl_atr_period + 1):]

        # [개선#3] surge_type 감지 (gap_up vs intraday)
        if code not in stock_surge_type and prev_close > 0:
            stock_open = stock_state[code]['open']
            open_change = (stock_open - prev_close) / prev_close * 100
            stock_surge_type[code] = 'gap_up' if open_change >= 3.0 else 'intraday'

        # 눌림목: 급등 감지 및 장중 고점 추적
        if prev_close > 0:
            chg_track = (price - prev_close) / prev_close * 100
            if chg_track >= 5.0 and code not in surge_detected:
                surge_detected[code] = True
                intraday_high[code] = candle['high']
            elif code in surge_detected:
                if candle['high'] > intraday_high.get(code, 0):
                    intraday_high[code] = candle['high']

        # ── 보유 중이면 분할 매수 체크 → 매도 체크 ──
        if code in positions:
            pos = positions[code]

            # [개선#4] 분할 매수: 수익 구간 진입 시 추가 매수
            if split_entry_enabled and pos.add_buy_stage < len(split_ratios):
                cur_pnl = (price - pos.entry_price) / pos.entry_price * 100
                next_trigger = split_triggers[pos.add_buy_stage] if pos.add_buy_stage < len(split_triggers) else 999
                if cur_pnl >= next_trigger:
                    max_amount = int(total_equity * max_per_stock_pct)
                    add_ratio = split_ratios[pos.add_buy_stage]
                    add_qty = int(max_amount * add_ratio) // price
                    if add_qty > 0:
                        pos.total_invested += price * add_qty
                        pos.qty += add_qty
                        pos.entry_price = pos.avg_price()
                        pos.add_buy_stage += 1

            result = check_exit(pos, candle, dt, trailing_cfg)
            if result:
                exit_type, exit_price = result
                avg_entry = pos.avg_price()
                gross_pnl = (exit_price - avg_entry) / avg_entry * 100
                net_pnl = gross_pnl - TOTAL_COST_PCT
                net_amount = int(pos.total_invested * net_pnl / 100)
                entry_dt = parse_candle_time(pos.entry_time)
                hold_sec = int((dt - entry_dt).total_seconds()) if entry_dt else 0

                trades.append(TradeResult(
                    code=code, name=name,
                    entry_price=avg_entry,
                    entry_time=pos.entry_time,
                    exit_price=exit_price,
                    exit_time=candle['time'],
                    qty=pos.qty, exit_type=exit_type,
                    tp_pct=pos.tp_pct, sl_pct=pos.sl_pct,
                    gross_pnl_pct=round(gross_pnl, 3),
                    net_pnl_pct=round(net_pnl, 3),
                    net_pnl_amount=net_amount,
                    score=pos.score,
                    hold_seconds=hold_sec,
                ))
                del positions[code]
            continue

        # ── 매수 시그널 체크 ──
        # 진입 조건: 09:10~14:00, 미보유, 미진입(당일 2회 제한)
        if dt.time() < time(9, 10) or dt.time() >= time(14, 0):
            continue
        if code in positions or code in entered_codes:
            continue
        # 데드존 (11:00~11:30)
        if time(11, 0) <= dt.time() < time(11, 30):
            continue

        # [개선#5] 일일 매매 횟수 제한
        if daily_trade_count >= max_daily_trades:
            continue

        # 현재 등락률 체크
        if prev_close <= 0:
            continue
        cur_change = (price - prev_close) / prev_close * 100

        # ── [개선#3] surge_type별 진입 전략 분리 ──
        s_type = stock_surge_type.get(code, 'intraday')

        if s_type == 'gap_up':
            # 갭상승 종목: 골든타임(~09:30) 즉시 진입, 이후는 눌림목
            if dt.time() <= time(9, 30):
                # 즉시 진입 — +5~30% 범위 체크만
                if cur_change < 5.0 or cur_change > 30.0:
                    continue
            else:
                # 09:30 이후 갭상승 종목도 눌림목 적용
                if not _check_pullback(code, price, candle, cur_change,
                                       surge_detected, intraday_high,
                                       pullback_min_drop_pct, pullback_min_bounce,
                                       pullback_cfg):
                    continue
        else:
            # 장중 급등 종목: 항상 눌림목 필터 적용
            if pullback_enabled:
                if not _check_pullback(code, price, candle, cur_change,
                                       surge_detected, intraday_high,
                                       pullback_min_drop_pct, pullback_min_bounce,
                                       pullback_cfg):
                    continue
            else:
                if cur_change < 5.0 or cur_change > 30.0:
                    continue

        # ── [개선#1] 거래량 확인: 진입 캔들 거래량 ≥ 직전 N분 평균 × 배수 ──
        # 장중급등(intraday) 눌림목 반등 시만 적용 (갭상승 골든타임 제외)
        if vol_confirm_enabled and s_type == 'intraday':
            vols = recent_volumes.get(code, [])
            if len(vols) >= vol_confirm_window + 1:
                recent_avg = np.mean(vols[:-1])
                cur_vol = vols[-1]
                if recent_avg > 0 and cur_vol < recent_avg * vol_confirm_ratio:
                    continue

        # 거래대금 체크
        cur_trade_value = price * cumul_volume.get(code, 0)
        min_tv = 5_000_000_000  # 50억
        if dt.time() >= time(9, 30):
            min_tv = 10_000_000_000  # 100억
        if dt.time() >= time(11, 30):
            min_tv = 20_000_000_000  # 200억
        if cur_trade_value < min_tv:
            continue

        # 분석 빈도 제한 (종목당 5분 간격)
        last_t = last_analysis_time.get(code)
        if last_t and (dt - last_t).total_seconds() < 300:
            continue

        # ── ScalpingCoordinator 분석 ──
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

        # ── [개선#2] ATR 기반 동적 SL ──
        final_sl = analysis.scalp_sl_pct
        if dynamic_sl_enabled:
            hist = candle_history.get(code, [])
            if len(hist) >= dynamic_sl_atr_period:
                trs = []
                for i in range(1, len(hist)):
                    h, l, c_prev = hist[i][0], hist[i][1], hist[i-1][2]
                    tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
                    trs.append(tr)
                if trs:
                    atr = np.mean(trs[-dynamic_sl_atr_period:])
                    atr_sl_pct = -(atr * dynamic_sl_atr_mult / price * 100)
                    # floor/ceil 제한
                    atr_sl_pct = max(atr_sl_pct, dynamic_sl_floor)   # 최대 하한
                    atr_sl_pct = min(atr_sl_pct, dynamic_sl_ceil)    # 최소 상한
                    final_sl = round(atr_sl_pct, 2)

        # ── 매수 진입 ──
        # [개선#4] 분할 매수: 1차 비율만 진입
        if split_entry_enabled:
            first_ratio = split_ratios[0]
            max_amount = int(total_equity * max_per_stock_pct * first_ratio)
        else:
            max_amount = int(total_equity * max_per_stock_pct * initial_ratio)
        qty = max_amount // price
        if qty <= 0:
            continue

        invested = price * qty
        positions[code] = Position(
            code=code, name=name,
            entry_price=price,
            entry_time=candle['time'],
            qty=qty,
            tp_pct=analysis.scalp_tp_pct,
            sl_pct=final_sl,
            hold_minutes=analysis.hold_minutes,
            score=analysis.total_score,
            high_watermark=price,
            add_buy_stage=1,
            total_invested=invested,
            surge_type=s_type,
        )
        entered_codes.add(code)
        daily_trade_count += 1

    # 장 마감 후 미청산 포지션 강제 청산
    for code, pos in list(positions.items()):
        name = stock_names.get(code, code)
        last_candle = candles_by_code.get(code, [{}])[-1]
        exit_price = last_candle.get('price', pos.entry_price)
        avg_entry = pos.avg_price()
        gross_pnl = (exit_price - avg_entry) / avg_entry * 100
        net_pnl = gross_pnl - TOTAL_COST_PCT
        net_amount = int(pos.total_invested * net_pnl / 100)
        entry_dt = parse_candle_time(pos.entry_time)
        exit_t = last_candle.get('time', pos.entry_time)
        exit_dt = parse_candle_time(exit_t)
        hold_sec = int((exit_dt - entry_dt).total_seconds()) if entry_dt and exit_dt else 0

        trades.append(TradeResult(
            code=code, name=name,
            entry_price=avg_entry,
            entry_time=pos.entry_time,
            exit_price=exit_price,
            exit_time=exit_t,
            qty=pos.qty, exit_type='MARKET_CLOSE',
            tp_pct=pos.tp_pct, sl_pct=pos.sl_pct,
            gross_pnl_pct=round(gross_pnl, 3),
            net_pnl_pct=round(net_pnl, 3),
            net_pnl_amount=net_amount,
            score=pos.score,
            hold_seconds=hold_sec,
        ))

    trades.sort(key=lambda t: t.entry_time)
    return trades


def print_report(trades: List[TradeResult], target_date: str, total_equity: int):
    """매매 리포트 출력"""
    print("\n" + "=" * 95)
    print(f"스캘핑 인트라데이 백테스트 리포트 — {target_date}")
    print("=" * 95)

    if not trades:
        print("\n  매매 발생 없음")
        return

    total_trades = len(trades)
    tp_cnt = sum(1 for t in trades if t.exit_type == 'TP')
    sl_cnt = sum(1 for t in trades if t.exit_type == 'SL')
    trail_cnt = sum(1 for t in trades if t.exit_type == 'TRAILING')
    time_cnt = sum(1 for t in trades if t.exit_type == 'TIME_EXIT')
    force_cnt = sum(1 for t in trades if t.exit_type in ('FORCE_EXIT', 'MARKET_CLOSE'))
    win_cnt = sum(1 for t in trades if t.net_pnl_pct > 0)
    loss_cnt = total_trades - win_cnt
    win_rate = win_cnt / total_trades * 100

    total_pnl = sum(t.net_pnl_amount for t in trades)
    total_invested = sum(t.entry_price * t.qty for t in trades)
    avg_hold = np.mean([t.hold_seconds for t in trades])

    win_pnls = [t.net_pnl_pct for t in trades if t.net_pnl_pct > 0]
    loss_pnls = [t.net_pnl_pct for t in trades if t.net_pnl_pct <= 0]

    print(f"""
  총자산: {total_equity:,}원 | 수수료+세금: {TOTAL_COST_PCT:.2f}%

  ┌──────────────────────────────────────────────────────────┐
  │  총 매매:    {total_trades:>3}건                                         │
  │    TP 익절:  {tp_cnt:>3}건  SL 손절: {sl_cnt:>3}건  트레일링: {trail_cnt:>3}건          │
  │    시간초과: {time_cnt:>3}건  강제청산: {force_cnt:>3}건                          │
  ├──────────────────────────────────────────────────────────┤
  │  승률:       {win_rate:>5.1f}%  ({win_cnt}승 {loss_cnt}패)                          │
  │  평균 홀딩:  {avg_hold/60:>5.1f}분                                       │
  ├──────────────────────────────────────────────────────────┤
  │  총 순수익:  {total_pnl:>+12,}원                                  │
  │  총 투자금:  {total_invested:>12,}원                                  │
  │  수익률:     {total_pnl/total_invested*100 if total_invested else 0:>+6.2f}%                                          │
  └──────────────────────────────────────────────────────────┘""")

    if win_pnls:
        print(f"  수익 거래: 평균 +{np.mean(win_pnls):.2f}% | 최대 +{max(win_pnls):.2f}%")
    if loss_pnls:
        print(f"  손실 거래: 평균 {np.mean(loss_pnls):.2f}% | 최대 {min(loss_pnls):.2f}%")
    if win_pnls and loss_pnls:
        pf = abs(sum(win_pnls)) / abs(sum(loss_pnls))
        print(f"  Profit Factor: {pf:.2f} | R:R = {abs(np.mean(win_pnls)):.2f}:{abs(np.mean(loss_pnls)):.2f}")

    # 개별 매매 상세
    print(f"\n  {'시간':>14} {'종목':<12} {'매수가':>9} {'매도가':>9} {'수량':>5} "
          f"{'유형':>10} {'TP':>5} {'SL':>5} {'점수':>4} "
          f"{'홀딩':>5} {'순수익률':>7} {'순수익':>10}")
    print("  " + "-" * 110)

    for t in trades:
        entry_t = t.entry_time[8:12] if len(t.entry_time) >= 12 else t.entry_time
        exit_t = t.exit_time[8:12] if len(t.exit_time) >= 12 else t.exit_time
        icon = {'TP': '✅', 'SL': '❌', 'TRAILING': '🔄',
                'TIME_EXIT': '⏰', 'FORCE_EXIT': '🔚',
                'MARKET_CLOSE': '🔚'}.get(t.exit_type, '?')
        hold_min = t.hold_seconds / 60

        print(f"  {entry_t}→{exit_t} {t.name:<10} "
              f"{t.entry_price:>9,} {t.exit_price:>9,} {t.qty:>5} "
              f"{icon}{t.exit_type:<8} "
              f"+{t.tp_pct:.1f} {t.sl_pct:.1f} {t.score:>4.0f} "
              f"{hold_min:>4.0f}분 "
              f"{t.net_pnl_pct:>+6.2f}% {t.net_pnl_amount:>+9,}")

    # 시간대별 분석
    if len(trades) >= 3:
        print(f"\n  ── 시간대별 성과 ──")
        time_buckets = {'09:10~09:30': [], '09:30~11:00': [],
                        '11:30~13:00': [], '13:00~14:00': []}
        for t in trades:
            h = int(t.entry_time[8:10]) if len(t.entry_time) >= 10 else 0
            m = int(t.entry_time[10:12]) if len(t.entry_time) >= 12 else 0
            et = time(h, m)
            if et < time(9, 30):
                time_buckets['09:10~09:30'].append(t)
            elif et < time(11, 0):
                time_buckets['09:30~11:00'].append(t)
            elif et < time(13, 0):
                time_buckets['11:30~13:00'].append(t)
            else:
                time_buckets['13:00~14:00'].append(t)

        for bucket, bucket_trades in time_buckets.items():
            if not bucket_trades:
                continue
            w = sum(1 for t in bucket_trades if t.net_pnl_pct > 0)
            pnl = sum(t.net_pnl_amount for t in bucket_trades)
            wr = w / len(bucket_trades) * 100
            print(f"    {bucket}: {len(bucket_trades)}건 | "
                  f"승률 {wr:.0f}% | 순수익 {pnl:>+,}원")


async def run_single_date(date: str, config: dict, all_ohlcv: dict,
                          stock_names: dict, equity: int, max_stocks: int) -> List[TradeResult]:
    """단일 날짜 백테스트 실행"""
    print("=" * 95)
    print(f"스캘핑 인트라데이 백테스트 — 1분봉 기반 ({date})")
    print("=" * 95)

    # 상승 종목 추출
    print(f"\n[2] {date} 상승 종목 추출...")
    candidates = find_surge_stocks(all_ohlcv, date)
    for c in candidates:
        c['name'] = stock_names.get(c['code'], c['code'])
    print(f"    → {len(candidates)}종목 (거래대금 순)")
    for c in candidates[:10]:
        print(f"       {c['code']} {c['name']:<12} "
              f"+{c['change_pct']:.1f}% "
              f"거래대금 {c['trade_value']/1e8:.0f}억")

    # 1분봉 수집
    target_codes = [c['code'] for c in candidates[:max_stocks]]
    print(f"\n[3] 1분봉 데이터 수집 ({len(target_codes)}종목)...")

    api = KiwoomRestAPI(config)
    await api.initialize()
    candles_by_code = await collect_minute_candles(api, target_codes, date)
    await api.close()

    total_candles = sum(len(v) for v in candles_by_code.values())
    print(f"    → {len(candles_by_code)}종목, 총 {total_candles:,}개 1분봉")

    if not candles_by_code:
        print("\n  1분봉 데이터 없음")
        return []

    # 시뮬레이션
    print(f"\n[4] 인트라데이 시뮬레이션 실행...")
    trades = run_intraday_simulation(
        candles_by_code, candidates, stock_names,
        all_ohlcv, config, date, equity,
    )

    print_report(trades, date, equity)

    counts = api.get_api_call_count()
    if counts:
        print(f"\n  API 호출: {counts}")

    return trades


async def main():
    parser = argparse.ArgumentParser(description='스캘핑 인트라데이 백테스트')
    parser.add_argument('--date', type=str, help='대상일 (YYYYMMDD)')
    parser.add_argument('--dates', type=str, nargs='+', help='복수 날짜 (YYYYMMDD ...)')
    parser.add_argument('--equity', type=int, default=50_000_000, help='총자산')
    parser.add_argument('--max-stocks', type=int, default=30, help='최대 수집 종목 수')
    args = parser.parse_args()

    if not args.date and not args.dates:
        parser.error('--date 또는 --dates 필수')

    # 날짜 목록 구성
    dates = []
    if args.dates:
        dates = [d.replace('-', '') for d in args.dates]
    elif args.date:
        dates = [args.date.replace('-', '')]

    config = load_config('simulation')
    cache_dir = config.get('scanner', {}).get('cache_dir', './data/ohlcv_cache')

    print("\n[1] OHLCV 캐시 로드...")
    all_ohlcv = load_ohlcv_cache(cache_dir)
    stock_names = load_stock_names(config)
    print(f"    → {len(all_ohlcv)}종목 캐시, {len(stock_names)}개 종목명")

    # [개선#6] 멀티 날짜 백테스트
    all_trades = []
    for date in dates:
        day_trades = await run_single_date(
            date, config, all_ohlcv, stock_names, args.equity, args.max_stocks)
        all_trades.extend(day_trades)

    # 복수 날짜 종합 리포트
    if len(dates) > 1 and all_trades:
        print("\n" + "=" * 95)
        print(f"종합 리포트 — {len(dates)}일 ({dates[0]}~{dates[-1]})")
        print("=" * 95)
        print_report(all_trades, f"{dates[0]}~{dates[-1]}", args.equity)

    print(f"\n{'='*95}")
    print("백테스트 완료")
    print("=" * 95)


if __name__ == '__main__':
    asyncio.run(main())
