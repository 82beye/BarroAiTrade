"""BAR-OPS-24 — TelegramBot 테스트."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from backend.core.notify.telegram import TelegramNotifier
from backend.core.notify.telegram_bot import TelegramBot


def _http_response(payload: dict) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = 200
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


def _notifier_mock() -> AsyncMock:
    n = AsyncMock(spec=TelegramNotifier)
    n.send = AsyncMock(return_value={"message_id": 1})
    return n


def _make(http=None) -> TelegramBot:
    return TelegramBot(
        bot_token=SecretStr("BOTTOK"),
        notifier=_notifier_mock(),
        allowed_chat_ids=["123", "456"],
        http_client=http or AsyncMock(spec=httpx.AsyncClient),
        poll_timeout=1,
    )


def _update(chat_id: str | int, text: str, update_id: int = 1) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "text": text,
        },
    }


# -- validation -------------------------------------------------------------


def test_token_must_be_secretstr():
    with pytest.raises(TypeError, match="SecretStr"):
        TelegramBot(
            bot_token="plain",  # type: ignore
            notifier=_notifier_mock(), allowed_chat_ids=["1"],
        )


def test_whitelist_required():
    with pytest.raises(ValueError, match="allowed_chat_ids"):
        TelegramBot(
            bot_token=SecretStr("t"), notifier=_notifier_mock(),
            allowed_chat_ids=[],
        )


def test_register_command_prefix_required():
    bot = _make()
    with pytest.raises(ValueError, match="must start with '/'"):
        bot.register("balance", lambda b, m: None)  # type: ignore


# -- whitelist enforcement -------------------------------------------------


@pytest.mark.asyncio
async def test_unauthorized_chat_ignored():
    bot = _make()
    handler = AsyncMock(return_value="OK")
    bot.register("/test", handler)
    reply = await bot.handle_update(_update(chat_id="999", text="/test"))
    assert reply is None
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_authorized_chat_dispatches():
    bot = _make()
    handler = AsyncMock(return_value="✅ ok")
    bot.register("/balance", handler)
    reply = await bot.handle_update(_update(chat_id="123", text="/balance"))
    assert reply == "✅ ok"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_command_returns_none():
    bot = _make()
    bot.register("/balance", AsyncMock(return_value="ok"))
    reply = await bot.handle_update(_update(chat_id="123", text="/unknown"))
    assert reply is None


@pytest.mark.asyncio
async def test_command_with_args_uses_first_token():
    bot = _make()
    handler = AsyncMock(return_value="ok")
    bot.register("/sim", handler)
    reply = await bot.handle_update(_update(chat_id="123", text="/sim --top 5"))
    assert reply == "ok"


@pytest.mark.asyncio
async def test_handler_error_returns_failure_message():
    bot = _make()
    bot.register("/x", AsyncMock(side_effect=RuntimeError("boom")))
    reply = await bot.handle_update(_update(chat_id="123", text="/x"))
    assert "실행 실패" in reply
    assert "boom" in reply


# -- poll loop -------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_once_advances_offset_and_notifies():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.get = AsyncMock(return_value=_http_response({
        "ok": True,
        "result": [
            _update(chat_id="123", text="/balance", update_id=10),
            _update(chat_id="999", text="/balance", update_id=11),     # 차단
        ],
    }))
    bot = _make(http=http)
    handler = AsyncMock(return_value="잔고: 100원")
    bot.register("/balance", handler)
    n = await bot.poll_once()
    assert n == 1                         # whitelist 통과 1건
    assert bot._offset == 12              # 마지막 + 1
    bot._notifier.send.assert_awaited_once_with("잔고: 100원")


@pytest.mark.asyncio
async def test_poll_once_no_updates():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.get = AsyncMock(return_value=_http_response({"ok": True, "result": []}))
    bot = _make(http=http)
    n = await bot.poll_once()
    assert n == 0


@pytest.mark.asyncio
async def test_get_updates_error_raises():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.get = AsyncMock(return_value=_http_response({"ok": False, "description": "x"}))
    bot = _make(http=http)
    with pytest.raises(RuntimeError, match="getUpdates"):
        await bot.get_updates()
