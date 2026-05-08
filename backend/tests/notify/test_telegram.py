"""BAR-OPS-21 — TelegramNotifier 테스트."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from backend.core.notify.telegram import (
    TelegramNotifier,
    format_blocked_alert,
    format_buy_alert,
    format_sell_alert,
    format_simulation_summary,
)


def _http_response(status: int, payload: dict) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


# -- validation -------------------------------------------------------------


def test_token_must_be_secretstr():
    with pytest.raises(TypeError, match="SecretStr"):
        TelegramNotifier(bot_token="plain", chat_id="123")  # type: ignore


def test_chat_id_required():
    with pytest.raises(ValueError, match="chat_id"):
        TelegramNotifier(bot_token=SecretStr("t"), chat_id="")


def test_invalid_parse_mode():
    with pytest.raises(ValueError, match="parse_mode"):
        TelegramNotifier(bot_token=SecretStr("t"), chat_id="1", parse_mode="rst")


def test_from_env_missing_raises(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(SystemExit, match="TELEGRAM"):
        TelegramNotifier.from_env()


def test_from_env_with_values(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc:def")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    n = TelegramNotifier.from_env()
    assert n._chat_id == "123"


# -- send ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_empty_text_raises():
    n = TelegramNotifier(bot_token=SecretStr("t"), chat_id="1")
    with pytest.raises(ValueError, match="text required"):
        await n.send("")


@pytest.mark.asyncio
async def test_send_calls_correct_url_and_body():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response(200, {"ok": True, "result": {"message_id": 42}}))
    n = TelegramNotifier(
        bot_token=SecretStr("BOTTOK"), chat_id="9999",
        http_client=http,
    )
    result = await n.send("hello")
    assert result == {"message_id": 42}
    call = http.post.call_args
    # URL: /botBOTTOK/sendMessage
    assert "/botBOTTOK/sendMessage" in call.args[0]
    body = call.kwargs["json"]
    assert body["chat_id"] == "9999"
    assert body["text"] == "hello"
    assert body["parse_mode"] == "Markdown"


@pytest.mark.asyncio
async def test_send_truncates_long_text():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response(200, {"ok": True, "result": {}}))
    n = TelegramNotifier(bot_token=SecretStr("t"), chat_id="1", http_client=http)
    huge = "x" * 5000
    await n.send(huge)
    sent = http.post.call_args.kwargs["json"]["text"]
    assert len(sent) <= 4096
    assert "(truncated)" in sent


@pytest.mark.asyncio
async def test_send_telegram_error():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response(200, {
        "ok": False, "error_code": 400, "description": "Bad Request: chat not found"
    }))
    n = TelegramNotifier(bot_token=SecretStr("t"), chat_id="1", http_client=http)
    with pytest.raises(RuntimeError, match="code=400"):
        await n.send("hi")


# -- formatters ------------------------------------------------------------


def test_format_buy_alert_dry_run():
    s = format_buy_alert("005930", "삼성전자", 10, "DRY_RUN", dry_run=True)
    assert "DRY_RUN" in s
    assert "005930" in s
    assert "삼성전자" in s
    assert "10주" in s


def test_format_sell_alert_tp():
    s = format_sell_alert("005930", "삼성전자", 10, "take_profit", 6.35, "0001234", dry_run=False)
    assert "✅ TP" in s
    assert "+6.35%" in s


def test_format_sell_alert_sl():
    s = format_sell_alert("005930", "삼성전자", 10, "stop_loss", -3.5, "0001234", dry_run=False)
    assert "🛑 SL" in s
    assert "-3.50%" in s


def test_format_simulation_summary():
    s = format_simulation_summary(total_trades=110, total_pnl=7458231.0, n_leaders=5, mode="daily")
    assert "📊" in s
    assert "+7,458,231" in s
    assert "5" in s


def test_format_blocked_alert():
    s = format_blocked_alert("buy", "005930", "LIVE_TRADING_ENABLED 미설정")
    assert "차단" in s
    # Markdown escape 적용 — `_` → `\_`
    assert "LIVE\\_TRADING\\_ENABLED" in s


def test_format_blocked_alert_truncates_long_reason():
    long_reason = "x" * 300
    s = format_blocked_alert("buy", "005930", long_reason)
    assert "..." in s
    # 헤더 + truncated reason → 전체 길이 제한
    assert len(s) < 300
