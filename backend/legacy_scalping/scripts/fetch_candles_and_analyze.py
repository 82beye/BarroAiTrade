"""
매매 종목 1분봉 조회 + 매수/매도 타점 vs 시그널 수익률 상세 비교

사용법:
    python scripts/fetch_candles_and_analyze.py              # 4/6(최근 거래일)
    python scripts/fetch_candles_and_analyze.py 20260406     # 특정일
"""
import asyncio
import json
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml
from dotenv import load_dotenv

from execution.kiwoom_api import KiwoomRestAPI


def load_config():
    env_path = PROJECT_ROOT / "config" / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    with open(PROJECT_ROOT / "config" / "settings.yaml", 'r') as f:
        return yaml.safe_load(f)


def load_local_trades(date_str: str):
    """trades.jsonl 에서 해당일 매매 로드"""
    formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    trades = []
    path = PROJECT_ROOT / "logs" / "trades.jsonl"
    if not path.exists():
        return trades
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            t = json.loads(line)
            if t.get('timestamp', '').startswith(formatted):
                trades.append(t)
    return trades


def load_scalping_report(date_str: str):
    formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    path = PROJECT_ROOT / "logs" / "scalping_reports" / f"scalping_{formatted}.json"
    if path.exists():
        with open(path, 'r') as f:
            return json.load(f)
    return None


def extract_hhmm(candle_time: str, date_str: str = "") -> str:
    """분봉 시간에서 HHMM 추출. 포맷: YYYYMMDDHHmmSS / HHmmSS / HH:MM:SS"""
    ct = candle_time.strip()
    if len(ct) == 14:  # YYYYMMDDHHmmSS
        return ct[8:12]  # HHmm
    elif len(ct) == 8 and ':' in ct:  # HH:MM:SS
        return ct.replace(':', '')[:4]
    elif len(ct) >= 6 and ct.isdigit():  # HHmmSS
        return ct[:4]
    elif len(ct) >= 4 and ct[:4].isdigit():
        return ct[:4]
    return ""


def filter_candles_by_date(candles, date_str):
    """해당 날짜의 봉만 필터"""
    filtered = []
    for c in candles:
        ct = c.get('time', '')
        if len(ct) == 14 and ct[:8] == date_str:
            filtered.append(c)
        elif len(ct) <= 8:
            # 날짜 없는 시간만 있는 경우 모두 포함
            filtered.append(c)
    return filtered


def find_candle_at_time(candles, target_time_str, date_str=""):
    """target_time_str(HH:MM:SS) 시점의 1분봉 찾기"""
    if not candles or not target_time_str:
        return None
    target_hhmm = target_time_str.replace(':', '')[:4]

    best = None
    best_diff = 99999
    for c in candles:
        ct_hhmm = extract_hhmm(c.get('time', ''), date_str)
        if not ct_hhmm or not ct_hhmm.isdigit():
            continue
        diff = abs(int(ct_hhmm) - int(target_hhmm))
        if diff < best_diff:
            best_diff = diff
            best = c
        if diff == 0:
            return c
    return best if best_diff <= 1 else None


def to_minutes(hhmm: str) -> int:
    """HHMM -> 분"""
    h = int(hhmm[:2])
    m = int(hhmm[2:4])
    return h * 60 + m


def find_optimal_in_window(candles, start_time, minutes_after, direction='buy', date_str=""):
    """
    start_time 부터 minutes_after 분 동안의 최적 타점 찾기
    direction='buy': 최저가 / 'sell': 최고가
    """
    if not candles or not start_time:
        return None

    start_hhmm = start_time.replace(':', '')[:4]
    if not start_hhmm.isdigit():
        return None

    start_min = to_minutes(start_hhmm)
    end_min = start_min + minutes_after

    window = []
    for c in candles:
        ct_hhmm = extract_hhmm(c.get('time', ''), date_str)
        if not ct_hhmm or not ct_hhmm.isdigit():
            continue
        ct_min = to_minutes(ct_hhmm)
        if start_min <= ct_min <= end_min:
            window.append(c)

    if not window:
        return None

    if direction == 'buy':
        return min(window, key=lambda c: c.get('low', 999999999))
    else:
        return max(window, key=lambda c: c.get('high', 0))


async def main():
    date_str = "20260406"
    if len(sys.argv) > 1:
        date_str = sys.argv[1]

    formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    print(f"=== 매매 종목 1분봉 분석 ({formatted}) ===\n")

    config = load_config()
    api = KiwoomRestAPI(config)

    try:
        await api.initialize()

        # 1. 로컬 매매 기록 로드
        trades = load_local_trades(date_str)
        report = load_scalping_report(date_str)

        if not trades:
            print(f"[ERROR] {formatted} 매매 기록 없음")
            return

        # 매매 종목 추출
        traded_codes = {}
        for t in trades:
            code = t['code']
            if code not in traded_codes:
                traded_codes[code] = t.get('name', code)

        print(f"매매 종목 수: {len(traded_codes)}개")
        print(f"매매 건수: {len(trades)}건\n")

        # 2. 1분봉 데이터 조회 (종목별)
        candle_data = {}
        save_dir = PROJECT_ROOT / "logs" / "candles"
        save_dir.mkdir(exist_ok=True)

        for code, name in traded_codes.items():
            # 이미 저장된 파일이 있으면 로드
            cache_path = save_dir / f"candle_1m_{code}_{date_str}.json"
            if cache_path.exists():
                print(f"  1분봉 캐시: [{code}] {name} ...", end=" ")
                with open(cache_path, 'r') as f:
                    cached = json.load(f)
                candles = cached.get('candles', [])
                day_candles = filter_candles_by_date(candles, date_str)
                candle_data[code] = day_candles if day_candles else candles
                print(f"{len(candles)}봉 (당일 {len(candle_data[code])}봉)")
                continue

            print(f"  1분봉 조회: [{code}] {name} ...", end=" ")
            try:
                candles = await api.get_intraday_chart(
                    code, tick_scope=1, count=400, base_dt=date_str)
                if candles:
                    # 시간순 정렬
                    candles.sort(key=lambda c: c.get('time', ''))
                    # 해당일만 필터
                    day_candles = filter_candles_by_date(candles, date_str)
                    candle_data[code] = day_candles if day_candles else candles
                    dc = candle_data[code]
                    print(f"{len(candles)}봉 (당일 {len(dc)}봉)")

                    # 파일 저장
                    save_path = save_dir / f"candle_1m_{code}_{date_str}.json"
                    with open(save_path, 'w') as f:
                        json.dump({
                            'code': code, 'name': name, 'date': formatted,
                            'tick_scope': 1, 'count': len(candles),
                            'candles': candles
                        }, f, ensure_ascii=False, indent=2)
                else:
                    print("데이터 없음")
            except Exception as e:
                print(f"실패: {e}")
            await asyncio.sleep(0.3)  # rate limit

        print(f"\n1분봉 저장 경로: {save_dir}/\n")

        # 3. 매수/매도 타점 분석
        # trades.jsonl에서 매수/매도 페어링
        buy_trades = [t for t in trades if t['action'] == 'BUY']
        sell_trades = [t for t in trades if t['action'] == 'SELL']

        # report의 stock_details에서 매칭된 페어 가져오기
        pairs = []
        if report and 'stock_details' in report:
            for stock in report['stock_details']:
                for trade in stock.get('trades', []):
                    pairs.append(trade)

        if not pairs:
            # report 없으면 직접 페어링
            sell_by_code = defaultdict(list)
            for s in sell_trades:
                sell_by_code[s['code']].append(s)
            for b in buy_trades:
                code = b['code']
                if sell_by_code[code]:
                    s = sell_by_code[code].pop(0)
                    pairs.append({
                        'code': code,
                        'name': b.get('name', ''),
                        'buy_time': b['timestamp'],
                        'sell_time': s['timestamp'],
                        'buy_price': b['price'],
                        'sell_price': s['price'],
                        'qty': b.get('qty', 0),
                        'signal_price': b.get('signal_price', b['price']),
                        'score': b.get('score', 0),
                        'net_pnl_pct': 0,
                        'net_amount': 0,
                        'exit_type': s.get('exit_type', ''),
                    })

        print("=" * 140)
        print(f"{'#':>2} | {'종목':>10} | {'매수시간':>8} | {'시그널가':>8} | "
              f"{'체결가':>8} | {'슬립':>6} | "
              f"{'1분봉저가':>8} | {'최적매수':>8} | "
              f"{'매도시간':>8} | {'매도가':>8} | "
              f"{'1분봉고가':>8} | {'최적매도':>8} | "
              f"{'실현%':>6} | {'최적%':>6} | {'기회손실%':>7} | {'결과':>6}")
        print("-" * 140)

        total_actual_pnl = 0
        total_optimal_pnl = 0
        total_slip_cost = 0

        for i, p in enumerate(pairs, 1):
            code = p['code']
            name = (p.get('name', '') or code)[:6]
            candles = candle_data.get(code, [])

            # 매수 시간
            buy_ts = p.get('buy_time', '')
            if 'T' in buy_ts:
                buy_time = buy_ts.split('T')[1][:8]
            else:
                buy_time = buy_ts.split(' ')[1][:8] if ' ' in buy_ts else ''

            # 매도 시간
            sell_ts = p.get('sell_time', '')
            if 'T' in sell_ts:
                sell_time = sell_ts.split('T')[1][:8]
            else:
                sell_time = sell_ts.split(' ')[1][:8] if ' ' in sell_ts else ''

            buy_price = p.get('buy_price', 0)
            sell_price = p.get('sell_price', 0)
            signal_price = p.get('signal_price', buy_price)
            qty = p.get('qty', 0)

            # 슬리피지
            slip_pct = ((buy_price - signal_price) / signal_price * 100
                        if signal_price > 0 else 0)

            # 1분봉에서 매수 시점 봉 찾기
            buy_candle = find_candle_at_time(candles, buy_time, date_str)
            buy_candle_low = buy_candle['low'] if buy_candle else 0

            # 매수 시점 전후 3분 최적 매수 (최저가)
            optimal_buy_candle = find_optimal_in_window(
                candles, buy_time, 3, direction='buy', date_str=date_str)
            optimal_buy = optimal_buy_candle['low'] if optimal_buy_candle else 0

            # 1분봉에서 매도 시점 봉 찾기
            sell_candle = find_candle_at_time(candles, sell_time, date_str)
            sell_candle_high = sell_candle['high'] if sell_candle else 0

            # 매수 시점 ~ 매도 시점 사이 최적 매도 (최고가)
            hold_minutes = p.get('hold_minutes', 10)
            optimal_sell_candle = find_optimal_in_window(
                candles, buy_time, max(int(hold_minutes) + 2, 5),
                direction='sell', date_str=date_str)
            optimal_sell = optimal_sell_candle['high'] if optimal_sell_candle else 0

            # 실현 수익률
            actual_pnl_pct = ((sell_price - buy_price) / buy_price * 100
                              if buy_price > 0 else 0)

            # 최적 수익률 (최저 매수 → 최고 매도)
            if optimal_buy > 0 and optimal_sell > 0:
                optimal_pnl_pct = (optimal_sell - optimal_buy) / optimal_buy * 100
            else:
                optimal_pnl_pct = 0

            # 기회손실 = 최적 - 실현
            opp_loss = optimal_pnl_pct - actual_pnl_pct

            # 슬리피지 비용 (원)
            slip_cost = (buy_price - signal_price) * qty if qty > 0 else 0

            result = "익절" if actual_pnl_pct > 0 else ("본전" if actual_pnl_pct == 0 else "손절")
            exit_type = p.get('exit_type', '')
            if '익절' in exit_type:
                result = '익절'
            elif '손절' in exit_type:
                result = '손절'
            elif '트레일링' in exit_type:
                result = '트레일'
            elif '시간초과' in exit_type:
                result = '시간초과'

            total_actual_pnl += actual_pnl_pct
            total_optimal_pnl += optimal_pnl_pct
            total_slip_cost += slip_cost

            print(f"{i:>2} | {name:>10} | {buy_time[:5]:>8} | "
                  f"{signal_price:>8,} | {buy_price:>8,} | {slip_pct:>+5.1f}% | "
                  f"{buy_candle_low:>8,} | {optimal_buy:>8,} | "
                  f"{sell_time[:5]:>8} | {sell_price:>8,} | "
                  f"{sell_candle_high:>8,} | {optimal_sell:>8,} | "
                  f"{actual_pnl_pct:>+5.1f}% | {optimal_pnl_pct:>+5.1f}% | "
                  f"{opp_loss:>+6.1f}% | {result:>6}")

        print("-" * 140)
        n = len(pairs)
        print(f"\n{'항목':>20} | {'값':>10}")
        print("-" * 40)
        print(f"{'총 매매 건수':>20} | {n:>10}건")
        print(f"{'평균 실현 수익률':>20} | {total_actual_pnl/n:>+9.2f}%")
        print(f"{'평균 최적 수익률':>20} | {total_optimal_pnl/n:>+9.2f}%")
        print(f"{'평균 기회손실':>20} | {(total_optimal_pnl-total_actual_pnl)/n:>+9.2f}%")
        print(f"{'총 슬리피지 비용':>20} | {total_slip_cost:>+9,}원")

        # 4. 종목별 상세 1분봉 타점 분석
        print(f"\n\n{'='*100}")
        print("종목별 1분봉 상세 타점 분석")
        print(f"{'='*100}")

        for p in pairs:
            code = p['code']
            name = p.get('name', code)
            candles = candle_data.get(code, [])
            if not candles:
                continue

            buy_ts = p.get('buy_time', '')
            sell_ts = p.get('sell_time', '')
            if 'T' in buy_ts:
                buy_time = buy_ts.split('T')[1][:8]
            else:
                buy_time = buy_ts.split(' ')[1][:8] if ' ' in buy_ts else ''
            if 'T' in sell_ts:
                sell_time = sell_ts.split('T')[1][:8]
            else:
                sell_time = sell_ts.split(' ')[1][:8] if ' ' in sell_ts else ''

            buy_hhmm = buy_time.replace(':', '')[:4]
            sell_hhmm = sell_time.replace(':', '')[:4]

            buy_price = p.get('buy_price', 0)
            sell_price = p.get('sell_price', 0)
            signal_price = p.get('signal_price', buy_price)

            # 매수 전후 5분 봉 출력
            print(f"\n[{code}] {name} | 매수 {buy_time[:5]} @ {buy_price:,} "
                  f"(시그널 {signal_price:,}) → 매도 {sell_time[:5]} @ {sell_price:,}")
            print(f"  시간   | 시가     | 고가     | 저가     | 종가     | 거래량    | 비고")
            print(f"  {'-'*80}")

            try:
                buy_int = int(buy_hhmm)
                sell_int = int(sell_hhmm)
            except ValueError:
                continue

            # 매수 3분 전 ~ 매도 2분 후 범위
            bh, bm = divmod(buy_int, 100)
            show_start = (bh * 60 + bm - 3)
            sh, sm = divmod(sell_int, 100)
            show_end = (sh * 60 + sm + 2)

            for c in candles:
                ct_hhmm = extract_hhmm(c.get('time', ''), date_str)
                if not ct_hhmm or not ct_hhmm.isdigit():
                    continue
                ct_min = to_minutes(ct_hhmm)

                if show_start <= ct_min <= show_end:
                    marker = ""
                    if ct_hhmm == buy_hhmm:
                        marker = "◀ 매수"
                    elif ct_hhmm == sell_hhmm:
                        marker = "◀ 매도"

                    low = c.get('low', 0)
                    high = c.get('high', 0)
                    if low > 0 and low < buy_price and "매수" in marker:
                        marker += f" (봉저가 {low:,} < 체결가)"
                    if high > 0 and high > sell_price and "매도" in marker:
                        marker += f" (봉고가 {high:,} > 매도가)"

                    ct_fmt = f"{ct_hhmm[:2]}:{ct_hhmm[2:]}"
                    print(f"  {ct_fmt:>6} | {c.get('open',0):>8,} | "
                          f"{c.get('high',0):>8,} | {c.get('low',0):>8,} | "
                          f"{c.get('price',0):>8,} | {c.get('volume',0):>8,} | {marker}")

    finally:
        await api.close()


if __name__ == '__main__':
    asyncio.run(main())
