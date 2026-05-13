"""BAR-OPS-11 — 당일 주도주 자동 선정 + 다종목 시뮬.

단일 종목 지정 X. 키움 자체 OpenAPI 의 거래대금/등락률 ranking 으로 당일
주도주 top N 자동 선정 → 각 종목 일봉/분봉 시뮬 → 통합 리포트.

환경변수 (.env.local):
    KIWOOM_APP_KEY, KIWOOM_APP_SECRET, KIWOOM_BASE_URL

사용:
    python scripts/simulate_leaders.py                          # daily, top 5
    python scripts/simulate_leaders.py --top 10 --mode daily
    python scripts/simulate_leaders.py --mode minute --tic-scope 1
    python scripts/simulate_leaders.py --min-flu 3.0 --top 3    # 등락률 ≥3%
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone

from pydantic import SecretStr

from decimal import Decimal

from backend.core.backtester import IntradaySimulator
from backend.core.gateway.kiwoom_native_account import KiwoomNativeAccountFetcher
from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.gateway.kiwoom_native_orders import KiwoomNativeOrderExecutor
from backend.core.gateway.kiwoom_native_rank import (
    KiwoomNativeLeaderPicker,
    LeaderCandidate,
)
from backend.core.journal.active_positions import ActivePositionStore
from backend.core.journal.policy_config import PolicyConfigStore
from backend.core.journal.simulation_log import SimulationLogEntry, SimulationLogger
from backend.core.notify.telegram import (
    TelegramNotifier,
    format_buy_alert,
    format_simulation_summary,
)
from backend.core.risk.balance_gate import evaluate_risk_gate
from backend.core.risk.live_order_gate import GatePolicy, LiveOrderGate


def _build_oauth() -> KiwoomNativeOAuth:
    app_key = os.environ.get("KIWOOM_APP_KEY", "")
    app_secret = os.environ.get("KIWOOM_APP_SECRET", "")
    base_url = os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com")
    if not app_key or not app_secret:
        raise SystemExit(
            "KIWOOM_APP_KEY / KIWOOM_APP_SECRET 환경변수 필요.\n"
            "예: set -a; . ./.env.local; set +a"
        )
    return KiwoomNativeOAuth(
        app_key=SecretStr(app_key),
        app_secret=SecretStr(app_secret),
        base_url=base_url,
    )


async def _run(args) -> int:
    # PolicyConfig 자동 로드 — CLI 명시 X 인 옵션만 config 값 사용 (BAR-OPS-32)
    cfg = PolicyConfigStore("data/policy.json").load()
    if args.min_score == 0.0:
        args.min_score = cfg.min_score
    if args.max_per_position == 0.30:
        args.max_per_position = cfg.max_per_position
    if args.max_total_position == 0.90:
        args.max_total_position = cfg.max_total_position
    if args.daily_loss_limit == -3.0:
        args.daily_loss_limit = cfg.daily_loss_limit
    if args.daily_max_orders == 50:
        args.daily_max_orders = cfg.daily_max_orders

    oauth = _build_oauth()
    picker = KiwoomNativeLeaderPicker(
        oauth=oauth,
        min_flu_rate=args.min_flu,
        min_score=args.min_score,
    )
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth)

    print(f"== 당일 주도주 선정 (mode={args.mode}, top={args.top}, min_flu={args.min_flu}%, min_score={args.min_score}) [policy.json 로드됨] ==")
    leaders: list[LeaderCandidate] = await picker.pick(top_n=args.top)
    if not leaders:
        print("주도주 후보 없음. --min-flu 또는 --min-score 낮춰서 재시도.")
        return 1

    print(f"\n선정된 주도주 {len(leaders)} 종목 (3-factor: 거래대금·등락률·거래량):")
    print(f"  {'rank':>4} {'symbol':<8} {'name':<16} {'price':>10} {'flu%':>7} {'TVrk':>5} {'FRrk':>5} {'VOLrk':>6} {'score':>6}")
    for i, c in enumerate(leaders, 1):
        print(
            f"  {i:>4} {c.symbol:<8} {c.name:<16} {c.cur_price:>10,.0f} "
            f"{c.flu_rate:>+7.2f} {str(c.rank_trade_value or '-'):>5} "
            f"{str(c.rank_flu_rate or '-'):>5} {str(c.rank_volume or '-'):>6} {c.score:>6.3f}"
        )

    # 각 종목 시뮬
    sim = IntradaySimulator()
    strategies = args.strategies.split(",")
    total_pnl = 0.0
    total_trades = 0
    per_strategy_pnl: dict[str, float] = {s: 0.0 for s in strategies}
    log_entries: list[SimulationLogEntry] = []
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    print(f"\n== 시뮬 실행 ({len(leaders)} 종목 × {len(strategies)} 전략) ==")
    for c in leaders:
        try:
            if args.mode == "daily":
                candles = await fetcher.fetch_daily(symbol=c.symbol)
            else:
                candles = await fetcher.fetch_minute(symbol=c.symbol, tic_scope=args.tic_scope)
        except Exception as e:
            print(f"  {c.symbol} {c.name:<16} fetch failed: {e}")
            continue

        if len(candles) < 31:
            print(f"  {c.symbol} {c.name:<16} 캔들 부족 ({len(candles)} < 31), 스킵")
            continue

        result = sim.run(candles, symbol=c.symbol, strategies=strategies)
        sym_pnl = float(sum(result.pnl_by_strategy.values()))
        total_pnl += sym_pnl
        total_trades += len(result.trades)
        for sid, pnl in result.pnl_by_strategy.items():
            pnl_f = float(pnl)
            per_strategy_pnl[sid] = per_strategy_pnl.get(sid, 0.0) + pnl_f
            sid_trades = [t for t in result.trades if t.strategy_id == sid]
            wr = result.win_rate_by_strategy.get(sid, 0.0)
            log_entries.append(SimulationLogEntry(
                run_at=run_at, mode=args.mode,
                symbol=c.symbol, name=c.name, strategy=sid,
                candle_count=len(candles), trades=len(sid_trades),
                pnl=pnl_f, win_rate=wr, score=c.score, flu_rate=c.flu_rate,
            ))
        print(
            f"  {c.symbol} {c.name:<16} candles={len(candles):>4} "
            f"trades={len(result.trades):>2}  PnL={sym_pnl:>+12,.0f}"
        )

    print(f"\n== 통합 결과 ==")
    print(f"  총 거래   : {total_trades} 건")
    print(f"  총 PnL    : {total_pnl:+,.0f} 원")
    print(f"  전략별 합산:")
    for sid in strategies:
        print(f"    {sid:<25s}: {per_strategy_pnl.get(sid, 0):+,.0f}")

    notifier = TelegramNotifier.from_env() if args.telegram else None
    if notifier:
        try:
            await notifier.send(format_simulation_summary(
                total_trades=total_trades, total_pnl=total_pnl,
                n_leaders=len(leaders), mode=args.mode,
            ))
        except Exception as e:
            print(f"⚠️ telegram 전송 실패: {e}")

    if args.log and log_entries:
        logger = SimulationLogger(args.log)
        n = logger.append(log_entries)
        total = len(logger.read_all())
        print(f"\n📝 {n}개 entry → {args.log} 영속화 (누적 {total} rows)")

    gate_result = None
    if args.check_balance or args.execute:
        print("\n== 잔고 기반 자금 한도 + 추천 매수 qty (BAR-OPS-16) ==")
        account = KiwoomNativeAccountFetcher(oauth=oauth)
        deposit = await account.fetch_deposit()
        balance = await account.fetch_balance()

        # ── 필터링을 자금 배분 **전에** 적용 (예산 낭비 방지) ──────────
        # 당일 SL 종목
        today_sl_symbols: set[str] = set()
        try:
            import csv as _csv
            audit_path = Path(args.audit_log)
            if audit_path.exists():
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                with audit_path.open(newline="", encoding="utf-8") as f:
                    for row in _csv.DictReader(f):
                        if row.get("ts", "").startswith(today) and row.get("action") == "ORDERED" \
                                and row.get("side") == "sell":
                            today_sl_symbols.add(row["symbol"])
        except Exception:
            pass

        # 이미 보유 중인 종목
        already_held: set[str] = set()
        try:
            already_held = {h.symbol for h in (balance.holdings or [])}
        except Exception:
            pass

        filtered_leaders: list[LeaderCandidate] = []
        for c in leaders:
            if c.flu_rate >= 25.0:
                print(f"  [PRE-SKIP] {c.symbol} {c.name:<16} 상한가 근접 ({c.flu_rate:+.1f}%)")
                continue
            if c.cur_price < 5_000:
                print(f"  [PRE-SKIP] {c.symbol} {c.name:<16} 저가주 ({c.cur_price:,.0f}원)")
                continue
            if c.symbol in today_sl_symbols:
                print(f"  [PRE-SKIP] {c.symbol} {c.name:<16} 당일 SL 발동 종목")
                continue
            if c.symbol in already_held:
                print(f"  [PRE-SKIP] {c.symbol} {c.name:<16} 이미 보유 중")
                continue
            filtered_leaders.append(c)

        if already_held:
            print(f"  ℹ️ 이미 보유 중: {', '.join(already_held)}")

        candidates_for_gate = [
            (c.symbol, c.name, Decimal(str(c.cur_price))) for c in filtered_leaders
        ]
        gate_result = evaluate_risk_gate(
            deposit=deposit, balance=balance,
            candidates=candidates_for_gate,
            max_per_position_ratio=Decimal(str(args.max_per_position)),
            max_total_position_ratio=Decimal(str(args.max_total_position)),
        )
        print(f"  예수금         : {gate_result.cash:>15,.0f}")
        print(f"  현재 평가금액   : {gate_result.current_eval:>15,.0f}")
        print(f"  진입 가능액     : {gate_result.available:>15,.0f}")
        print(f"  종목당 한도    : {gate_result.max_per_position:>15,.0f}")
        print(f"  총 보유 한도   : {gate_result.max_total_position:>15,.0f}")
        print()
        print(f"  {'symbol':<8} {'name':<16} {'price':>10} {'rec_qty':>8} {'value':>14}  비고")
        for r in gate_result.recommendations:
            value = Decimal(r.recommended_qty) * r.cur_price
            tag = r.reason if r.blocked else "✅"
            print(
                f"  {r.symbol:<8} {r.name:<16} {r.cur_price:>10,.0f} "
                f"{r.recommended_qty:>8} {value:>+14,.0f}  {tag}"
            )

    if args.execute and gate_result:
        pos_store = ActivePositionStore(args.pos_log)

        print(f"\n== 주문 실행 (BAR-OPS-17 LiveOrderGate, dry_run={args.dry_run}, 1분할 50%) ==")
        executor = KiwoomNativeOrderExecutor(oauth=oauth, dry_run=args.dry_run)
        gate = LiveOrderGate(
            executor=executor,
            audit_path=args.audit_log,
            policy=GatePolicy(
                daily_loss_limit_pct=Decimal(str(args.daily_loss_limit)),
                daily_max_orders=args.daily_max_orders,
            ),
            notifier=notifier,
        )
        executed = 0
        for r in gate_result.recommendations:
            if r.blocked or r.recommended_qty <= 0:
                continue

            # 1분할(50%) 수량 계산
            tranche1_qty = max(1, round(r.recommended_qty * 0.5))

            try:
                result = await gate.place_buy(symbol=r.symbol, qty=tranche1_qty)
                executed += 1
                tag = "DRY_RUN" if result.dry_run else "ORDERED"
                print(
                    f"  [{tag}] {r.symbol} {r.name:<16} qty={tranche1_qty:>5}"
                    f"(1/3분할, 전체 {r.recommended_qty}) order_no={result.order_no}"
                )

                # active_positions 저장 (전략 정보 + 분할 계획)
                best_strategy = max(
                    per_strategy_pnl,
                    key=lambda s: per_strategy_pnl.get(s, 0.0),
                ) if per_strategy_pnl else "swing_38"
                leader = next((c for c in filtered_leaders if c.symbol == r.symbol), None)
                pos_store.create_from_order(
                    symbol=r.symbol,
                    name=r.name,
                    strategy=best_strategy,
                    entry_price=float(r.cur_price),
                    total_recommended_qty=r.recommended_qty,
                    order_no=result.order_no,
                    sl_pct=args.sl,
                    flu_rate=float(leader.flu_rate) if leader else 0.0,
                    score=float(leader.score) if leader else 0.0,
                )

                if notifier:
                    try:
                        await notifier.send(format_buy_alert(
                            r.symbol, r.name, tranche1_qty,
                            result.order_no, result.dry_run,
                        ))
                    except Exception as te:
                        print(f"    ⚠️ telegram 알림 실패: {te}")
            except Exception as e:
                print(f"  [BLOCKED] {r.symbol} {r.name:<16}: {type(e).__name__}: {e}")
        print(f"\n  → 실행 {executed} 건 / audit log: {args.audit_log}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="당일 주도주 자동 선정 시뮬 (BAR-OPS-11)")
    ap.add_argument("--top", type=int, default=5, help="주도주 top N (기본 5)")
    ap.add_argument(
        "--mode", choices=["daily", "minute"], default="daily",
        help="캔들 모드 (기본 daily)",
    )
    ap.add_argument("--tic-scope", default="1", help="minute 분 단위 (1/3/5/10/15/30/45/60)")
    ap.add_argument(
        "--min-flu", type=float, default=1.0,
        help="최소 등락률 필터 %% (기본 1.0)",
    )
    ap.add_argument(
        "--min-score", type=float, default=0.0,
        help="최소 절대 점수 threshold 0~1 (기본 0.0, 강한 시그널만은 0.7+)",
    )
    ap.add_argument(
        "--strategies",
        default="f_zone,sf_zone,gold_zone,swing_38,scalping_consensus",
        help="실행 전략 (comma-separated)",
    )
    ap.add_argument(
        "--log",
        help="시뮬 결과 CSV 영속화 경로 (예: data/simulation_log.csv)",
    )
    ap.add_argument(
        "--check-balance", action="store_true",
        help="잔고 조회 + 자금 한도 정책 + 추천 매수 qty 표시 (BAR-OPS-16)",
    )
    ap.add_argument(
        "--max-per-position", type=float, default=0.30,
        help="종목당 최대 비중 (기본 0.30 = 30%%)",
    )
    ap.add_argument(
        "--max-total-position", type=float, default=0.90,
        help="총 보유 최대 비중 (기본 0.90 = 90%%)",
    )
    ap.add_argument(
        "--execute", action="store_true",
        help="추천 qty 그대로 LiveOrderGate 실 주문 (BAR-OPS-17/18)",
    )
    ap.add_argument(
        "--dry-run", action="store_true", default=True,
        help="DRY_RUN 모드 (기본 True). 실 주문 시 --no-dry-run 명시 필요",
    )
    ap.add_argument(
        "--no-dry-run", action="store_false", dest="dry_run",
        help="실 주문 활성화 (LIVE_TRADING_ENABLED 환경변수 필요)",
    )
    ap.add_argument(
        "--audit-log", default="data/order_audit.csv",
        help="주문 감사 CSV 경로 (기본 data/order_audit.csv)",
    )
    ap.add_argument(
        "--pos-log", default="data/active_positions.json",
        help="활성 포지션 메타 경로 (기본 data/active_positions.json)",
    )
    ap.add_argument(
        "--sl", type=float, default=-4.0,
        help="분할매수 SL 기준 %% (기본 -4.0)",
    )
    ap.add_argument(
        "--daily-loss-limit", type=float, default=-3.0,
        help="일일 손실 한도 %% (기본 -3.0)",
    )
    ap.add_argument(
        "--daily-max-orders", type=int, default=50,
        help="일일 거래수 한도 (기본 50)",
    )
    ap.add_argument(
        "--telegram", action="store_true",
        help="Telegram 알림 전송 (TELEGRAM_BOT_TOKEN/CHAT_ID 환경변수 필요)",
    )
    args = ap.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
