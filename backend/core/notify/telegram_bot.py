"""BAR-OPS-24 — 텔레그램 양방향 봇 (getUpdates long-polling).

운영자가 모바일 텔레그램에서 명령으로 잔고·상태 즉시 확인.

명령:
  /help     - 사용 가능 명령
  /balance  - 키움 모의 계좌 예수금 + 보유 종목
  /history  - CSV 누적 시뮬 history 요약
  /ping     - 봇 동작 확인

보안:
- chat_id whitelist 강제 (인가된 chat 외에는 무시)
- 봇 응답 자체는 TelegramNotifier 사용
- 명령 실행은 격리된 핸들러 함수 (외부 서브프로세스 X)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

import httpx
from pydantic import SecretStr

from backend.core.notify.telegram import TelegramNotifier

logger = logging.getLogger(__name__)


_API_BASE = "https://api.telegram.org/bot"

CommandHandler = Callable[["TelegramBot", dict], Awaitable[str]]


class TelegramBot:
    """getUpdates long-polling 봇."""

    def __init__(
        self,
        bot_token: SecretStr,
        notifier: TelegramNotifier,
        allowed_chat_ids: list[str] | set[str],
        http_client: Optional[httpx.AsyncClient] = None,
        poll_timeout: int = 30,
    ) -> None:
        if not isinstance(bot_token, SecretStr):
            raise TypeError("bot_token must be SecretStr (CWE-798)")
        if not allowed_chat_ids:
            raise ValueError("allowed_chat_ids required (whitelist)")
        self._token = bot_token
        self._notifier = notifier
        self._whitelist: set[str] = {str(c) for c in allowed_chat_ids}
        self._http = http_client
        self._poll_timeout = poll_timeout
        self._handlers: dict[str, CommandHandler] = {}
        self._offset = 0
        self._running = False

    def register(self, command: str, handler: CommandHandler) -> None:
        if not command.startswith("/"):
            raise ValueError(f"command must start with '/', got {command!r}")
        self._handlers[command] = handler

    async def get_updates(self) -> list[dict]:
        url = f"{_API_BASE}{self._token.get_secret_value()}/getUpdates"
        params = {"offset": self._offset, "timeout": self._poll_timeout}
        owns = self._http is None
        client = self._http or httpx.AsyncClient(timeout=self._poll_timeout + 5)
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        finally:
            if owns:
                await client.aclose()
        if not data.get("ok"):
            raise RuntimeError(f"getUpdates error: {data}")
        return data.get("result", [])

    async def handle_update(self, update: dict) -> Optional[str]:
        """update 처리. 인가된 chat 의 명령만 dispatch."""
        msg = update.get("message", {})
        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", ""))
        text = (msg.get("text") or "").strip()

        # whitelist 검증
        if chat_id not in self._whitelist:
            logger.warning("unauthorized chat: id=%s text=%s", chat_id, text[:50])
            return None

        # 명령 추출 — 첫 토큰만 사용 (`/balance arg1 arg2` 무시)
        first = text.split()[0] if text else ""
        handler = self._handlers.get(first)
        if not handler:
            return None

        try:
            return await handler(self, msg)
        except Exception as e:
            logger.error("handler %s failed: %s", first, type(e).__name__)
            return f"⚠️ 명령 실행 실패: {type(e).__name__}: {e}"

    async def poll_once(self) -> int:
        """한 사이클 — getUpdates → handle → notify. 처리 건수 반환."""
        updates = await self.get_updates()
        n = 0
        for u in updates:
            self._offset = max(self._offset, u.get("update_id", 0) + 1)
            reply = await self.handle_update(u)
            if reply:
                try:
                    await self._notifier.send(reply)
                except Exception as e:
                    logger.error("reply send failed: %s", type(e).__name__)
                n += 1
        return n

    async def run(self) -> None:
        """무한 polling. SIGINT/SIGTERM 으로만 종료."""
        self._running = True
        _backoff = 5
        _MAX_BACKOFF = 60
        logger.info("telegram bot started (whitelist=%s, handlers=%s)",
                    self._whitelist, list(self._handlers))
        while self._running:
            try:
                await self.poll_once()
                _backoff = 5  # 성공 시 리셋
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("poll cycle failed: %s — retrying in %ds", type(e).__name__, _backoff)
                await asyncio.sleep(_backoff)
                _backoff = min(_backoff * 2, _MAX_BACKOFF)

    def stop(self) -> None:
        self._running = False


__all__ = ["TelegramBot", "CommandHandler"]
