"""장중 실시간 포지션 관리 데몬 — 09:30~15:20 3분 간격.

매 사이클마다:
  1) 보유 종목 매도 평가 (TP/SL/트레일링/DCA)
  2) 신규 시그널 매수 스캔

사용:
    python scripts/intraday_buy_daemon.py
    python scripts/intraday_buy_daemon.py --interval 180 --top 5 --no-dry-run --telegram
"""
from __future__ import annotations

import argparse
import asyncio
import csv as _csv
import os
import signal
import sys
from datetime import datetime, time, timezone, timedelta
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
from backend.core.notify.telegram import TelegramNotifier, format_buy_alert, format_sell_alert
from backend.core.risk.balance_gate import evaluate_risk_gate
from backend.core.risk.holding_evaluator import (
    ExitPolicy, PositionContext, SellSignal, STRATEGY_EXIT_PROFILES,
    evaluate_all, resolve_policy,
)
from backend.core.risk.live_order_gate import GatePolicy, LiveOrderGate

KST = timezone(timedelta(hours=9))
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(15, 20)


def _now_kst() -> datetime:
    return datetime.now(KST)


def _in_market_hours() -> bool:
    now = _now_kst().time()
    return MARKET_OPEN <= now <= MARKET_CLOSE


def _build_oauth() -> KiwoomNativeOAuth:
    app_key = os.environ.get("KIWOOM_APP_KEY", "")
    app_secret = os.environ.get("KIWOOM_APP_SECRET", "")
    base_url = os.environ.get("KIWOOM_BASE_URL", "https://openapi.kiwoom.com")
    if not app_key or not app_secret:
        raise SystemExit("KIWOOM_APP_KEY / KIWOOM_APP_SECRET 환경변수 필요")
    return KiwoomNativeOAuth(
        app_key=SecretStr(app_key), app_secret=SecretStr(app_secret),
        base_url=base_url,
    )


async def _sync_positions(pos_store: ActivePositionStore, held_symbols: set[str]) -> int:
    """브로커 잔고와 active_positions 동기화. 잔고에 없는 종목은 제거."""
    active = pos_store.load_all()
    removed = 0
    for sym in list(active.keys()):
        if sym not in held_symbols:
            pos_store.remove(sym)
            ts = _now_kst().strftime("%H:%M:%S")
            print(f"  [{ts}][SYNC] {sym} {active[sym].name} — 잔고에 없음, active_positions 제거")
            removed += 1
    return removed


async def _evaluate_and_sell(args, oauth, notifier) -> int:
    """보유 종목 매도 평가 + DCA. 매도 건수 반환."""
    cfg = PolicyConfigStore("data/policy.json").load()
    account = KiwoomNativeAccountFetcher(oauth=oauth)
    balance = await account.fetch_balance()

    # 브로커 잔고 ↔ active_positions 동기화
    pos_store = ActivePositionStore(args.pos_log)
    held_symbols = {h.symbol for h in (balance.holdings or [])}
    await _sync_positions(pos_store, held_symbols)

    if not balance.holdings:
        return 0

    policy = ExitPolicy(
        take_profit_pct=Decimal(str(cfg.take_profit_pct)),
        stop_loss_pct=Decimal(str(cfg.stop_loss_pct)),
        trailing_start_pct=Decimal(str(cfg.trailing_start_pct)),
        trailing_offset_pct=Decimal(str(cfg.trailing_offset_pct)),
        breakeven_trigger_pct=Decimal(str(cfg.breakeven_trigger_pct)),
        partial_tp_pct=Decimal(str(cfg.partial_tp_pct)),
        partial_tp_ratio=Decimal(str(cfg.partial_tp_ratio)),
        hold_days_tighten=cfg.hold_days_tighten,
        tightened_sl_pct=Decimal(str(cfg.tightened_sl_pct)),
    )

    active_positions = pos_store.load_all()
    contexts: dict[str, PositionContext] = {}

    for h in balance.holdings:
        pos = active_positions.get(h.symbol)
        if pos:
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

    # DCA 분할매수
    _SELL_SIGNALS = {
        SellSignal.STOP_LOSS, SellSignal.TRAILING_STOP,
        SellSignal.BREAKEVEN_STOP, SellSignal.TIME_TIGHTENED_SL,
    }
    sl_symbols = {d.symbol for d in decisions if d.signal in _SELL_SIGNALS}

    executor = KiwoomNativeOrderExecutor(oauth=oauth, dry_run=args.dry_run)
    gate = LiveOrderGate(
        executor=executor, audit_path=args.audit_log,
        policy=GatePolicy(daily_max_orders=cfg.daily_max_orders),
        notifier=notifier,
    )

    # DCA
    active_positions = pos_store.load_all()
    for h in balance.holdings:
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
            if tranche.qty <= 0:
                continue
            trigger_price = pos.entry_price * (1 + tranche.trigger_drop_pct / 100)
            if cur_price > trigger_price:
                continue
            try:
                r = await gate.place_buy(symbol=h.symbol, qty=tranche.qty)
                tag = "DRY_RUN" if r.dry_run else "DCA"
                ts = _now_kst().strftime("%H:%M:%S")
                print(f"  [{ts}][{tag}] {h.symbol} {h.name} T{tranche.tranche} qty={tranche.qty}")
                tranche.status = "filled"
                tranche.order_no = r.order_no
                tranche.filled_price = cur_price
                tranche.filled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                pos_store.upsert(pos)
            except Exception as e:
                print(f"  [DCA-ERR] {h.symbol}: {e}")

    # 매도 실행
    sell_targets = [d for d in decisions if d.signal != SellSignal.HOLD]
    if not sell_targets:
        return 0

    sold = 0
    for d in sell_targets:
        sell_qty = d.sell_qty if d.sell_qty > 0 else d.qty
        try:
            r = await gate.place_sell(symbol=d.symbol, qty=sell_qty)
            tag = "DRY_RUN" if r.dry_run else "SOLD"
            ts = _now_kst().strftime("%H:%M:%S")
            print(
                f"  [{ts}][{tag}] {d.symbol} {d.name:<14} qty={sell_qty}/{d.qty} "
                f"signal={d.signal.value} pnl={d.pnl_rate:+.1f}%"
            )
            if d.signal == SellSignal.PARTIAL_TP:
                pos = active_positions.get(d.symbol)
                if pos:
                    pos.partial_tp_done = True
                    pos_store.upsert(pos)
            elif sell_qty >= d.qty:
                pos_store.remove(d.symbol)

            if notifier:
                try:
                    await notifier.send(format_sell_alert(
                        d.symbol, d.name, sell_qty, d.signal.value,
                        float(d.pnl_rate), r.order_no, r.dry_run,
                    ))
                except Exception:
                    pass
            sold += 1
        except Exception as e:
            print(f"  [SELL-ERR] {d.symbol}: {e}")

    return sold


async def _scan_and_buy(args, oauth, session_bought: set[str]) -> int:
    """한 사이클: 스캔 → 시그널 검증 → 매수. 매수 건수 반환."""
    cfg = PolicyConfigStore("data/policy.json").load()
    picker = KiwoomNativeLeaderPicker(
        oauth=oauth, min_flu_rate=args.min_flu, min_score=cfg.min_score,
    )
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth)

    leaders = await picker.pick(top_n=args.top)
    if not leaders:
        return 0

    # 보유/당일매도/세션매수 제외
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

    excluded = already_held | active_symbols | today_sold | session_bought
    filtered = [c for c in leaders if c.symbol not in excluded
                and c.flu_rate < 25.0 and c.cur_price >= 5_000]

    if not filtered:
        return 0

    # 전략 시뮬레이션 시그널 검증
    sim = IntradaySimulator()
    strategies = ["f_zone", "sf_zone", "gold_zone", "swing_38"]
    signals = []

    for c in filtered:
        try:
            candles = await fetcher.fetch_daily(symbol=c.symbol)
        except Exception:
            continue
        if len(candles) < 60:
            continue

        result = sim.run(candles, symbol=c.symbol, strategies=strategies)
        best_strategy = max(result.pnl_by_strategy, key=lambda s: float(result.pnl_by_strategy[s]))
        best_pnl = float(result.pnl_by_strategy[best_strategy])

        if best_pnl > 0 and len(result.trades) > 0:
            signals.append((c, best_strategy, best_pnl))
            print(f"  [SIGNAL] {c.symbol} {c.name:<14} 전략={best_strategy} PnL={best_pnl:+,.0f}")

    if not signals:
        return 0

    # 자금 한도 체크
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
        return 0

    # 주문 실행
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
            ts = _now_kst().strftime("%H:%M:%S")
            print(
                f"  [{ts}][{tag}] {r.symbol} {r.name:<14} qty={tranche1_qty}"
                f"(전체 {r.recommended_qty}) strategy={strategy} order_no={result.order_no}"
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

            session_bought.add(r.symbol)

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

    return executed


async def _daemon(args):
    print(f"== 실시간 포지션 관리 데몬 (interval={args.interval}s, top={args.top}) ==")
    oauth = _build_oauth()
    notifier = TelegramNotifier.from_env() if args.telegram else None
    session_bought: set[str] = set()
    total_bought = 0
    total_sold = 0

    # 장 시작 전이면 대기
    while not _in_market_hours():
        now = _now_kst()
        print(f"  [{now.strftime('%H:%M:%S')}] 장 시작 대기중...")
        await asyncio.sleep(60)

    print(f"  [{_now_kst().strftime('%H:%M:%S')}] 장중 감시 시작")

    while _in_market_hours():
        ts = _now_kst().strftime("%H:%M:%S")

        # 1) 매도 평가 (우선)
        try:
            sell_count = await _evaluate_and_sell(args, oauth, notifier)
            if sell_count > 0:
                total_sold += sell_count
                print(f"  [{ts}] 매도 {sell_count}건 (누적 {total_sold}건)")
        except Exception as e:
            print(f"  [{ts}][SELL-ERROR] {type(e).__name__}: {e}")

        # 2) 매수 스캔
        try:
            buy_count = await _scan_and_buy(args, oauth, session_bought)
            if buy_count > 0:
                total_bought += buy_count
                print(f"  [{ts}] 매수 {buy_count}건 (누적 {total_bought}건)")
        except Exception as e:
            print(f"  [{ts}][BUY-ERROR] {type(e).__name__}: {e}")

        # 다음 사이클까지 대기
        if _in_market_hours():
            await asyncio.sleep(args.interval)

    print(f"\n== 장 마감 — 데몬 종료 (매수 {total_bought}건, 매도 {total_sold}건) ==")


def main():
    ap = argparse.ArgumentParser(description="장중 시그널 매수 데몬 (3분 간격)")
    ap.add_argument("--interval", type=int, default=60, help="스캔 간격 초 (기본 60=1분)")
    ap.add_argument("--top", type=int, default=5, help="스캔 후보 수 (기본 5)")
    ap.add_argument("--min-flu", type=float, default=3.0, help="최소 등락률 (기본 3.0%%)")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--no-dry-run", action="store_false", dest="dry_run")
    ap.add_argument("--telegram", action="store_true", help="텔레그램 알림")
    ap.add_argument("--audit-log", default="data/order_audit.csv")
    ap.add_argument("--pos-log", default="data/active_positions.json")
    args = ap.parse_args()

    # graceful shutdown
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: loop.stop())

    try:
        loop.run_until_complete(_daemon(args))
    except (KeyboardInterrupt, SystemExit):
        print("\n데몬 종료.")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
