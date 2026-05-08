"""BAR-OPS-26 — 2단계 confirm 패턴 매수 토큰 저장.

텔레그램 한 줄 명령으로 실 주문이 즉시 발동되면 위험 — 6자리 token
+ 5분 TTL 로 한 번 더 검증.

흐름:
  /sim_execute → 후보 표시 + token 발급 (예: A3F7K2)
  /confirm A3F7K2 → 매수 실행
  /cancel → 폐기

저장은 메모리 (단일 프로세스 가정). 봇 재시작 시 모든 token 무효화.
"""
from __future__ import annotations

import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional


_TOKEN_ALPHABET = string.ascii_uppercase + string.digits


@dataclass(frozen=True)
class PendingOrder:
    symbol: str
    name: str
    qty: int


@dataclass
class PendingBatch:
    token: str
    chat_id: str
    orders: list[PendingOrder]
    expires_at: datetime


class OrderConfirmStore:
    """메모리 기반 confirm 토큰 저장 — chat_id 별 하나만 유효."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        self._by_chat: dict[str, PendingBatch] = {}

    def _gen_token(self, length: int = 6) -> str:
        return "".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(length))

    def issue(self, chat_id: str, orders: list[PendingOrder]) -> PendingBatch:
        if not orders:
            raise ValueError("orders required")
        token = self._gen_token()
        # 충돌 방지 — 만에 하나
        while any(b.token == token for b in self._by_chat.values()):
            token = self._gen_token()
        batch = PendingBatch(
            token=token,
            chat_id=str(chat_id),
            orders=orders,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self._ttl),
        )
        self._by_chat[str(chat_id)] = batch
        return batch

    def consume(self, chat_id: str, token: str) -> Optional[PendingBatch]:
        """token 일치 + 만료 X → 반환 + 폐기. 그 외 None."""
        batch = self._by_chat.get(str(chat_id))
        if batch is None:
            return None
        if batch.token != token.strip().upper():
            return None
        if datetime.now(timezone.utc) > batch.expires_at:
            del self._by_chat[str(chat_id)]
            return None
        del self._by_chat[str(chat_id)]
        return batch

    def cancel(self, chat_id: str) -> bool:
        if str(chat_id) in self._by_chat:
            del self._by_chat[str(chat_id)]
            return True
        return False

    def gc(self) -> int:
        """만료 토큰 정리 — 처리 건수 반환."""
        now = datetime.now(timezone.utc)
        expired = [c for c, b in self._by_chat.items() if now > b.expires_at]
        for c in expired:
            del self._by_chat[c]
        return len(expired)


__all__ = ["OrderConfirmStore", "PendingOrder", "PendingBatch"]
