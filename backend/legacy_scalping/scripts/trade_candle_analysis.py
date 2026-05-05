#!/usr/bin/env python3
"""
매매 시점 1분봉 분석 스크립트

장마감 후 실행:
  python3 scripts/trade_candle_analysis.py                  # 오늘 매매 분석
  python3 scripts/trade_candle_analysis.py --date 20260402  # 특정일 분석
  python3 scripts/trade_candle_analysis.py --all             # 전체 누적 분석

기능:
  1. trades.jsonl에서 당일 스캘핑 매매 추출 (BUY/SELL 페어 매칭)
  2. 매매 종목별 1분봉 데이터 수집 (키움 API ka10080)
  3. 진입/매도 시점 전후 1분봉 분석 (MFE, MAE, 최적 진입/청산 시점)
  4. 누적 데이터 저장 → data/trade_candle_analysis.jsonl
  5. 요약 리포트 출력
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# 프로젝트 루트를 sys.path에 추가
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import yaml
from execution.kiwoom_api import KiwoomRestAPI

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
logger = logging.getLogger(__name__)

# ─── 설정 ───
TRADES_LOG = ROOT / 'logs' / 'trades.jsonl'
OUTPUT_DIR = ROOT / 'data'
CUMULATIVE_FILE = OUTPUT_DIR / 'trade_candle_analysis.jsonl'
REPORT_DIR = ROOT / 'logs' / 'candle_reports'


def load_config() -> dict:
    with open(ROOT / 'config' / 'settings.yaml', 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_trades(target_date: str) -> List[Dict]:
    """trades.jsonl에서 특정 날짜의 스캘핑 매매 추출, BUY/SELL 페어 매칭"""
    if not TRADES_LOG.exists():
        logger.error(f"trades.jsonl 없음: {TRADES_LOG}")
        return []

    buys = {}   # {(code, order_idx): buy_record}
    sells = []  # sell records
    buy_counts = {}  # {code: count} — 매수 순번 추적

    with open(TRADES_LOG, 'r', encoding='utf-8') as f:
        for line in f:
            t = json.loads(line.strip())
            ts = t.get('timestamp', '')
            if not ts.startswith(target_date[:4] + '-' + target_date[4:6] + '-' + target_date[6:8]):
                continue

            if t['action'] == 'BUY' and t.get('strategy_type') == 'scalping':
                code = t['code']
                buy_counts[code] = buy_counts.get(code, 0) + 1
                buys[(code, buy_counts[code])] = t
            elif t['action'] == 'SELL' and t.get('strategy_type') == 'scalping':
                sells.append(t)

    # BUY/SELL 페어 매칭
    pairs = []
    sell_counts = {}
    for sell in sells:
        code = sell['code']
        sell_counts[code] = sell_counts.get(code, 0) + 1
        idx = sell_counts[code]
        buy = buys.get((code, idx))
        if buy:
            pairs.append({
                'code': code,
                'name': sell.get('name', buy.get('name', '')),
                'buy_time': buy['timestamp'],
                'buy_price': buy['price'],
                'buy_qty': buy['qty'],
                'sell_time': sell['timestamp'],
                'sell_price': sell['price'],
                'sell_qty': sell['qty'],
                'pnl_pct': sell.get('pnl_pct', 0),
                'exit_type': sell.get('exit_type', ''),
                'reason': sell.get('reason', ''),
                'mae_pct': sell.get('mae_pct'),
                'signal_price': buy.get('signal_price'),
            })

    return pairs


async def fetch_candles(
    api: KiwoomRestAPI, code: str, target_date: str,
) -> List[dict]:
    """특정 종목의 당일 1분봉 전체 수집"""
    try:
        candles = await api.get_intraday_chart(
            code=code, tick_scope=1, count=400,
            base_dt=target_date, max_pages=5,
        )
        # 당일 데이터만 필터
        day_candles = []
        for c in candles:
            dt_str = c.get('stck_bsop_date', c.get('date', ''))
            if dt_str == target_date:
                day_candles.append(c)
        return sorted(day_candles, key=lambda x: x.get('stck_cntg_hour', x.get('time', '')))
    except Exception as e:
        logger.error(f"[{code}] 1분봉 수집 실패: {e}")
        return []


def analyze_trade_with_candles(
    trade: dict, candles: List[dict],
) -> dict:
    """매매 1건에 대해 1분봉 기반 분석 수행"""
    buy_time = datetime.fromisoformat(trade['buy_time'])
    sell_time = datetime.fromisoformat(trade['sell_time'])
    buy_price = trade['buy_price']
    sell_price = trade['sell_price']

    # 1분봉 시간 파싱
    parsed = []
    for c in candles:
        t_str = c.get('stck_cntg_hour', c.get('time', ''))
        if len(t_str) >= 6:
            h, m, s = int(t_str[:2]), int(t_str[2:4]), int(t_str[4:6])
            candle_time = buy_time.replace(hour=h, minute=m, second=s)
            parsed.append({
                'time': candle_time,
                'time_str': t_str,
                'open': float(c.get('stck_oprc', c.get('open', 0))),
                'high': float(c.get('stck_hgpr', c.get('high', 0))),
                'low': float(c.get('stck_lwpr', c.get('low', 0))),
                'close': float(c.get('stck_clpr', c.get('close', 0))),
                'volume': int(c.get('cntg_vol', c.get('volume', 0))),
            })

    if not parsed:
        # 캔들 없으면 기본 값으로 채움
        hold_seconds = int((sell_time - buy_time).total_seconds())
        return {
            'date': buy_time.strftime('%Y-%m-%d'),
            'code': trade['code'],
            'name': trade['name'],
            'buy_time': trade['buy_time'],
            'buy_price': buy_price,
            'sell_time': trade['sell_time'],
            'sell_price': sell_price,
            'pnl_pct': round(trade['pnl_pct'], 2),
            'exit_type': trade['exit_type'],
            'hold_seconds': hold_seconds,
            'mfe_pct': round(trade['pnl_pct'], 2) if trade['pnl_pct'] > 0 else 0,
            'mfe_price': max(buy_price, sell_price),
            'mfe_time': sell_time.strftime('%H:%M:%S'),
            'mae_pct': round(trade.get('mae_pct') or trade['pnl_pct'], 2) if trade['pnl_pct'] < 0 else 0,
            'mae_price': min(buy_price, sell_price),
            'mae_time': sell_time.strftime('%H:%M:%S'),
            'ext_mfe_pct': round(trade['pnl_pct'], 2) if trade['pnl_pct'] > 0 else 0,
            'ext_mfe_time': sell_time.strftime('%H:%M:%S'),
            'missed_upside_pct': 0, 'avoided_downside_pct': 0,
            'entry_candle': None, 'pre_entry_pattern': [],
            'optimal_entry_diff_pct': 0, 'optimal_exit_diff_pct': 0,
            'entry_volume': 0, 'hold_candle_count': 0,
            'no_candle_data': True,
        }

    # 진입~매도 구간 캔들
    entry_candles = [c for c in parsed if buy_time - timedelta(minutes=1) <= c['time'] <= sell_time + timedelta(minutes=1)]
    # 진입 전 5분 캔들
    pre_entry = [c for c in parsed if buy_time - timedelta(minutes=6) <= c['time'] < buy_time]
    # 매도 후 10분 캔들
    post_exit = [c for c in parsed if sell_time < c['time'] <= sell_time + timedelta(minutes=11)]

    # MFE (Maximum Favorable Excursion) — 진입 후 최고점
    mfe_price = buy_price
    mfe_time = buy_time
    mae_price = buy_price
    mae_time = buy_time

    hold_candles = [c for c in parsed if c['time'] >= buy_time and c['time'] <= sell_time]
    for c in hold_candles:
        if c['high'] > mfe_price:
            mfe_price = c['high']
            mfe_time = c['time']
        if c['low'] < mae_price:
            mae_price = c['low']
            mae_time = c['time']

    mfe_pct = (mfe_price - buy_price) / buy_price * 100
    mae_pct = (mae_price - buy_price) / buy_price * 100

    # 진입 후 30분까지 확장 MFE (더 기다렸으면 얼마나 벌었을까)
    extended_candles = [c for c in parsed if buy_time <= c['time'] <= buy_time + timedelta(minutes=30)]
    ext_mfe_price = buy_price
    ext_mfe_time = buy_time
    for c in extended_candles:
        if c['high'] > ext_mfe_price:
            ext_mfe_price = c['high']
            ext_mfe_time = c['time']
    ext_mfe_pct = (ext_mfe_price - buy_price) / buy_price * 100

    # 매도 후 가격 추이 (후회 분석)
    post_prices = [c['close'] for c in post_exit]
    post_max = max(post_prices) if post_prices else sell_price
    post_min = min(post_prices) if post_prices else sell_price
    missed_upside_pct = (post_max - sell_price) / sell_price * 100
    avoided_downside_pct = (post_min - sell_price) / sell_price * 100

    # 진입 전 캔들 패턴 (직전 3분봉의 양/음봉 카운트)
    pre_pattern = []
    for c in pre_entry[-3:]:
        chg = (c['close'] - c['open']) / c['open'] * 100 if c['open'] > 0 else 0
        pre_pattern.append(round(chg, 2))

    # 진입 시점 캔들 (진입이 발생한 1분봉)
    entry_candle = None
    for c in parsed:
        if abs((c['time'] - buy_time).total_seconds()) <= 60:
            entry_candle = {
                'open': c['open'], 'high': c['high'],
                'low': c['low'], 'close': c['close'],
                'volume': c['volume'],
                'body_pct': round((c['close'] - c['open']) / c['open'] * 100, 2) if c['open'] > 0 else 0,
            }
            break

    # 최적 진입 시점 (진입 전후 5분 내 최저가)
    near_entry = [c for c in parsed
                  if buy_time - timedelta(minutes=5) <= c['time'] <= buy_time + timedelta(minutes=2)]
    optimal_entry_price = min(c['low'] for c in near_entry) if near_entry else buy_price
    optimal_entry_diff_pct = (buy_price - optimal_entry_price) / optimal_entry_price * 100

    # 최적 매도 시점 (보유기간 내 최고가)
    optimal_exit_diff_pct = (mfe_price - sell_price) / sell_price * 100 if sell_price > 0 else 0

    # 보유 시간
    hold_seconds = (sell_time - buy_time).total_seconds()

    analysis = {
        'date': buy_time.strftime('%Y-%m-%d'),
        'code': trade['code'],
        'name': trade['name'],
        'buy_time': trade['buy_time'],
        'buy_price': buy_price,
        'sell_time': trade['sell_time'],
        'sell_price': sell_price,
        'pnl_pct': round(trade['pnl_pct'], 2),
        'exit_type': trade['exit_type'],
        'hold_seconds': int(hold_seconds),
        # MFE/MAE
        'mfe_pct': round(mfe_pct, 2),
        'mfe_price': mfe_price,
        'mfe_time': mfe_time.strftime('%H:%M:%S'),
        'mae_pct': round(mae_pct, 2),
        'mae_price': mae_price,
        'mae_time': mae_time.strftime('%H:%M:%S'),
        # 확장 MFE (30분)
        'ext_mfe_pct': round(ext_mfe_pct, 2),
        'ext_mfe_time': ext_mfe_time.strftime('%H:%M:%S'),
        # 매도 후 가격 추이
        'missed_upside_pct': round(missed_upside_pct, 2),
        'avoided_downside_pct': round(avoided_downside_pct, 2),
        # 진입 시점 분석
        'entry_candle': entry_candle,
        'pre_entry_pattern': pre_pattern,
        'optimal_entry_diff_pct': round(optimal_entry_diff_pct, 2),
        'optimal_exit_diff_pct': round(optimal_exit_diff_pct, 2),
        # 거래량
        'entry_volume': entry_candle['volume'] if entry_candle else 0,
        'hold_candle_count': len(hold_candles),
    }

    return analysis


def save_cumulative(analyses: List[dict]):
    """누적 데이터 저장 (중복 방지)"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 기존 데이터 로드
    existing_keys = set()
    if CUMULATIVE_FILE.exists():
        with open(CUMULATIVE_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                t = json.loads(line.strip())
                key = f"{t['code']}_{t['buy_time']}"
                existing_keys.add(key)

    # 신규 데이터만 추가
    new_count = 0
    with open(CUMULATIVE_FILE, 'a', encoding='utf-8') as f:
        for a in analyses:
            key = f"{a['code']}_{a['buy_time']}"
            if key not in existing_keys:
                f.write(json.dumps(a, ensure_ascii=False, default=str) + '\n')
                new_count += 1

    logger.info(f"누적 저장: {new_count}건 추가 (기존 {len(existing_keys)}건)")
    return new_count


def print_report(analyses: List[dict], target_date: str):
    """분석 리포트 출력"""
    if not analyses:
        print(f"\n{target_date} 스캘핑 매매 없음")
        return

    print(f"\n{'='*80}")
    print(f"  1분봉 매매 시점 분석 리포트 — {target_date}")
    print(f"{'='*80}")

    wins = [a for a in analyses if a['pnl_pct'] > 0]
    losses = [a for a in analyses if a['pnl_pct'] <= 0]

    for i, a in enumerate(analyses, 1):
        result = "✓ WIN" if a['pnl_pct'] > 0 else "✗ LOSS"
        no_candle = " [캔들없음]" if a.get('no_candle_data') else ""
        print(f"\n── #{i} [{a['code']}] {a['name']} | {result} {a['pnl_pct']:+.2f}% | {a['exit_type']}{no_candle} ──")
        print(f"  매수: {a['buy_time'][11:19]} @ {a['buy_price']:,}원")
        print(f"  매도: {a['sell_time'][11:19]} @ {a['sell_price']:,}원 (홀딩 {a['hold_seconds']}초)")
        print(f"  MFE: +{a['mfe_pct']:.2f}% @ {a['mfe_time']} | MAE: {a['mae_pct']:.2f}% @ {a['mae_time']}")
        print(f"  30분 MFE: +{a['ext_mfe_pct']:.2f}% @ {a['ext_mfe_time']}")
        print(f"  매도 후: 추가상승 +{a['missed_upside_pct']:.2f}% / 추가하락 {a['avoided_downside_pct']:.2f}%")
        print(f"  최적 진입 차이: {a['optimal_entry_diff_pct']:.2f}% | 최적 매도 차이: {a['optimal_exit_diff_pct']:.2f}%")
        if a.get('entry_candle'):
            ec = a['entry_candle']
            print(f"  진입 캔들: O{ec['open']:,} H{ec['high']:,} L{ec['low']:,} C{ec['close']:,} ({ec['body_pct']:+.2f}%) vol:{ec['volume']:,}")
        if a.get('pre_entry_pattern'):
            print(f"  직전 3분봉: {a['pre_entry_pattern']}")

    # 요약 통계
    print(f"\n{'─'*80}")
    print(f"  요약 통계")
    print(f"{'─'*80}")
    print(f"  총 매매: {len(analyses)}건 | 승: {len(wins)}건 | 패: {len(losses)}건 | 승률: {len(wins)/len(analyses)*100:.0f}%")

    if wins:
        avg_win = sum(a['pnl_pct'] for a in wins) / len(wins)
        avg_win_mfe = sum(a['mfe_pct'] for a in wins) / len(wins)
        avg_win_ext_mfe = sum(a['ext_mfe_pct'] for a in wins) / len(wins)
        avg_win_missed = sum(a['missed_upside_pct'] for a in wins) / len(wins)
        print(f"  수익 평균: {avg_win:+.2f}% | MFE: +{avg_win_mfe:.2f}% | 30분MFE: +{avg_win_ext_mfe:.2f}% | 놓친 상승: +{avg_win_missed:.2f}%")

    if losses:
        avg_loss = sum(a['pnl_pct'] for a in losses) / len(losses)
        avg_loss_mae = sum(a['mae_pct'] for a in losses) / len(losses)
        avg_loss_mfe = sum(a['mfe_pct'] for a in losses) / len(losses)
        print(f"  손실 평균: {avg_loss:+.2f}% | MAE: {avg_loss_mae:.2f}% | MFE: +{avg_loss_mfe:.2f}% (진입 후 최고)")

    avg_hold = sum(a['hold_seconds'] for a in analyses) / len(analyses)
    avg_optimal_entry = sum(a['optimal_entry_diff_pct'] for a in analyses) / len(analyses)
    avg_optimal_exit = sum(a['optimal_exit_diff_pct'] for a in analyses) / len(analyses)
    print(f"  평균 홀딩: {avg_hold:.0f}초 | 진입 슬리피지: {avg_optimal_entry:.2f}% | 매도 놓친 폭: {avg_optimal_exit:.2f}%")

    # 전략 인사이트
    print(f"\n{'─'*80}")
    print(f"  전략 인사이트")
    print(f"{'─'*80}")

    # 손실 매매에서 MFE > 0인 경우 = 한때 수익이었다가 손절
    loss_had_profit = [a for a in losses if a['mfe_pct'] > 0.5]
    if loss_had_profit:
        print(f"  ⚠ 손실 {len(losses)}건 중 {len(loss_had_profit)}건은 한때 +0.5% 이상 수익 → 트레일링 개선 여지")

    # 수익 매매에서 30분 MFE가 실현 수익보다 훨씬 큰 경우
    win_left_money = [a for a in wins if a['ext_mfe_pct'] > a['pnl_pct'] * 2]
    if win_left_money:
        print(f"  ⚠ 수익 {len(wins)}건 중 {len(win_left_money)}건은 30분 MFE가 실현의 2배 → 보유시간 연장 검토")

    # 매도 후 추가 상승이 큰 경우
    early_exits = [a for a in wins if a['missed_upside_pct'] > 1.0]
    if early_exits:
        print(f"  ⚠ 수익 {len(wins)}건 중 {len(early_exits)}건은 매도 후 +1% 추가 상승 → 조기 청산 의심")

    print(f"{'='*80}\n")


def print_cumulative_summary():
    """누적 데이터 전체 요약"""
    if not CUMULATIVE_FILE.exists():
        print("누적 데이터 없음")
        return

    all_trades = []
    with open(CUMULATIVE_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            all_trades.append(json.loads(line.strip()))

    if not all_trades:
        print("누적 데이터 없음")
        return

    dates = sorted(set(a['date'] for a in all_trades))
    wins = [a for a in all_trades if a['pnl_pct'] > 0]
    losses = [a for a in all_trades if a['pnl_pct'] <= 0]

    print(f"\n{'='*80}")
    print(f"  누적 1분봉 분석 요약 ({dates[0]} ~ {dates[-1]})")
    print(f"{'='*80}")
    print(f"  총 {len(all_trades)}건 | {len(dates)}일 | 승률 {len(wins)/len(all_trades)*100:.0f}%")

    if wins:
        print(f"  수익: avg {sum(a['pnl_pct'] for a in wins)/len(wins):+.2f}% | MFE avg +{sum(a['mfe_pct'] for a in wins)/len(wins):.2f}%")
    if losses:
        print(f"  손실: avg {sum(a['pnl_pct'] for a in losses)/len(losses):+.2f}% | MAE avg {sum(a['mae_pct'] for a in losses)/len(losses):.2f}%")

    # 청산유형별 통계
    by_exit = {}
    for a in all_trades:
        et = a['exit_type']
        by_exit.setdefault(et, []).append(a)

    print(f"\n  청산유형별:")
    for et, trades in sorted(by_exit.items(), key=lambda x: -len(x[1])):
        avg_pnl = sum(t['pnl_pct'] for t in trades) / len(trades)
        win_rate = len([t for t in trades if t['pnl_pct'] > 0]) / len(trades) * 100
        print(f"    {et:20s}: {len(trades):3d}건 | avg {avg_pnl:+.2f}% | 승률 {win_rate:.0f}%")

    # 시간대별 통계
    print(f"\n  시간대별:")
    by_hour = {}
    for a in all_trades:
        h = a['buy_time'][11:13]
        by_hour.setdefault(h, []).append(a)
    for h, trades in sorted(by_hour.items()):
        avg_pnl = sum(t['pnl_pct'] for t in trades) / len(trades)
        win_rate = len([t for t in trades if t['pnl_pct'] > 0]) / len(trades) * 100
        print(f"    {h}시: {len(trades):3d}건 | avg {avg_pnl:+.2f}% | 승률 {win_rate:.0f}%")

    print(f"{'='*80}\n")


async def main():
    import argparse
    parser = argparse.ArgumentParser(description='매매 시점 1분봉 분석')
    parser.add_argument('--date', type=str, default=None,
                        help='분석 대상 날짜 (YYYYMMDD)')
    parser.add_argument('--all', action='store_true',
                        help='누적 데이터 전체 요약만 출력')
    parser.add_argument('--no-api', action='store_true',
                        help='API 호출 없이 기존 누적 데이터만 분석')
    args = parser.parse_args()

    if args.all:
        print_cumulative_summary()
        return

    target_date = args.date or datetime.now().strftime('%Y%m%d')
    target_date = target_date.replace('-', '')

    logger.info(f"매매 분석 시작: {target_date}")

    # 매매 데이터 로드
    pairs = load_trades(target_date)
    if not pairs:
        logger.info(f"{target_date} 스캘핑 매매 없음")
        return

    logger.info(f"{target_date} 스캘핑 매매 {len(pairs)}건 발견")

    if args.no_api:
        print_cumulative_summary()
        return

    # 키움 API 초기화
    config = load_config()
    api = KiwoomRestAPI(config)
    await api.initialize()

    # 종목별 1분봉 수집 + 분석
    analyses = []
    codes_done = set()

    # 종목별 캔들 캐싱 (동일 종목 여러 매매 시 재사용)
    candle_cache = {}

    for pair in pairs:
        code = pair['code']
        if code not in candle_cache:
            logger.info(f"[{code}] {pair['name']} 1분봉 수집 중...")
            candles = await fetch_candles(api, code, target_date)
            candle_cache[code] = candles
            logger.info(f"[{code}] {len(candles)}봉 수집 완료")
            await asyncio.sleep(3)  # rate limit (모의투자 API 공유 방지)

        result = analyze_trade_with_candles(pair, candle_cache[code])
        analyses.append(result)

    # 누적 저장
    save_cumulative(analyses)

    # 리포트 출력
    print_report(analyses, target_date)

    # 일별 리포트 파일 저장
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_file = REPORT_DIR / f'candle_analysis_{target_date}.json'
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(analyses, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"리포트 저장: {report_file}")


if __name__ == '__main__':
    asyncio.run(main())
