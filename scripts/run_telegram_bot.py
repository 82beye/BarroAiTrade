"""BAR-OPS-24 — 텔레그램 봇 데몬.

사용:
    python scripts/run_telegram_bot.py

환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
         KIWOOM_APP_KEY, KIWOOM_APP_SECRET, KIWOOM_BASE_URL.

명령:
  /help     - 사용 가능 명령
  /balance  - 키움 모의 계좌 예수금 + 보유 종목 수
  /history  - CSV 누적 시뮬 history (전략별 PnL)
  /ping     - 봇 동작 확인
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import SecretStr

from decimal import Decimal

from backend.core.gateway.kiwoom_native_account import KiwoomNativeAccountFetcher
from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.gateway.kiwoom_native_orders import KiwoomNativeOrderExecutor
from backend.core.gateway.kiwoom_native_rank import KiwoomNativeLeaderPicker
from backend.core.journal.simulation_log import (
    SimulationLogger,
    summarize_by_strategy,
)
from backend.core.notify.order_confirm import OrderConfirmStore, PendingOrder
from backend.core.notify.telegram import TelegramNotifier
from backend.core.notify.telegram_bot import TelegramBot
from backend.core.risk.balance_gate import evaluate_risk_gate
from backend.core.risk.holding_evaluator import ExitPolicy, evaluate_all
from backend.core.risk.live_order_gate import GatePolicy, LiveOrderGate

_LOG_PATH = "data/simulation_log.csv"
_AUDIT_PATH = "data/order_audit.csv"
_CONFIRM_STORE = OrderConfirmStore(ttl_seconds=300)            # 5분 TTL


def _build_oauth() -> KiwoomNativeOAuth:
    return KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )


async def _cmd_help(bot: TelegramBot, msg: dict) -> str:
    return (
        "*BarroAiTrade 봇 명령*\n"
        "/balance - 잔고/예수금/보유 종목 수\n"
        "/history - 시뮬 누적 (전략별 PnL)\n"
        "/sim - 당일 주도주 top 3 + 추천 qty\n"
        "/eval - 보유 종목 매도 시그널 (TP +5% / SL -2%)\n"
        "/audit - 최근 audit log 5건\n"
        "/ping - 봇 동작 확인\n"
        "/help - 이 메시지"
    )


async def _cmd_ping(bot: TelegramBot, msg: dict) -> str:
    return "pong 🏓"


async def _cmd_balance(bot: TelegramBot, msg: dict) -> str:
    oauth = _build_oauth()
    account = KiwoomNativeAccountFetcher(oauth=oauth)
    deposit = await account.fetch_deposit()
    balance = await account.fetch_balance()
    return (
        f"💰 *잔고*\n"
        f"예수금: {int(deposit.cash):,} 원\n"
        f"평가금액: {int(balance.total_eval):,} 원\n"
        f"평가손익: *{int(balance.total_pnl):+,}* ({float(balance.total_pnl_rate):+.2f}%)\n"
        f"보유 종목: {len(balance.holdings)} 개"
    )


async def _cmd_sim(bot: TelegramBot, msg: dict) -> str:
    """당일 주도주 top 3 선정 + 잔고 기반 추천 qty."""
    oauth = _build_oauth()
    picker = KiwoomNativeLeaderPicker(oauth=oauth, min_score=0.5)
    leaders = await picker.pick(top_n=3)
    if not leaders:
        return "주도주 후보 없음"
    account = KiwoomNativeAccountFetcher(oauth=oauth)
    deposit = await account.fetch_deposit()
    balance = await account.fetch_balance()
    gate = evaluate_risk_gate(
        deposit=deposit, balance=balance,
        candidates=[(c.symbol, c.name, Decimal(str(c.cur_price))) for c in leaders],
    )
    lines = [f"📈 *주도주 top {len(leaders)}* (자금 {int(gate.cash):,})"]
    for c, r in zip(leaders, gate.recommendations):
        tag = "✅" if not r.blocked else "🚫"
        lines.append(
            f"`{c.symbol}` {c.name} {c.flu_rate:+.2f}% "
            f"→ {r.recommended_qty}주 {tag}"
        )
    return "\n".join(lines)


async def _cmd_eval(bot: TelegramBot, msg: dict) -> str:
    """보유 종목 매도 시그널 평가."""
    oauth = _build_oauth()
    account = KiwoomNativeAccountFetcher(oauth=oauth)
    balance = await account.fetch_balance()
    if not balance.holdings:
        return "보유 종목 없음"
    decisions = evaluate_all(balance.holdings, ExitPolicy())
    lines = [f"📋 *보유 평가* ({len(decisions)} 종목, TP +5% / SL -2%)"]
    for d in decisions:
        sig = {"hold": "🔵 HOLD", "take_profit": "✅ TP", "stop_loss": "🛑 SL"}[d.signal.value]
        lines.append(f"`{d.symbol}` {d.name} {float(d.pnl_rate):+.2f}% {sig}")
    return "\n".join(lines)


async def _cmd_sim_execute(bot: TelegramBot, msg: dict) -> str:
    """주도주 시뮬 + 자금 정책 → 토큰 발급 (5분 TTL) — BAR-OPS-26."""
    chat_id = str(msg.get("chat", {}).get("id", ""))
    oauth = _build_oauth()
    picker = KiwoomNativeLeaderPicker(oauth=oauth, min_score=0.5)
    leaders = await picker.pick(top_n=3)
    if not leaders:
        return "주도주 후보 없음 — 발급 X"
    account = KiwoomNativeAccountFetcher(oauth=oauth)
    deposit = await account.fetch_deposit()
    balance = await account.fetch_balance()
    gate = evaluate_risk_gate(
        deposit=deposit, balance=balance,
        candidates=[(c.symbol, c.name, Decimal(str(c.cur_price))) for c in leaders],
    )
    pending = [
        PendingOrder(symbol=r.symbol, name=r.name, qty=r.recommended_qty)
        for r in gate.recommendations if not r.blocked and r.recommended_qty > 0
    ]
    if not pending:
        return "추천 매수 종목 없음 (자금 부족 또는 한도) — 발급 X"
    batch = _CONFIRM_STORE.issue(chat_id=chat_id, orders=pending)
    lines = [
        f"🔐 *매수 토큰 발급* (TTL 5분)",
        f"토큰: `{batch.token}`",
        "",
        "*예정 주문*",
    ]
    for o in pending:
        lines.append(f"`{o.symbol}` {o.name} → {o.qty}주")
    lines.append("")
    lines.append(f"확인: `/confirm {batch.token}`  /  취소: `/cancel`")
    return "\n".join(lines)


async def _cmd_confirm(bot: TelegramBot, msg: dict) -> str:
    """token 검증 → 매수 실행 — BAR-OPS-26."""
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").strip()
    parts = text.split()
    if len(parts) < 2:
        return "사용법: `/confirm <TOKEN>`"
    batch = _CONFIRM_STORE.consume(chat_id=chat_id, token=parts[1])
    if not batch:
        return "❌ 토큰 무효/만료/이미 사용됨"

    oauth = _build_oauth()
    dry_run = os.environ.get("LIVE_TRADING_ENABLED", "").lower() not in {"1", "true", "yes", "on"}
    executor = KiwoomNativeOrderExecutor(oauth=oauth, dry_run=dry_run)
    gate = LiveOrderGate(executor=executor, audit_path=_AUDIT_PATH, policy=GatePolicy())
    lines = [f"🚀 *매수 실행* (dry\\_run={dry_run})"]
    for o in batch.orders:
        try:
            r = await gate.place_buy(symbol=o.symbol, qty=o.qty)
            tag = "🧪 DRY_RUN" if r.dry_run else "✅ ORDERED"
            lines.append(f"{tag} `{o.symbol}` qty={o.qty}")
        except Exception as e:
            lines.append(f"❌ `{o.symbol}` {type(e).__name__}: {str(e)[:80]}")
    return "\n".join(lines)


async def _cmd_cancel(bot: TelegramBot, msg: dict) -> str:
    """발급 토큰 폐기 — BAR-OPS-26."""
    chat_id = str(msg.get("chat", {}).get("id", ""))
    if _CONFIRM_STORE.cancel(chat_id=chat_id):
        return "🗑️ 토큰 폐기됨"
    return "발급된 토큰 없음"


async def _cmd_sell_execute(bot: TelegramBot, msg: dict) -> str:
    """보유 종목 TP/SL 평가 → 매도 토큰 발급 — BAR-OPS-27."""
    chat_id = str(msg.get("chat", {}).get("id", ""))
    oauth = _build_oauth()
    account = KiwoomNativeAccountFetcher(oauth=oauth)
    balance = await account.fetch_balance()
    if not balance.holdings:
        return "보유 종목 없음 — 발급 X"
    decisions = evaluate_all(balance.holdings, ExitPolicy())
    targets = [d for d in decisions if d.signal.value != "hold"]
    if not targets:
        return "TP/SL 도달 종목 없음 (모두 HOLD) — 발급 X"
    pending = [
        PendingOrder(symbol=d.symbol, name=d.name, qty=d.qty, side="sell")
        for d in targets
    ]
    batch = _CONFIRM_STORE.issue(chat_id=chat_id, orders=pending)
    lines = [
        f"🔐 *매도 토큰 발급* (TTL 5분)",
        f"토큰: `{batch.token}`",
        "",
        "*예정 매도*",
    ]
    for d in targets:
        sig = "✅ TP" if d.signal.value == "take_profit" else "🛑 SL"
        lines.append(
            f"`{d.symbol}` {d.name} {float(d.pnl_rate):+.2f}% qty={d.qty} {sig}"
        )
    lines.append("")
    lines.append(f"확인: `/confirm_sell {batch.token}`  /  취소: `/cancel`")
    return "\n".join(lines)


async def _cmd_confirm_sell(bot: TelegramBot, msg: dict) -> str:
    """token 검증 → 매도 실행 — BAR-OPS-27."""
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").strip()
    parts = text.split()
    if len(parts) < 2:
        return "사용법: `/confirm_sell <TOKEN>`"
    batch = _CONFIRM_STORE.consume(chat_id=chat_id, token=parts[1])
    if not batch:
        return "❌ 토큰 무효/만료/이미 사용됨"
    if not all(o.side == "sell" for o in batch.orders):
        return "❌ 매도 토큰 아님 (`/sell_execute` 로 발급된 토큰만 사용)"

    oauth = _build_oauth()
    dry_run = os.environ.get("LIVE_TRADING_ENABLED", "").lower() not in {"1", "true", "yes", "on"}
    executor = KiwoomNativeOrderExecutor(oauth=oauth, dry_run=dry_run)
    gate = LiveOrderGate(executor=executor, audit_path=_AUDIT_PATH, policy=GatePolicy())
    lines = [f"🚀 *매도 실행* (dry\\_run={dry_run})"]
    for o in batch.orders:
        try:
            r = await gate.place_sell(symbol=o.symbol, qty=o.qty)
            tag = "🧪 DRY_RUN" if r.dry_run else "✅ ORDERED"
            lines.append(f"{tag} `{o.symbol}` qty={o.qty}")
        except Exception as e:
            lines.append(f"❌ `{o.symbol}` {type(e).__name__}: {str(e)[:80]}")
    return "\n".join(lines)


async def _cmd_diff(bot: TelegramBot, msg: dict) -> str:
    """시뮬(예측) PnL vs 실현 PnL 비교 — BAR-OPS-29."""
    from datetime import date, timedelta
    from backend.core.journal.pnl_diff import compare, summarize
    sim_entries = SimulationLogger(_LOG_PATH).read_all()
    if not sim_entries:
        return f"시뮬 누적 없음 ({_LOG_PATH})"
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    oauth = _build_oauth()
    account = KiwoomNativeAccountFetcher(oauth=oauth)
    real_entries = await account.fetch_realized_pnl(start_date=start, end_date=end)
    diffs = compare(sim_entries=sim_entries, real_entries=real_entries)
    summary = summarize(diffs)
    lines = [
        f"🔍 *시뮬 vs 실현* ({summary['n_symbols']} 종목)",
        f"시뮬 합계: *{int(summary['total_sim']):+,}*",
        f"실현 합계: *{int(summary['total_real']):+,}*",
        f"차이:     *{int(summary['total_diff']):+,}*",
    ]
    bias_str = " / ".join(f"{k}: {v}" for k, v in summary["bias_counts"].items())
    if bias_str:
        lines.append(f"_{bias_str}_")
    lines.append("")
    lines.append("*차이 큰 5종목*")
    for d in diffs[:5]:
        pct_str = f"{float(d.diff_pct):+.0f}%" if d.diff_pct is not None else "-"
        lines.append(
            f"`{d.symbol}` {d.name[:8]} sim={int(d.sim_pnl):+,} "
            f"real={int(d.real_pnl):+,} ({pct_str}) {d.bias}"
        )
    return "\n".join(lines)


async def _cmd_pnl(bot: TelegramBot, msg: dict) -> str:
    """최근 30일 실현손익 (ka10073) — BAR-OPS-28."""
    from datetime import date, timedelta
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=30)).strftime("%Y%m%d")
    oauth = _build_oauth()
    account = KiwoomNativeAccountFetcher(oauth=oauth)
    rows = await account.fetch_realized_pnl(start_date=start, end_date=end)
    if not rows:
        return f"실현손익 없음 ({start}~{end})"
    total_pnl = sum((r.pnl for r in rows), Decimal("0"))
    total_cmsn = sum((r.commission for r in rows), Decimal("0"))
    total_tax = sum((r.tax for r in rows), Decimal("0"))
    win = sum(1 for r in rows if r.pnl > 0)
    win_rate = (win / len(rows)) * 100 if rows else 0.0
    lines = [
        f"💵 *실현손익* ({start[4:6]}/{start[6:8]} ~ {end[4:6]}/{end[6:8]})",
        f"거래: {len(rows)}건 / 승률: {win_rate:.1f}%",
        f"순손익: *{int(total_pnl):+,}* 원",
        f"수수료/세금: {int(total_cmsn):,} / {int(total_tax):,}",
        "",
        "*최근 5건*",
    ]
    for r in rows[-5:]:
        sig = "✅" if r.pnl > 0 else ("🛑" if r.pnl < 0 else "⚪")
        lines.append(
            f"`{r.date}` {r.name} qty={r.qty} "
            f"{int(r.pnl):+,} ({float(r.pnl_rate):+.2f}%) {sig}"
        )
    return "\n".join(lines)


async def _cmd_audit(bot: TelegramBot, msg: dict) -> str:
    """최근 audit log 5건."""
    p = Path(_AUDIT_PATH)
    if not p.exists():
        return f"audit 로그 없음 ({_AUDIT_PATH})"
    import csv
    with open(p, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return "audit 비어있음"
    recent = rows[-5:]
    lines = [f"📝 *최근 audit {len(recent)}/{len(rows)} 건*"]
    for r in recent:
        ts = r["ts"][11:19]    # HH:MM:SS
        lines.append(
            f"`{ts}` {r['action']} {r['side']} {r['symbol']} qty={r['qty']}"
        )
    return "\n".join(lines)


async def _cmd_history(bot: TelegramBot, msg: dict) -> str:
    entries = SimulationLogger(_LOG_PATH).read_all()
    if not entries:
        return f"누적 history 없음 ({_LOG_PATH})"
    summary = summarize_by_strategy(entries)
    lines = [f"📊 *시뮬 누적* ({len(entries)} entries)"]
    rows = sorted(summary.items(), key=lambda x: -x[1]["total_pnl"])
    for sid, s in rows[:10]:
        lines.append(
            f"`{sid}`: {s['runs']} runs / "
            f"{s['total_trades']} trades / {int(s['total_pnl']):+,}"
        )
    return "\n".join(lines)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수 필요")
    if not os.environ.get("KIWOOM_APP_KEY"):
        raise SystemExit("KIWOOM_APP_KEY / SECRET 환경변수 필요")

    notifier = TelegramNotifier(bot_token=SecretStr(token), chat_id=chat_id)
    bot = TelegramBot(
        bot_token=SecretStr(token),
        notifier=notifier,
        allowed_chat_ids=[chat_id],     # 본인 chat 만
    )
    bot.register("/help", _cmd_help)
    bot.register("/ping", _cmd_ping)
    bot.register("/balance", _cmd_balance)
    bot.register("/history", _cmd_history)
    bot.register("/sim", _cmd_sim)                # OPS-25
    bot.register("/eval", _cmd_eval)              # OPS-25
    bot.register("/audit", _cmd_audit)            # OPS-25
    bot.register("/sim_execute", _cmd_sim_execute) # OPS-26
    bot.register("/confirm", _cmd_confirm)         # OPS-26
    bot.register("/cancel", _cmd_cancel)           # OPS-26
    bot.register("/sell_execute", _cmd_sell_execute)   # OPS-27
    bot.register("/confirm_sell", _cmd_confirm_sell)   # OPS-27
    bot.register("/pnl", _cmd_pnl)                     # OPS-28
    bot.register("/diff", _cmd_diff)                   # OPS-29

    print(f"🤖 봇 시작 — chat_id={chat_id}, 명령={list(bot._handlers)}")
    print("   Ctrl+C 로 종료")
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\n봇 종료")


if __name__ == "__main__":
    main()
