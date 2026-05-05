"""
스캘핑 전략 시뮬레이션 — 캔들 데이터 기반

캐시된 OHLCV 데이터에서 가장 최근 거래일의 상승 종목을 추출하여
ScalpingCoordinator(10 에이전트)를 통해 매수 시그널과 TP/SL을 산출한다.

Usage:
    python scripts/scalping_simulation.py
    python scripts/scalping_simulation.py --date 20260330
    python scripts/scalping_simulation.py --top 30
"""

import sys
import os
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime, time
from unittest.mock import patch

# 프로젝트 루트 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / "config" / ".env")

from strategy.scalping_team.coordinator import ScalpingCoordinator
from strategy.scalping_team.base_agent import StockSnapshot
from main import load_config

logging.basicConfig(
    level=logging.WARNING,
    format='%(message)s',
)
logger = logging.getLogger(__name__)


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


def find_surge_stocks(all_ohlcv: dict, target_date: str, min_change: float = 5.0,
                      max_change: float = 30.0) -> list:
    """대상 날짜에 상승률 기준 충족 종목 추출"""
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

        if prev_close <= 0:
            continue

        change_pct = (cur_close - prev_close) / prev_close * 100
        if change_pct < min_change or change_pct > max_change:
            continue

        # 20일 평균 거래량
        start_idx = max(0, idx - 20)
        avg_vol_20 = df.iloc[start_idx:idx]['volume'].mean()
        vol_ratio = cur_volume / avg_vol_20 if avg_vol_20 > 0 else 0

        # 거래대금
        trade_value = cur_close * cur_volume

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

    # 상승률 내림차순 정렬
    candidates.sort(key=lambda x: x['change_pct'], reverse=True)
    return candidates


def get_stock_names(config: dict) -> dict:
    """종목 코드 → 이름 매핑 (API 조회 후 로컬 캐시)"""
    cache_path = Path(config.get('scanner', {}).get(
        'cache_dir', './data/ohlcv_cache')).parent / 'stock_names.json'

    # 캐시 파일이 최근 7일 이내면 재사용
    if cache_path.exists():
        import time as _time
        age_days = (_time.time() - cache_path.stat().st_mtime) / 86400
        if age_days < 7:
            try:
                with open(cache_path) as f:
                    return json.load(f)
            except Exception:
                pass

    # API로 종목명 조회
    try:
        import asyncio
        from execution.kiwoom_api import KiwoomRestAPI

        async def _fetch():
            api = KiwoomRestAPI(config)
            await api.initialize()
            names = {}
            for market in ["0", "10"]:  # KOSPI, KOSDAQ
                stocks = await api.get_stock_list_with_meta(market)
                for s in stocks:
                    code = s.get('code', '')
                    name = s.get('name', '')
                    if code and name:
                        names[code] = name
            await api.close()
            return names

        names = asyncio.run(_fetch())
        # 캐시 저장
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(names, f, ensure_ascii=False)
        print(f"    → 종목명 {len(names)}개 로드 (API 조회 후 캐시)")
        return names
    except Exception as e:
        print(f"    → 종목명 조회 실패: {e}")
        return {}


def simulate_scalping_time(coordinator, snapshots, cache_data, sim_time: time):
    """특정 시간대에서의 스캘핑 분석 시뮬레이션"""
    # GoldenTimeAgent 등 시간 의존 에이전트를 위해 현재 시간 오버라이드
    # coordinator.analyze()는 datetime.now()를 사용하므로,
    # 여기서는 결과를 직접 해석

    results = coordinator.analyze(
        snapshots,
        cache_data=cache_data,
        intraday_data={},
    )
    return results


def main():
    parser = argparse.ArgumentParser(description='스캘핑 시뮬레이션')
    parser.add_argument('--date', type=str, default=None,
                        help='대상 날짜 (YYYYMMDD, 기본: 최근 거래일)')
    parser.add_argument('--top', type=int, default=30,
                        help='분석 종목 수 (기본: 30)')
    parser.add_argument('--min-change', type=float, default=5.0,
                        help='최소 상승률 (기본: 5.0%%)')
    parser.add_argument('--max-change', type=float, default=30.0,
                        help='최대 상승률 (기본: 30.0%%)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='상세 로그 출력')
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    config = load_config('simulation')
    cache_dir = config.get('scanner', {}).get('cache_dir', './data/ohlcv_cache')

    print("=" * 80)
    print("스캘핑 전략 시뮬레이션 — 캔들 데이터 기반")
    print("=" * 80)

    # 1. OHLCV 캐시 로드
    print("\n[1] OHLCV 캐시 로드 중...")
    all_ohlcv = load_ohlcv_cache(cache_dir)
    print(f"    → {len(all_ohlcv)}종목 로드 완료")

    # 2. 대상 날짜 결정
    if args.date:
        target_date = args.date
    else:
        # 가장 최근 공통 날짜 찾기
        all_dates = set()
        for code, df in list(all_ohlcv.items())[:100]:
            if 'date' in df.columns:
                all_dates.update(df['date'].tolist())
        if all_dates:
            target_date = sorted(all_dates)[-1]
        else:
            print("ERROR: 날짜 데이터 없음")
            return

    print(f"    → 대상 날짜: {target_date}")

    # 3. 상승 종목 추출
    print(f"\n[2] 상승 종목 추출 (등락률 +{args.min_change}% ~ +{args.max_change}%)...")
    candidates = find_surge_stocks(
        all_ohlcv, target_date, args.min_change, args.max_change)
    print(f"    → {len(candidates)}종목 발견")

    if not candidates:
        print("\n상승 종목이 없습니다.")
        return

    # 종목명 매핑
    stock_names = get_stock_names(config)

    # 상위 N개만 분석
    top_candidates = candidates[:args.top]

    # 4. StockSnapshot 생성
    print(f"\n[3] StockSnapshot 생성 (상위 {len(top_candidates)}종목)...")
    snapshots = []
    ohlcv_cache = {}

    for c in top_candidates:
        code = c['code']
        name = stock_names.get(code, code)
        snap = StockSnapshot(
            code=code,
            name=name,
            price=c['close'],       # 종가를 현재가로 사용
            open=c['open'],
            high=c['high'],
            low=c['low'],
            prev_close=c['prev_close'],
            volume=c['volume'],
            change_pct=c['change_pct'],
            trade_value=c['trade_value'],
            volume_ratio=c['volume_ratio'],
            category='급등주' if c['change_pct'] >= 15 else '강세주',
            score=c['change_pct'] * 3,  # 임시 점수
        )
        snapshots.append(snap)
        if code in all_ohlcv:
            ohlcv_cache[code] = all_ohlcv[code]

    # 5. 스캘핑 코디네이터 분석 (시간대별)
    sim_times = [
        ("09:15 골든타임", datetime(2026, 3, 30, 9, 15, 0)),
        ("10:00 오전장",   datetime(2026, 3, 30, 10, 0, 0)),
        ("13:30 오후장",   datetime(2026, 3, 30, 13, 30, 0)),
    ]

    # 대상 날짜에 맞게 시뮬 시간 설정
    year = int(target_date[:4])
    month = int(target_date[4:6])
    day = int(target_date[6:8])
    sim_times = [
        ("09:15 골든타임", datetime(year, month, day, 9, 15, 0)),
        ("10:00 오전장",   datetime(year, month, day, 10, 0, 0)),
        ("13:30 오후장",   datetime(year, month, day, 13, 30, 0)),
    ]

    all_results = {}
    for label, sim_dt in sim_times:
        print(f"\n[4-{label}] 스캘핑 10 에이전트 분석 (시간대: {label})...")
        print("-" * 80)

        coordinator = ScalpingCoordinator(config)

        with patch('strategy.scalping_team.coordinator.datetime') as mock_dt:
            mock_dt.now.return_value = sim_dt
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            mock_dt.strptime = datetime.strptime
            results = coordinator.analyze(
                snapshots,
                cache_data=ohlcv_cache,
                intraday_data={},
            )
        all_results[label] = results

    # 가장 결과가 많은 시간대 선택
    best_label = max(all_results, key=lambda k: len(all_results[k]))
    results = all_results[best_label]
    print(f"\n    → 최적 시간대: {best_label} ({len(results)}종목 통과)")

    # 6. 결과 출력
    print("\n" + "=" * 80)
    print(f"분석 결과 — {target_date}")
    print("=" * 80)

    # 전체 상승 종목 요약
    print(f"\n▶ 상승 종목 ({len(candidates)}종목 발견, 상위 {len(top_candidates)}종목 분석)")
    print(f"{'순위':>4} {'종목코드':<8} {'종목명':<12} {'등락률':>7} "
          f"{'거래대금':>12} {'거래량배수':>8}")
    print("-" * 60)
    for i, c in enumerate(top_candidates[:15], 1):
        name = stock_names.get(c['code'], c['code'])
        tv_억 = c['trade_value'] / 100_000_000
        print(f"  {i:>2}. {c['code']:<8} {name:<12} "
              f"+{c['change_pct']:>5.1f}% "
              f"{tv_억:>10.0f}억 "
              f"x{c['volume_ratio']:>5.1f}")

    # 에이전트 분석 결과
    if not results:
        print("\n▶ 스캘핑 시그널: 없음 (모든 종목 필터링됨)")
    else:
        # 타이밍별 분류
        buy_signals = [r for r in results if r.timing == '즉시']
        wait_signals = [r for r in results if r.timing in ('대기', '눌림목대기')]
        avoid_signals = [r for r in results if r.timing == '관망']

        print(f"\n▶ 스캘핑 에이전트 분석 결과 ({len(results)}종목)")
        print(f"  즉시 매수: {len(buy_signals)}종목 | "
              f"대기: {len(wait_signals)}종목 | "
              f"관망: {len(avoid_signals)}종목")

        if buy_signals:
            print(f"\n{'':>2}{'순위':>4} {'종목코드':<8} {'종목명':<12} {'점수':>5} "
                  f"{'타이밍':<6} {'합의':<8} {'TP':>6} {'SL':>6} "
                  f"{'홀딩':>5} {'유형':<8}")
            print("  " + "-" * 85)
            for r in buy_signals:
                name = stock_names.get(r.code, r.code)
                surge = {'gap_up': '갭상승', 'intraday': '장중급등',
                         'mixed': '혼합', 'unknown': '미분류'}.get(
                    getattr(r, 'surge_type', 'unknown'), '미분류')
                print(f"  {'★':>2} {r.rank:>2}. {r.code:<8} {name:<12} "
                      f"{r.total_score:>5.0f} "
                      f"{r.timing:<6} {r.consensus_level:<8} "
                      f"+{r.scalp_tp_pct:>4.1f}% "
                      f"{r.scalp_sl_pct:>5.1f}% "
                      f"{r.hold_minutes:>3.0f}분 "
                      f"{surge:<8}")

        if wait_signals:
            print(f"\n  ── 대기/눌림목대기 종목 ──")
            for r in wait_signals:
                name = stock_names.get(r.code, r.code)
                print(f"     {r.rank:>2}. {r.code:<8} {name:<12} "
                      f"{r.total_score:>5.0f} "
                      f"{r.timing:<10} {r.consensus_level:<8} "
                      f"TP +{r.scalp_tp_pct:.1f}% SL {r.scalp_sl_pct:.1f}%")

        if avoid_signals:
            print(f"\n  ── 관망 종목 ({len(avoid_signals)}종목) ──")
            for r in avoid_signals[:5]:
                name = stock_names.get(r.code, r.code)
                print(f"     {r.rank:>2}. {r.code:<8} {name:<12} "
                      f"{r.total_score:>5.0f} {r.timing:<6} "
                      f"{r.consensus_level:<8}")

        # 상세 분석 (매수 시그널 종목)
        if buy_signals:
            print(f"\n{'='*80}")
            print("▶ 매수 시그널 종목 상세 분석")
            print("=" * 80)
            for r in buy_signals:
                name = stock_names.get(r.code, r.code)
                snap = r.snapshot
                print(f"\n  [{r.code}] {name}")
                print(f"    현재가: {snap.price:,}원 | "
                      f"시가: {snap.open:,}원 | "
                      f"고가: {snap.high:,}원 | "
                      f"저가: {snap.low:,}원")
                print(f"    등락률: +{snap.change_pct:.1f}% | "
                      f"거래량: {snap.volume:,} | "
                      f"거래대금: {snap.trade_value/1e8:,.0f}억원")
                print(f"    ────────────────────────────────────────")
                print(f"    종합점수: {r.total_score:.0f}/100 | "
                      f"신뢰도: {r.confidence:.0%} | "
                      f"합의: {r.consensus_level}")
                print(f"    TP: +{r.scalp_tp_pct:.1f}% | "
                      f"SL: {r.scalp_sl_pct:.1f}% | "
                      f"홀딩: {r.hold_minutes:.0f}분")

                # 에이전트별 의견
                if hasattr(r, 'agent_signals') and r.agent_signals:
                    print(f"    ── 에이전트별 분석 ──")
                    for agent_name, sig in r.agent_signals.items():
                        weight = ScalpingCoordinator.AGENT_WEIGHTS.get(agent_name, 0)
                        print(f"      {agent_name} (w={weight:.0%}): "
                              f"점수 {sig.entry_score:.0f} | "
                              f"{sig.timing} | "
                              f"TP +{sig.scalp_tp_pct:.1f}% SL {sig.scalp_sl_pct:.1f}%")
                        if sig.reasons:
                            for reason in sig.reasons[:2]:
                                print(f"        → {reason}")

    # 7. 투자 시뮬레이션 요약
    if results and buy_signals:
        scalp_cfg = config.get('strategy', {}).get('scalping', {})
        total_equity = 50_000_000  # 5천만원 기준
        max_per_stock = scalp_cfg.get('max_per_stock_pct', 5.0) / 100
        initial_ratio = scalp_cfg.get('initial_entry_ratio', 0.6)

        print(f"\n{'='*80}")
        print(f"▶ 투자 시뮬레이션 (총자산 {total_equity/1e4:,.0f}만원 기준)")
        print("=" * 80)
        print(f"{'종목':<16} {'매수가':>10} {'수량':>5} {'매수금액':>12} "
              f"{'TP 목표가':>10} {'SL 손절가':>10} {'TP 수익':>10} {'SL 손실':>10}")
        print("-" * 95)

        total_invested = 0
        for r in buy_signals:
            name = stock_names.get(r.code, r.code)
            price = r.snapshot.price
            max_amount = total_equity * max_per_stock * initial_ratio
            qty = int(max_amount / price) if price > 0 else 0
            if qty <= 0:
                continue
            invested = qty * price
            total_invested += invested

            tp_price = int(price * (1 + r.scalp_tp_pct / 100))
            sl_price = int(price * (1 + r.scalp_sl_pct / 100))
            tp_profit = (tp_price - price) * qty
            sl_loss = (sl_price - price) * qty

            print(f"  {name:<14} {price:>10,} {qty:>5} {invested:>12,} "
                  f"{tp_price:>10,} {sl_price:>10,} "
                  f"+{tp_profit:>9,} {sl_loss:>9,}")

        print(f"\n  총 매수금액: {total_invested:,}원 "
              f"({total_invested/total_equity*100:.1f}%)")

    print(f"\n{'='*80}")
    print("시뮬레이션 완료")
    print("=" * 80)


if __name__ == '__main__':
    main()
