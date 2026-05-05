"""
매매내역 대조 스크립트

키움 REST API 실제 거래 기록 vs 시스템 trades.jsonl 비교 분석
- ka10170: 당일매매일지 (종목별 매수/매도 요약)
- ka10076: 체결 내역 (개별 주문 단위)

사용법:
    python scripts/reconcile_trades.py                   # 최근 거래일 대조
    python scripts/reconcile_trades.py --date 20260327   # 특정 날짜 대조
    python scripts/reconcile_trades.py --range 20260306 20260327  # 기간 대조
"""

import asyncio
import json
import sys
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# 프로젝트 루트 설정
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml
from dotenv import load_dotenv

from execution.kiwoom_api import KiwoomRestAPI


def load_config() -> dict:
    config_path = PROJECT_ROOT / "config" / "settings.yaml"
    env_path = PROJECT_ROOT / "config" / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def load_trades_jsonl(target_date: str) -> list:
    """trades.jsonl에서 특정 날짜의 매매 기록 로드"""
    trades_path = PROJECT_ROOT / "logs" / "trades.jsonl"
    if not trades_path.exists():
        print(f"[ERROR] trades.jsonl 파일 없음: {trades_path}")
        return []

    trades = []
    with open(trades_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            trade = json.loads(line)
            ts = trade.get('timestamp', '')
            if ts.startswith(target_date[:4] + '-' + target_date[4:6] + '-' + target_date[6:8]):
                trades.append(trade)
    return trades


def summarize_system_trades(trades: list) -> dict:
    """시스템 trades.jsonl → 종목별 매수/매도 요약"""
    summary = {}
    for t in trades:
        code = t['code']
        if code not in summary:
            summary[code] = {
                'name': t.get('name', ''),
                'buy_qty': 0, 'buy_amount': 0,
                'sell_qty': 0, 'sell_amount': 0,
                'buy_trades': [], 'sell_trades': [],
            }
        s = summary[code]
        if t['action'] == 'BUY':
            qty = t.get('qty', 0)
            price = t.get('price', 0)
            s['buy_qty'] += qty
            s['buy_amount'] += qty * price
            s['buy_trades'].append(t)
        elif t['action'] == 'SELL':
            qty = t.get('qty', 0)
            price = t.get('price', 0)
            s['sell_qty'] += qty
            s['sell_amount'] += qty * price
            s['sell_trades'].append(t)

    # 평균가 계산
    for code, s in summary.items():
        s['buy_avg_price'] = (
            round(s['buy_amount'] / s['buy_qty'])
            if s['buy_qty'] > 0 else 0
        )
        s['sell_avg_price'] = (
            round(s['sell_amount'] / s['sell_qty'])
            if s['sell_qty'] > 0 else 0
        )

    return summary


def compare_and_report(exchange_journal: list, system_summary: dict,
                       exchange_executions: list, system_trades: list,
                       target_date: str):
    """거래소 기록 vs 시스템 기록 비교 리포트"""

    print("\n" + "=" * 80)
    print(f"  매매내역 대조 리포트 — {target_date}")
    print("=" * 80)

    # ── 1. 종목별 요약 비교 (ka10170 vs trades.jsonl 합산) ──
    print("\n[ 1. 종목별 매매 요약 비교 (거래소 ka10170 vs 시스템) ]")
    print("-" * 80)
    print(f"{'종목':>12} | {'구분':>4} | {'거래소수량':>8} {'거래소평균가':>10} | "
          f"{'시스템수량':>8} {'시스템평균가':>10} | {'차이':>8}")
    print("-" * 80)

    all_codes = set()
    exchange_by_code = {}
    for item in exchange_journal:
        code = item['code']
        exchange_by_code[code] = item
        all_codes.add(code)
    for code in system_summary:
        all_codes.add(code)

    discrepancies = []

    for code in sorted(all_codes):
        ex = exchange_by_code.get(code, {})
        sys_s = system_summary.get(code, {})
        name = ex.get('name') or sys_s.get('name', code)

        # 매수 비교
        ex_buy_qty = ex.get('buy_qty', 0)
        ex_buy_avg = ex.get('buy_avg_price', 0)
        sys_buy_qty = sys_s.get('buy_qty', 0)
        sys_buy_avg = sys_s.get('buy_avg_price', 0)

        buy_qty_diff = ex_buy_qty - sys_buy_qty
        buy_price_diff = ex_buy_avg - sys_buy_avg if (ex_buy_avg and sys_buy_avg) else 0

        if ex_buy_qty or sys_buy_qty:
            flag = ""
            if buy_qty_diff != 0:
                flag = f"수량 {buy_qty_diff:+d}"
                discrepancies.append((code, name, 'BUY', 'qty', buy_qty_diff,
                                      ex_buy_qty, sys_buy_qty))
            elif abs(buy_price_diff) > 5:
                flag = f"가격 {buy_price_diff:+d}"
                discrepancies.append((code, name, 'BUY', 'price', buy_price_diff,
                                      ex_buy_avg, sys_buy_avg))
            else:
                flag = "OK"

            print(f"{name:>12} | {'매수':>4} | {ex_buy_qty:>8,} {ex_buy_avg:>10,} | "
                  f"{sys_buy_qty:>8,} {sys_buy_avg:>10,} | {flag:>8}")

        # 매도 비교
        ex_sell_qty = ex.get('sell_qty', 0)
        ex_sell_avg = ex.get('sell_avg_price', 0)
        sys_sell_qty = sys_s.get('sell_qty', 0)
        sys_sell_avg = sys_s.get('sell_avg_price', 0)

        sell_qty_diff = ex_sell_qty - sys_sell_qty
        sell_price_diff = ex_sell_avg - sys_sell_avg if (ex_sell_avg and sys_sell_avg) else 0

        if ex_sell_qty or sys_sell_qty:
            flag = ""
            if sell_qty_diff != 0:
                flag = f"수량 {sell_qty_diff:+d}"
                discrepancies.append((code, name, 'SELL', 'qty', sell_qty_diff,
                                      ex_sell_qty, sys_sell_qty))
            elif abs(sell_price_diff) > 5:
                flag = f"가격 {sell_price_diff:+d}"
                discrepancies.append((code, name, 'SELL', 'price', sell_price_diff,
                                      ex_sell_avg, sys_sell_avg))
            else:
                flag = "OK"

            print(f"{name:>12} | {'매도':>4} | {ex_sell_qty:>8,} {ex_sell_avg:>10,} | "
                  f"{sys_sell_qty:>8,} {sys_sell_avg:>10,} | {flag:>8}")

    # ── 2. 주문번호 단위 체결 대조 (ka10076 vs trades.jsonl) ──
    print(f"\n[ 2. 주문번호 단위 체결 대조 (거래소 ka10076 vs 시스템) ]")
    print("-" * 80)

    sys_by_order = {}
    for t in system_trades:
        ono = t.get('order_no', '')
        if ono:
            sys_by_order[ono] = t

    ex_by_order = {}
    for e in exchange_executions:
        ono = e.get('order_no', '')
        if ono:
            ex_by_order[ono] = e

    # 거래소에만 있는 주문
    ex_only = set(ex_by_order.keys()) - set(sys_by_order.keys())
    # 시스템에만 있는 주문
    sys_only = set(sys_by_order.keys()) - set(ex_by_order.keys())
    # 공통 주문
    common = set(ex_by_order.keys()) & set(sys_by_order.keys())

    if ex_only:
        print(f"\n  거래소에만 존재하는 주문 ({len(ex_only)}건):")
        for ono in sorted(ex_only):
            e = ex_by_order[ono]
            print(f"    주문#{ono} | {e.get('name', e['code'])} | "
                  f"{e['action']} {e['qty']}주 @ {e['price']:,}원")

    if sys_only:
        print(f"\n  시스템에만 존재하는 주문 ({len(sys_only)}건):")
        for ono in sorted(sys_only):
            t = sys_by_order[ono]
            print(f"    주문#{ono} | {t.get('name', t['code'])} | "
                  f"{t['action']} {t.get('qty', 0)}주 @ {t.get('price', 0):,}원")

    # 공통 주문 가격/수량 불일치
    price_mismatches = []
    for ono in sorted(common):
        e = ex_by_order[ono]
        t = sys_by_order[ono]

        ex_price = e['price']
        sys_price = t.get('price', 0)
        ex_qty = e['qty']
        sys_qty = t.get('qty', 0)

        if abs(ex_price - sys_price) > 5 or ex_qty != sys_qty:
            price_mismatches.append({
                'order_no': ono,
                'name': e.get('name') or t.get('name', ''),
                'code': e['code'],
                'ex_price': ex_price, 'sys_price': sys_price,
                'ex_qty': ex_qty, 'sys_qty': sys_qty,
                'action': t.get('action', e.get('action', '')),
            })

    if price_mismatches:
        print(f"\n  체결가/수량 불일치 ({len(price_mismatches)}건):")
        for m in price_mismatches:
            parts = []
            if abs(m['ex_price'] - m['sys_price']) > 5:
                parts.append(
                    f"가격 거래소:{m['ex_price']:,} vs 시스템:{m['sys_price']:,} "
                    f"(차이: {m['ex_price'] - m['sys_price']:+,})")
            if m['ex_qty'] != m['sys_qty']:
                parts.append(
                    f"수량 거래소:{m['ex_qty']} vs 시스템:{m['sys_qty']} "
                    f"(차이: {m['ex_qty'] - m['sys_qty']:+d})")
            print(f"    주문#{m['order_no']} | {m['name']} {m['action']} | "
                  f"{' | '.join(parts)}")

    if not ex_only and not sys_only and not price_mismatches:
        print("  모든 주문번호 일치 — 불일치 없음")

    # ── 3. 거래소 손익 vs 시스템 손익 ──
    print(f"\n[ 3. 거래소 손익 vs 시스템 손익 ]")
    print("-" * 80)

    # 거래소 총 손익
    ex_total_pnl = sum(item.get('pnl_amount', 0) for item in exchange_journal)
    ex_total_fee = sum(item.get('commission_tax', 0) for item in exchange_journal)

    # 시스템 일간 손익 (마지막 SELL 기록의 daily_pnl)
    sys_last_pnl = 0.0
    for t in reversed(system_trades):
        if 'daily_pnl' in t:
            sys_last_pnl = t['daily_pnl']
            break

    print(f"  거래소 총손익금액: {ex_total_pnl:>12,}원 (수수료+세금: {ex_total_fee:,}원)")
    print(f"  시스템 일간손익  : {sys_last_pnl:>12,.0f}원")
    pnl_diff = ex_total_pnl - sys_last_pnl
    if abs(pnl_diff) > 100:
        print(f"  ⚠ 손익 차이: {pnl_diff:+,.0f}원")
    else:
        print(f"  손익 차이: {pnl_diff:+,.0f}원 — OK")

    # ── 4. 요약 ──
    print(f"\n[ 4. 종합 요약 ]")
    print("-" * 80)
    print(f"  조회일자       : {target_date}")
    print(f"  거래소 종목 수  : {len(exchange_journal)}")
    print(f"  시스템 종목 수  : {len(system_summary)}")
    print(f"  거래소 체결 건수 : {len(exchange_executions)}")
    print(f"  시스템 매매 건수 : {len(system_trades)}")
    print(f"  종목 요약 불일치 : {len(discrepancies)}건")
    print(f"  주문 누락       : 거래소만 {len(ex_only)}건 / 시스템만 {len(sys_only)}건")
    print(f"  체결가/수량 불일치: {len(price_mismatches)}건")
    print("=" * 80)

    return {
        'discrepancies': discrepancies,
        'ex_only_orders': ex_only,
        'sys_only_orders': sys_only,
        'price_mismatches': price_mismatches,
        'pnl_diff': pnl_diff,
    }


async def reconcile_date(api: KiwoomRestAPI, target_date: str) -> dict:
    """특정 날짜 매매내역 대조"""
    print(f"\n>>> {target_date} 매매내역 조회 중...")

    # 1. 거래소 데이터 조회
    journal = await api.get_trade_journal(target_date)
    print(f"  ka10170 당일매매일지: {len(journal)}건")

    executions = await api.get_executions()
    print(f"  ka10076 체결내역: {len(executions)}건")

    # 2. 시스템 trades.jsonl 로드
    system_trades = load_trades_jsonl(target_date)
    print(f"  시스템 trades.jsonl: {len(system_trades)}건")

    if not journal and not system_trades:
        print(f"  {target_date} — 거래 기록 없음 (거래소/시스템 모두)")
        return {}

    # 3. 시스템 데이터 요약
    system_summary = summarize_system_trades(system_trades)

    # 4. 비교 리포트
    return compare_and_report(
        journal, system_summary, executions, system_trades, target_date)


async def main():
    import argparse

    parser = argparse.ArgumentParser(description='매매내역 대조 (거래소 vs 시스템)')
    parser.add_argument('--date', '-d', type=str,
                        help='대조 날짜 YYYYMMDD (기본: 최근 거래일)')
    parser.add_argument('--range', '-r', nargs=2, type=str, metavar=('START', 'END'),
                        help='기간 대조 YYYYMMDD YYYYMMDD')
    args = parser.parse_args()

    config = load_config()
    api = KiwoomRestAPI(config)

    try:
        await api.initialize()

        if args.range:
            # 기간 대조
            start_dt, end_dt = args.range
            current = datetime.strptime(start_dt, '%Y%m%d')
            end = datetime.strptime(end_dt, '%Y%m%d')
            while current <= end:
                # 주말 제외
                if current.weekday() < 5:
                    await reconcile_date(api, current.strftime('%Y%m%d'))
                current += timedelta(days=1)
        else:
            # 단일 날짜
            if args.date:
                target = args.date
            else:
                # 최근 거래일 (오늘 또는 금요일)
                now = datetime.now()
                if now.weekday() == 5:  # 토요일
                    now -= timedelta(days=1)
                elif now.weekday() == 6:  # 일요일
                    now -= timedelta(days=2)
                target = now.strftime('%Y%m%d')

            await reconcile_date(api, target)

    finally:
        await api.close()


if __name__ == '__main__':
    asyncio.run(main())
