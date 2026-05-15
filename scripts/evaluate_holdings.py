"""BAR-OPS-20 — 보유 종목 매도 시그널 평가 CLI.

사용:
    # 평가만 (실 매도 X)
    python scripts/evaluate_holdings.py

    # 보수적 정책 (TP +3%, SL -1%)
    python scripts/evaluate_holdings.py --tp 3.0 --sl -1.0

    # 추천 SL 종목 자동 매도 (DRY_RUN)
    python scripts/evaluate_holdings.py --auto-sell

    # 실전 매도 (LIVE_TRADING_ENABLED 필요)
    python scripts/evaluate_holdings.py --auto-sell --no-dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_account import KiwoomNativeAccountFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.gateway.kiwoom_native_orders import KiwoomNativeOrderExecutor
from backend.core.journal.active_positions import ActivePositionStore
from backend.core.journal.policy_config import PolicyConfigStore
from backend.core.notify.telegram import TelegramNotifier, format_sell_alert
from backend.core.risk.holding_evaluator import (
    ExitPolicy,
    PositionContext,
    SellSignal,
    evaluate_all,
    render_decisions_table,
)
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
    oauth = _build_oauth()
    account = KiwoomNativeAccountFetcher(oauth=oauth)
    balance = await account.fetch_balance()

    if not balance.holdings:
        print("보유 종목 없음.")
        return 0

    # PolicyConfig 자동 로드 (BAR-OPS-32) — CLI default 인 경우만 override
    cfg = PolicyConfigStore("data/policy.json").load()
    tp = args.tp if args.tp != 5.0 else cfg.take_profit_pct
    sl = args.sl if args.sl != -4.0 else cfg.stop_loss_pct
    policy = ExitPolicy(
        take_profit_pct=Decimal(str(tp)),
        stop_loss_pct=Decimal(str(sl)),
        trailing_start_pct=Decimal(str(cfg.trailing_start_pct)),
        trailing_offset_pct=Decimal(str(cfg.trailing_offset_pct)),
        breakeven_trigger_pct=Decimal(str(cfg.breakeven_trigger_pct)),
        partial_tp_pct=Decimal(str(cfg.partial_tp_pct)),
        partial_tp_ratio=Decimal(str(cfg.partial_tp_ratio)),
        hold_days_tighten=cfg.hold_days_tighten,
        tightened_sl_pct=Decimal(str(cfg.tightened_sl_pct)),
    )

    # CLI에서 TP/SL 명시 지정 시 (강제청산 등) 전략 override 비활성
    force_mode = (args.tp != 5.0 or args.sl != -4.0)

    # ActivePosition 컨텍스트 로드 + peak 업데이트
    pos_store = ActivePositionStore(args.pos_log)
    active_positions = pos_store.load_all()
    contexts: dict[str, PositionContext] = {}
    if not force_mode:
        for h in balance.holdings:
            pos = active_positions.get(h.symbol)
            if pos:
                # peak 수익률 갱신
                cur_rate = float(h.pnl_rate)
                if cur_rate > pos.peak_pnl_rate:
                    pos.peak_pnl_rate = cur_rate
                    pos.peak_updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    pos_store.upsert(pos)
                contexts[h.symbol] = PositionContext(
                    peak_pnl_rate=pos.peak_pnl_rate,
                    partial_tp_done=pos.partial_tp_done,
                    entry_time=pos.entry_time,
                    strategy=pos.strategy,
                )

    decisions = evaluate_all(balance.holdings, policy, contexts)
    mode_label = "적응형" if contexts else "기본"
    print(
        f"== 보유 종목 평가 ({len(decisions)} 종목, TP={tp}%, SL={sl}%, "
        f"트레일링={cfg.trailing_start_pct}%/-{cfg.trailing_offset_pct}%, "
        f"BE={cfg.breakeven_trigger_pct}%) [{mode_label}] ==\n"
    )
    print(render_decisions_table(decisions))

    executor = KiwoomNativeOrderExecutor(oauth=oauth, dry_run=args.dry_run)
    notifier = TelegramNotifier.from_env() if args.telegram else None
    gate = LiveOrderGate(
        executor=executor, audit_path=args.audit_log,
        policy=GatePolicy(daily_max_orders=args.daily_max_orders),
        notifier=notifier,
    )

    # ── DCA 2·3분할 매수 체크 (매도 대상은 DCA 스킵) ─────────────────────────
    _SELL_SIGNALS = {
        SellSignal.STOP_LOSS, SellSignal.TRAILING_STOP,
        SellSignal.BREAKEVEN_STOP, SellSignal.TIME_TIGHTENED_SL,
    }
    sl_symbols = {d.symbol for d in decisions if d.signal in _SELL_SIGNALS}

    if args.auto_sell:
        active_positions = pos_store.load_all()
        dca_executed = 0
        for h in balance.holdings:
            # SL 대상 종목은 DCA 스킵
            if h.symbol in sl_symbols:
                continue
            pos = active_positions.get(h.symbol)
            if not pos:
                continue
            pending = pos.pending_tranches()
            if not pending:
                continue
            cur_price = float(h.cur_price)
            for tranche in pending:
                # qty=0 방어 (원래 수량이 작을 때 25% → 0주)
                if tranche.qty <= 0:
                    continue
                trigger_price = pos.entry_price * (1 + tranche.trigger_drop_pct / 100)
                if cur_price > trigger_price:
                    continue  # 아직 트리거 조건 미달
                print(
                    f"  [DCA-T{tranche.tranche}] {h.symbol} {h.name:<14} "
                    f"qty={tranche.qty} 현재가={cur_price:,.0f} ≤ 트리거{trigger_price:,.0f}"
                    f"({tranche.trigger_drop_pct:+.0f}%)"
                )
                try:
                    r = await gate.place_buy(symbol=h.symbol, qty=tranche.qty)
                    tag = "DRY_RUN" if r.dry_run else "ORDERED"
                    print(f"    [{tag}] order_no={r.order_no}")
                    # 트랜치 상태 업데이트
                    tranche.status = "filled"
                    tranche.order_no = r.order_no
                    tranche.filled_price = cur_price
                    tranche.filled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    pos_store.upsert(pos)
                    dca_executed += 1
                except Exception as e:
                    print(f"    [BLOCKED] DCA 주문 실패: {e}")
        if dca_executed:
            print(f"\n  → DCA 분할매수 {dca_executed} 건 실행")

    if not args.auto_sell:
        return 0

    sell_targets = [d for d in decisions if d.signal != SellSignal.HOLD]
    if not sell_targets:
        print("\n매도 대상 없음 (모두 HOLD).")
        return 0

    print(f"\n== 자동 매도 ({len(sell_targets)} 종목, dry_run={args.dry_run}) ==")
    for d in sell_targets:
        sell_qty = d.sell_qty if d.sell_qty > 0 else d.qty
        try:
            r = await gate.place_sell(symbol=d.symbol, qty=sell_qty)
            tag = "DRY_RUN" if r.dry_run else "ORDERED"
            print(
                f"  [{tag}] {d.symbol} {d.name:<14} qty={sell_qty:>5}/{d.qty} "
                f"signal={d.signal.value:<16} order_no={r.order_no}"
            )
            # 분할 익절 시 partial_tp_done 업데이트 (전량 청산 X)
            if d.signal == SellSignal.PARTIAL_TP:
                pos = active_positions.get(d.symbol)
                if pos:
                    pos.partial_tp_done = True
                    pos_store.upsert(pos)
            # 전량 매도 시 active_positions 정리
            elif sell_qty >= d.qty:
                pos_store.remove(d.symbol)
            if notifier:
                try:
                    await notifier.send(format_sell_alert(
                        d.symbol, d.name, sell_qty, d.signal.value,
                        float(d.pnl_rate), r.order_no, r.dry_run,
                    ))
                except Exception as te:
                    print(f"    telegram 알림 실패: {te}")
        except Exception as e:
            print(f"  [BLOCKED] {d.symbol} {d.name:<14}: {type(e).__name__}: {e}")

    print(f"\n  → audit log: {args.audit_log}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="보유 종목 매도 시그널 평가 (BAR-OPS-20)")
    ap.add_argument("--tp", type=float, default=5.0, help="take_profit %% (기본 5.0)")
    ap.add_argument("--sl", type=float, default=-4.0, help="stop_loss %% (기본 -4.0, policy.json 우선)")
    ap.add_argument("--pos-log", default="data/active_positions.json",
                    help="활성 포지션 메타 경로")
    ap.add_argument("--auto-sell", action="store_true",
                    help="TP/SL 도달 종목 LiveOrderGate 매도")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="DRY_RUN (기본). 실 매도는 --no-dry-run + LIVE_TRADING_ENABLED")
    ap.add_argument("--no-dry-run", action="store_false", dest="dry_run")
    ap.add_argument("--audit-log", default="data/order_audit.csv",
                    help="audit CSV (OPS-17)")
    ap.add_argument("--daily-max-orders", type=int, default=50,
                    help="일일 거래수 한도 (기본 50)")
    ap.add_argument("--telegram", action="store_true",
                    help="Telegram 알림 (TELEGRAM_BOT_TOKEN/CHAT_ID 필요)")
    args = ap.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
