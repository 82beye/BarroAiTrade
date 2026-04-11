"""
Telegram 알림 봇 — 매매 신호 및 시스템 이벤트 알림

환경변수:
  TELEGRAM_BOT_TOKEN  : BotFather에서 발급한 토큰
  TELEGRAM_CHAT_ID    : 알림 수신 채팅 ID (개인 또는 그룹)
"""
from __future__ import annotations

import asyncio
import logging
import os
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

# python-telegram-bot 옵셔널 임포트 (미설치 시 알림 비활성화)
try:
    from telegram import Bot
    from telegram.error import TelegramError
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot 미설치 — Telegram 알림 비활성화")


class AlertLevel(str, Enum):
    INFO = "ℹ️"
    SUCCESS = "✅"
    WARNING = "⚠️"
    ERROR = "🚨"
    TRADE = "💰"


class TelegramNotifier:
    """Telegram 비동기 알림 클래스"""

    def __init__(self) -> None:
        self._token: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
        self._chat_id: Optional[str] = os.getenv("TELEGRAM_CHAT_ID")
        self._bot: Optional[Any] = None
        self._enabled = bool(self._token and self._chat_id and _TELEGRAM_AVAILABLE)

        if self._enabled:
            self._bot = Bot(token=self._token)
            logger.info("Telegram 알림 봇 초기화 완료 (chat_id=%s)", self._chat_id)
        else:
            logger.info("Telegram 알림 봇 비활성화 (토큰 또는 chat_id 미설정)")

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def send(self, level: AlertLevel, title: str, body: str = "") -> None:
        """알림 전송"""
        if not self._enabled:
            return
        text = f"{level.value} *{title}*"
        if body:
            text += f"\n{body}"
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.error("Telegram 전송 실패: %s", exc)

    async def notify_entry_signal(self, symbol: str, strategy: str, price: float, qty: int) -> None:
        await self.send(
            AlertLevel.TRADE,
            f"매수 신호 — {symbol}",
            f"전략: {strategy}\n가격: {price:,.0f}원\n수량: {qty}주",
        )

    async def notify_exit_signal(self, symbol: str, pnl: float) -> None:
        icon = "📈" if pnl >= 0 else "📉"
        await self.send(
            AlertLevel.TRADE,
            f"매도 신호 — {symbol}",
            f"{icon} 손익: {pnl:+,.0f}원",
        )

    async def notify_risk_alert(self, message: str) -> None:
        await self.send(AlertLevel.WARNING, "리스크 경고", message)

    async def notify_system_start(self, mode: str, market: str) -> None:
        await self.send(
            AlertLevel.SUCCESS,
            "시스템 시작",
            f"모드: {mode}\n시장: {market}",
        )

    async def notify_system_stop(self) -> None:
        await self.send(AlertLevel.INFO, "시스템 중지", "트레이딩이 중지되었습니다.")

    async def notify_error(self, error: str) -> None:
        await self.send(AlertLevel.ERROR, "오류 발생", error)


# 싱글톤
telegram = TelegramNotifier()
