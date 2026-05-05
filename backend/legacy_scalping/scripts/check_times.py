"""API 체결내역 시간 vs 로컬 매매일지 시간 비교"""
import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime

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
    """trades.jsonl에서 해당일 매매 로드"""
    trades_path = PROJECT_ROOT / "logs" / "trades.jsonl"
    formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    trades = []
    with open(trades_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            t = json.loads(line)
            if t.get('timestamp', '').startswith(formatted):
                trades.append(t)
    return trades


async def main():
    date_str = "20260406"
    if len(sys.argv) > 1:
        date_str = sys.argv[1]

    formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    config = load_config()
    api = KiwoomRestAPI(config)

    try:
        await api.initialize()

        # 1. kt00009 체결내역 조회 (체결만)
        print(f"=== API 체결내역 조회 (kt00009, {date_str}) ===\n")
        executions = await api.get_order_executions(
            sell_tp="0", qry_tp="1", ord_dt=date_str)

        print(f"API 체결 건수: {len(executions)}건\n")

        # 원시 시간 필드 디버깅
        if executions:
            print(f"[DEBUG] 첫 체결 원시 데이터: {json.dumps(executions[0], ensure_ascii=False, indent=2)}\n")

        # API 체결 정렬 (주문번호순 = 시간순)
        executions.sort(key=lambda x: x.get('order_no', ''))

        # 2. 로컬 매매일지 로드
        local = load_local_trades(date_str)
        print(f"로컬 매매 건수: {len(local)}건\n")

        # 로컬 매매를 order_no 기준으로 인덱싱
        local_by_order = {}
        for t in local:
            ono = t.get('order_no', '')
            if ono:
                local_by_order[str(ono)] = t

        # 3. API 체결 출력 + 로컬 시간 비교
        print(f"{'No':>3} | {'시간(API)':>10} | {'종목':>12} | {'구분':>4} | "
              f"{'수량':>6} | {'체결가':>10} | {'주문번호':>10} | "
              f"{'시간(로컬)':>22} | {'시간차':>6}")
        print("-" * 130)

        for i, e in enumerate(executions, 1):
            tm = e.get('time', '').strip()
            # API 시간 포맷: "HH:MM:SS" (이미 콜론 포함)
            api_time_str = tm if tm else '?'

            code = e.get('code', '')
            name = e.get('name', '')[:8]
            trade_type = e.get('trade_type', '')
            qty = e.get('filled_qty', 0) or e.get('qty', 0)
            price = e.get('filled_price', 0) or e.get('price', 0)
            order_no = e.get('order_no', '')

            # 로컬 매칭
            local_t = local_by_order.get(str(order_no))
            local_time_str = ''
            time_diff = ''

            if local_t:
                ts = local_t.get('timestamp', '')
                if 'T' in ts:
                    local_time_str = ts.split('T')[1][:12]
                elif ' ' in ts:
                    local_time_str = ts.split(' ')[1][:12]
                else:
                    local_time_str = ts

                # 시간차 계산
                try:
                    api_dt = datetime.strptime(f"{formatted} {tm}", "%Y-%m-%d %H:%M:%S")
                    local_dt_str = local_time_str[:8]  # HH:MM:SS
                    local_dt = datetime.strptime(f"{formatted} {local_dt_str}", "%Y-%m-%d %H:%M:%S")
                    diff_sec = (api_dt - local_dt).total_seconds()
                    time_diff = f"{diff_sec:+.0f}초"
                except Exception as ex:
                    time_diff = '?'
            else:
                local_time_str = '(미매칭)'
                time_diff = '-'

            # 매수/매도 구분 (order_type: 현금매수/현금매도)
            order_type = e.get('order_type', '')
            if '매도' in order_type:
                action = '매도'
            elif '매수' in order_type:
                action = '매수'
            else:
                action = order_type[:4]

            print(f"{i:>3} | {api_time_str:>10} | {name:>12} | {action:>4} | "
                  f"{qty:>6,} | {price:>10,} | {order_no:>10} | "
                  f"{local_time_str:>22} | {time_diff:>6}")

        # 4. 로컬에만 있는 주문
        api_orders = {str(e.get('order_no', '')) for e in executions}
        local_only = [t for t in local if str(t.get('order_no', '')) not in api_orders and t.get('order_no')]
        if local_only:
            print(f"\n\n=== 로컬에만 있는 주문 ({len(local_only)}건) ===")
            for t in local_only:
                print(f"  주문#{t.get('order_no')} | {t.get('name', '')} | "
                      f"{t['action']} {t.get('qty',0)}주 @ {t.get('price',0):,} | "
                      f"시간: {t.get('timestamp', '')}")

        # 5. API에만 있는 주문
        api_only = [e for e in executions if str(e.get('order_no', '')) not in local_by_order]
        if api_only:
            print(f"\n\n=== API에만 있는 주문 ({len(api_only)}건) ===")
            for e in api_only:
                tm = e.get('time', '')
                api_ts = f"{tm[:2]}:{tm[2:4]}:{tm[4:6]}" if len(tm) >= 6 else tm
                print(f"  주문#{e.get('order_no')} | {e.get('name', '')} | "
                      f"{e.get('trade_type', '')} {e.get('filled_qty', 0)}주 "
                      f"@ {e.get('filled_price', 0):,} | API시간: {api_ts}")

    finally:
        await api.close()


if __name__ == '__main__':
    asyncio.run(main())
