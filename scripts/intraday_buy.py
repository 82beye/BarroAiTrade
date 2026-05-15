"""장중 시그널 기반 매수 — 10:00~14:30 매시 실행.

09:30 simulate_leaders 이후, 장중에도 새로운 진입 시그널이 발생하면 매수.
현재 보유종목/당일 매도종목은 제외, 자금 한도 내에서만 진입.

사용:
    python scripts/intraday_buy.py
    python scripts/intraday_buy.py --top 3 --no-dry-run --telegram

cron 등록 예:
    30 10-14 * * 1-5 cd $REPO && ... python scripts/intraday_buy.py --no-dry-run --telegram >> logs/intraday.log 2>&1
"""
from __future__ import annotations

import argparse
import asyncio
import csv as _csv
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import SecretStr

from backend.core.backtester import IntradaySimulator
from backend.core.gateway.kiwoom_native_account import KiwoomNativeAccountFetcher
from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.gateway.kiwoom_native_orders import KiwoomNativeOrderExecutor
from backend.core.gateway.kiwoom_native_rank import KiwoomNativeLeaderPicker
from backend.core.journal.active_positions import ActivePositionStore
from backend.core.journal.policy_config import PolicyConfigStore
from backend.core.notify.telegram import TelegramNotifier, format_buy_alert
from backend.core.risk.balance_gate import evaluate_risk_gate
from backend.core.risk.holding_evaluator import STRATEGY_EXIT_PROFILES
from backend.core.risk.live_order_gate import GatePolicy, LiveOrderGate


def _build_oauth() -> KiwoomNativeOAuth:
    app_key = os.environ.get("KIWOOM_APP_KEY", "")
    app_secret = os.environ.get("KIWOOM_APP_SECRET", "")
    base_url = os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com")
    if not app_key or not app_secret:
        raise SystemExit("KIWOOM_APP_KEY / KIWOOM_APP_SECRET 환경변수 필요")
    return KiwoomNativeOAuth(
        app_key=SecretStr(app_key), app_secret=SecretStr(app_secret),
        base_url=base_url,
    )


async def _run(args) -> int:
    cfg = PolicyConfigStore("data/policy.json").load()
    oauth = _build_oauth()
    picker = KiwoomNativeLeaderPicker(
        oauth=oauth, min_flu_rate=args.min_flu, min_score=cfg.min_score,
    )
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth)

    # 1. 주도주 스캔
    print(f"== 장중 시그널 스캔 (top={args.top}, min_flu={args.min_flu}%) ==")
    leaders = await picker.pick(top_n=args.top)
    if not leaders:
        print("후보 없음.")
        return 0

    # 2. 이미 보유/당일 매도 종목 제외
    account = KiwoomNativeAccountFetcher(oauth=oauth)
    balance = await account.fetch_balance()
    already_held = {h.symbol for h in (balance.holdings or [])}

    pos_store = ActivePositionStore(args.pos_log)
    active_symbols = set(pos_store.load_all().keys())

    today_sold: set[str] = set()
    audit_path = Path(args.audit_log)
    if audit_path.exists():
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            with audit_path.open(newline="", encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    if row.get("ts", "").startswith(today) and row.get("side") == "sell":
                        today_sold.add(row["symbol"])
        except Exception:
            pass

    excluded = already_held | active_symbols | today_sold
    filtered = [c for c in leaders if c.symbol not in excluded
                and c.flu_rate < 25.0 and c.cur_price >= 5_000]

    if not filtered:
        print(f"신규 진입 후보 없음 (보유 {len(already_held)}, 당일매도 {len(today_sold)}, 총후보 {len(leaders)})")
        return 0

    print(f"후보 {len(filtered)}개 (제외: 보유={len(already_held)}, 매도={len(today_sold)})")

    # 3. 전략 시뮬레이션으로 시그널 검증
    sim = IntradaySimulator()
    strategies = ["f_zone", "sf_zone", "gold_zone", "swing_38"]
    signals = []  # (candidate, best_strategy, pnl)

    for c in filtered:
        try:
            candles = await fetcher.fetch_daily(symbol=c.symbol)
        except Exception as e:
            print(f"  {c.symbol} fetch failed: {e}")
            continue

        if len(candles) < 60:
            continue

        result = sim.run(candles, symbol=c.symbol, strategies=strategies)
        # 수익이 나는 전략이 있는 종목만 진입
        best_strategy = max(result.pnl_by_strategy, key=lambda s: float(result.pnl_by_strategy[s]))
        best_pnl = float(result.pnl_by_strategy[best_strategy])

        if best_pnl > 0 and len(result.trades) > 0:
            signals.append((c, best_strategy, best_pnl))
            print(f"  [SIGNAL] {c.symbol} {c.name:<14} 전략={best_strategy} PnL={best_pnl:+,.0f}")
        else:
            print(f"  [SKIP]   {c.symbol} {c.name:<14} 시그널 없음 (best PnL={best_pnl:+,.0f})")

    if not signals:
        print("\n장중 진입 시그널 없음.")
        return 0

    # 4. 자금 한도 체크
    deposit = await account.fetch_deposit()
    candidates_for_gate = [
        (c.symbol, c.name, Decimal(str(c.cur_price))) for c, _, _ in signals
    ]
    gate_result = evaluate_risk_gate(
        deposit=deposit, balance=balance,
        candidates=candidates_for_gate,
        max_per_position_ratio=Decimal(str(cfg.max_per_position)),
        max_total_position_ratio=Decimal(str(cfg.max_total_position)),
    )

    buyable = [(r, s) for r, (_, s, _) in zip(gate_result.recommendations, signals)
               if not r.blocked and r.recommended_qty > 0]

    if not buyable:
        print("\n자금 한도 초과 — 매수 불가.")
        return 0

    # 5. 주문 실행
    if not args.execute:
        print(f"\n== 시그널 {len(buyable)}건 (--execute 미지정, 미실행) ==")
        for r, strategy in buyable:
            print(f"  {r.symbol} {r.name:<14} 전략={strategy} qty={r.recommended_qty}")
        return 0

    print(f"\n== 장중 매수 실행 ({len(buyable)}건, dry_run={args.dry_run}) ==")
    notifier = TelegramNotifier.from_env() if args.telegram else None
    executor = KiwoomNativeOrderExecutor(oauth=oauth, dry_run=args.dry_run)
    gate = LiveOrderGate(
        executor=executor, audit_path=args.audit_log,
        policy=GatePolicy(
            daily_loss_limit_pct=Decimal(str(cfg.daily_loss_limit)),
            daily_max_orders=cfg.daily_max_orders,
        ),
        notifier=notifier,
    )

    executed = 0
    for r, strategy in buyable:
        tranche1_qty = max(1, round(r.recommended_qty * 0.5))
        try:
            result = await gate.place_buy(symbol=r.symbol, qty=tranche1_qty)
            tag = "DRY_RUN" if result.dry_run else "ORDERED"
            print(
                f"  [{tag}] {r.symbol} {r.name:<14} qty={tranche1_qty}"
                f"(1/3, 전체 {r.recommended_qty}) strategy={strategy} order_no={result.order_no}"
            )

            # active_positions 저장
            profile = STRATEGY_EXIT_PROFILES.get(strategy.replace("_v1", "").replace("_v2", ""), {})
            sl = float(profile.get("stop_loss_pct", -4.0))
            leader = next((c for c, _, _ in signals if c.symbol == r.symbol), None)
            pos_store.create_from_order(
                symbol=r.symbol, name=r.name,
                strategy=strategy,
                entry_price=float(r.cur_price),
                total_recommended_qty=r.recommended_qty,
                order_no=result.order_no,
                sl_pct=sl,
                flu_rate=float(leader.flu_rate) if leader else 0.0,
                score=float(leader.score) if leader else 0.0,
            )

            if notifier:
                try:
                    await notifier.send(format_buy_alert(
                        r.symbol, r.name, tranche1_qty,
                        result.order_no, result.dry_run,
                    ))
                except Exception:
                    pass
            executed += 1
        except Exception as e:
            print(f"  [BLOCKED] {r.symbol}: {e}")

    print(f"\n  → 장중 매수 {executed}건 실행 완료")
    return 0


def main():
    ap = argparse.ArgumentParser(description="장중 시그널 기반 매수")
    ap.add_argument("--top", type=int, default=5, help="스캔 후보 수 (기본 5)")
    ap.add_argument("--min-flu", type=float, default=3.0, help="최소 등락률 (기본 3.0%)")
    ap.add_argument("--execute", action="store_true", help="실제 주문 실행")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--no-dry-run", action="store_false", dest="dry_run")
    ap.add_argument("--telegram", action="store_true", help="텔레그램 알림")
    ap.add_argument("--audit-log", default="data/order_audit.csv")
    ap.add_argument("--pos-log", default="data/active_positions.json")
    args = ap.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
