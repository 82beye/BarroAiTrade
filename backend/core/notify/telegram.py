"""BAR-OPS-21 — Telegram Bot 알림.

Bot API:
  POST https://api.telegram.org/bot<TOKEN>/sendMessage
  body: {chat_id, text, parse_mode: "Markdown"|"MarkdownV2"|"HTML"}

환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

보안:
- SecretStr 강제 (CWE-798) — 토큰 평문 노출 차단
- httpx timeout 10s
- 실패 시 raise (운영 시 try/except 권장)
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from pydantic import SecretStr

logger = logging.getLogger(__name__)


_API_BASE = "https://api.telegram.org/bot"


class TelegramNotifier:
    """Telegram Bot sendMessage wrapper."""

    def __init__(
        self,
        bot_token: SecretStr,
        chat_id: str,
        http_client: Optional[httpx.AsyncClient] = None,
        parse_mode: str = "Markdown",
        disable_web_page_preview: bool = True,
        timeout: float = 10.0,
    ) -> None:
        if not isinstance(bot_token, SecretStr):
            raise TypeError("bot_token must be SecretStr (CWE-798)")
        if not chat_id:
            raise ValueError("chat_id required")
        if parse_mode not in {"Markdown", "MarkdownV2", "HTML"}:
            raise ValueError(f"invalid parse_mode: {parse_mode}")
        self._token = bot_token
        self._chat_id = chat_id
        self._http = http_client
        self._parse_mode = parse_mode
        self._no_preview = disable_web_page_preview
        self._timeout = timeout

    @classmethod
    def from_env(cls, **kw) -> "TelegramNotifier":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            raise SystemExit(
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수 필요.\n"
                "예: set -a; . ./.env.local; set +a"
            )
        return cls(bot_token=SecretStr(token), chat_id=chat_id, **kw)

    async def send_chunks(self, text: str, chunk_size: int = 3900) -> list[dict]:
        """긴 메시지 자동 분할 전송 (BAR-OPS-23).

        Telegram 4096 char 제한 → 줄 단위 분할 보존.
        한 줄이 chunk_size 초과 시 강제 분할.
        """
        if not text or not text.strip():
            raise ValueError("text required")
        chunks: list[str] = []
        cur = ""
        for line in text.split("\n"):
            if len(line) > chunk_size:
                if cur:
                    chunks.append(cur); cur = ""
                for i in range(0, len(line), chunk_size):
                    chunks.append(line[i:i + chunk_size])
                continue
            if len(cur) + len(line) + 1 > chunk_size:
                chunks.append(cur)
                cur = line
            else:
                cur = f"{cur}\n{line}" if cur else line
        if cur:
            chunks.append(cur)

        results = []
        for i, chunk in enumerate(chunks, 1):
            prefix = f"_part {i}/{len(chunks)}_\n" if len(chunks) > 1 else ""
            results.append(await self.send(prefix + chunk))
        return results

    async def send(self, text: str) -> dict:
        if not text or not text.strip():
            raise ValueError("text required")
        # Telegram 제한: 4096 char/msg
        if len(text) > 4000:
            text = text[:3990] + "\n\n... (truncated)"

        url = f"{_API_BASE}{self._token.get_secret_value()}/sendMessage"
        body = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": self._parse_mode,
            "disable_web_page_preview": self._no_preview,
        }
        owns = self._http is None
        client = self._http or httpx.AsyncClient(timeout=self._timeout)
        try:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("telegram send failed: chat=%s err=%s",
                         self._chat_id, type(exc).__name__)
            raise
        finally:
            if owns:
                await client.aclose()

        if not data.get("ok"):
            raise RuntimeError(
                f"telegram error: code={data.get('error_code')} desc={data.get('description')}"
            )
        return data.get("result", {})


def format_buy_alert(symbol: str, name: str, qty: int, order_no: str, dry_run: bool) -> str:
    tag = "🧪 DRY_RUN" if dry_run else "✅ ORDERED"
    return (
        f"*{tag} 매수*\n"
        f"종목: `{symbol}` {name}\n"
        f"수량: {qty:,}주\n"
        f"주문번호: `{order_no}`"
    )


def format_sell_alert(symbol: str, name: str, qty: int, signal: str, pnl_rate: float,
                      order_no: str, dry_run: bool) -> str:
    tag = "🧪 DRY_RUN" if dry_run else ("✅ TP" if signal == "take_profit" else "🛑 SL")
    return (
        f"*{tag} 매도*\n"
        f"종목: `{symbol}` {name}\n"
        f"수량: {qty:,}주 / 수익률: {pnl_rate:+.2f}%\n"
        f"주문번호: `{order_no}`"
    )


def format_simulation_summary(
    *, total_trades: int, total_pnl: float, n_leaders: int, mode: str,
) -> str:
    return (
        f"📊 *시뮬 결과* ({mode})\n"
        f"종목 수: {n_leaders} / 거래: {total_trades}건\n"
        f"PnL: *{int(total_pnl):+,} 원*"
    )


def _escape_md(s: str) -> str:
    """Telegram Markdown 충돌 문자 escape."""
    for ch in ("_", "*", "[", "]", "`"):
        s = s.replace(ch, f"\\{ch}")
    return s


def format_blocked_alert(side: str, symbol: str, reason: str) -> str:
    if len(reason) > 200:
        reason = reason[:197] + "..."
    return (
        f"⚠️ *주문 차단*\n"
        f"방향: {side}\n"
        f"종목: `{symbol}`\n"
        f"사유: {_escape_md(reason)}"
    )


__all__ = [
    "TelegramNotifier",
    "format_buy_alert", "format_sell_alert",
    "format_simulation_summary", "format_blocked_alert",
]
