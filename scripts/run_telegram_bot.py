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

from backend.core.gateway.kiwoom_native_account import KiwoomNativeAccountFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.journal.simulation_log import (
    SimulationLogger,
    summarize_by_strategy,
)
from backend.core.notify.telegram import TelegramNotifier
from backend.core.notify.telegram_bot import TelegramBot

_LOG_PATH = "data/simulation_log.csv"


async def _cmd_help(bot: TelegramBot, msg: dict) -> str:
    return (
        "*BarroAiTrade 봇 명령*\n"
        "/balance - 잔고/예수금/보유 종목 수\n"
        "/history - 시뮬 누적 (전략별 PnL)\n"
        "/ping - 봇 동작 확인\n"
        "/help - 이 메시지"
    )


async def _cmd_ping(bot: TelegramBot, msg: dict) -> str:
    return "pong 🏓"


async def _cmd_balance(bot: TelegramBot, msg: dict) -> str:
    oauth = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )
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

    print(f"🤖 봇 시작 — chat_id={chat_id}, 명령={list(bot._handlers)}")
    print("   Ctrl+C 로 종료")
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\n봇 종료")


if __name__ == "__main__":
    main()
