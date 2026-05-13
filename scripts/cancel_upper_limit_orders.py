"""상한가 종목 미체결 주문 취소 스크립트.

동작:
  1. 미체결 매수 주문 전체 조회 (kt00004)
  2. 각 종목 현재가 조회 → 등락률 +29% 이상이면 상한가 판정
  3. 해당 주문 취소 (kt10003)
  4. 결과 출력 + Telegram 알림 (--telegram 옵션 시)

사용:
  python scripts/cancel_upper_limit_orders.py [--dry-run] [--telegram]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_account import KiwoomNativeAccountFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.gateway.kiwoom_native_orders import KiwoomNativeOrderExecutor

UPPER_LIMIT_THRESHOLD = 29.0   # 등락률 이 이상이면 상한가로 판정


def _build_oauth() -> KiwoomNativeOAuth:
    return KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )


async def _get_flu_rate(oauth: KiwoomNativeOAuth, symbol: str) -> float:
    """현재 등락률 조회."""
    from backend.core.gateway.kiwoom_native_rank import KiwoomNativeLeaderPicker
    try:
        # 잔고에서 cur_price / avg_buy_price 로 근사
        fetcher = KiwoomNativeAccountFetcher(oauth=oauth)
        balance = await fetcher.fetch_balance()
        for h in balance.holdings:
            if h.symbol == symbol:
                return float(h.pnl_rate)
    except Exception:
        pass
    return 0.0


async def main(dry_run: bool, telegram: bool) -> None:
    oauth = _build_oauth()
    fetcher = KiwoomNativeAccountFetcher(oauth=oauth)
    executor = KiwoomNativeOrderExecutor(oauth=oauth, dry_run=dry_run)

    # 1. 미체결 매수 주문 조회
    print("== 미체결 매수 주문 조회 ==")
    try:
        open_orders = await fetcher.fetch_open_orders(trade_type="1")  # 1=매수
    except Exception as e:
        print(f"  미체결 조회 실패: {e}")
        return

    if not open_orders:
        print("  미체결 주문 없음")
        return

    print(f"  총 {len(open_orders)}건 미체결")

    # 2. 잔고에서 등락률 맵 구성
    try:
        balance = await fetcher.fetch_balance()
        flu_map = {h.symbol: float(h.pnl_rate) for h in balance.holdings}
    except Exception:
        flu_map = {}

    # 3. 상한가 판정 및 취소
    cancel_targets = []
    for o in open_orders:
        flu = flu_map.get(o.symbol, 0.0)
        upper = flu >= UPPER_LIMIT_THRESHOLD
        tag = f"+{flu:.2f}% ⚠️상한가" if upper else f"+{flu:.2f}%"
        print(f"  {o.order_no} {o.symbol} {o.name:12s} "
              f"미체결{o.pending_qty}주/{o.order_qty}주  {tag}")
        if upper and o.pending_qty > 0:
            cancel_targets.append(o)

    if not cancel_targets:
        print("\n  상한가 미체결 주문 없음 — 취소 불필요")
        return

    print(f"\n== 취소 대상 {len(cancel_targets)}건 ({'DRY_RUN' if dry_run else '실행'}) ==")
    cancelled, failed = [], []
    for o in cancel_targets:
        try:
            result = await executor.cancel_order(
                original_order_no=o.order_no,
                symbol=o.symbol,
                cancel_qty=0,   # 0 = 전량 취소
            )
            tag = "DRY_RUN" if result.dry_run else "CANCELLED"
            print(f"  [{tag}] {o.symbol} {o.name} order_no={o.order_no} → {result.order_no}")
            cancelled.append(o)
        except Exception as e:
            print(f"  [FAILED] {o.symbol} {o.name} order_no={o.order_no} : {e}")
            failed.append(o)

    # 4. Telegram 알림
    if telegram and cancelled:
        try:
            import httpx
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
            lines = [f"🚫 *상한가 미체결 취소* ({'DRY_RUN' if dry_run else '실행'})"]
            for o in cancelled:
                lines.append(f"  `{o.symbol}` {o.name} {o.pending_qty}주 취소")
            if failed:
                lines.append(f"⚠️ 취소 실패: {', '.join(o.symbol for o in failed)}")
            async with httpx.AsyncClient() as c:
                await c.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "Markdown"},
                    timeout=10,
                )
        except Exception as e:
            print(f"  Telegram 알림 실패: {e}")

    print(f"\n  완료 — 취소 {len(cancelled)}건 / 실패 {len(failed)}건")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", default=False,
                    help="실제 취소 없이 확인만")
    ap.add_argument("--telegram", action="store_true", default=False,
                    help="결과 Telegram 알림")
    args = ap.parse_args()
    asyncio.run(main(dry_run=args.dry_run, telegram=args.telegram))
