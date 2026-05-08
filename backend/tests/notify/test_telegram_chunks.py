"""BAR-OPS-23 — TelegramNotifier.send_chunks 테스트."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from backend.core.notify.telegram import TelegramNotifier


def _http_response(payload: dict) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = 200
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


def _make(http) -> TelegramNotifier:
    return TelegramNotifier(
        bot_token=SecretStr("t"), chat_id="1", http_client=http,
    )


@pytest.mark.asyncio
async def test_send_chunks_short_text_single_call():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response({"ok": True, "result": {"message_id": 1}}))
    n = _make(http)
    results = await n.send_chunks("hello world")
    assert len(results) == 1
    # 단일 chunk → "part" prefix 없음
    assert "part" not in http.post.call_args.kwargs["json"]["text"]


@pytest.mark.asyncio
async def test_send_chunks_splits_long_text_by_lines():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response({"ok": True, "result": {"message_id": 1}}))
    n = _make(http)
    # 100 줄 × 100 char ≈ 10,100 char
    text = "\n".join("X" * 100 for _ in range(100))
    results = await n.send_chunks(text, chunk_size=3000)
    # 약 4 chunks
    assert len(results) >= 3
    # 모두 part prefix
    for call in http.post.call_args_list:
        assert "_part " in call.kwargs["json"]["text"]


@pytest.mark.asyncio
async def test_send_chunks_handles_oversized_single_line():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response({"ok": True, "result": {}}))
    n = _make(http)
    # 한 줄이 chunk_size 초과 → 강제 분할
    text = "X" * 5000
    results = await n.send_chunks(text, chunk_size=2000)
    assert len(results) >= 3
    # 각 chunk 길이 ≤ chunk_size + part prefix
    for call in http.post.call_args_list:
        sent = call.kwargs["json"]["text"]
        body_part = sent.split("_\n", 1)[-1]   # part prefix 제거
        assert len(body_part) <= 2010


@pytest.mark.asyncio
async def test_send_chunks_empty_raises():
    n = _make(AsyncMock(spec=httpx.AsyncClient))
    with pytest.raises(ValueError, match="text required"):
        await n.send_chunks("")
