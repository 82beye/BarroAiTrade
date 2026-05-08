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
from backend.core.gateway.kiwoom_native_rank import KiwoomNativeLeaderPicker
from backend.core.journal.simulation_log import (
    SimulationLogger,
    summarize_by_strategy,
)
from backend.core.notify.telegram import TelegramNotifier
from backend.core.notify.telegram_bot import TelegramBot
from backend.core.risk.balance_gate import evaluate_risk_gate
from backend.core.risk.holding_evaluator import ExitPolicy, evaluate_all

_LOG_PATH = "data/simulation_log.csv"
_AUDIT_PATH = "data/order_audit.csv"


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

    print(f"🤖 봇 시작 — chat_id={chat_id}, 명령={list(bot._handlers)}")
    print("   Ctrl+C 로 종료")
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\n봇 종료")


if __name__ == "__main__":
    main()
