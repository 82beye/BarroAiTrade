"""BAR-72 — WebSocket 채널 user_id 샤드 라우팅.

WS concurrent 1000 → 5000 목표. 사용자 hash 로 샤드 분배.
"""
from __future__ import annotations

import hashlib


class WebSocketChannelShard:
    """user_id → shard_id 매핑."""

    def __init__(self, num_shards: int = 8) -> None:
        if num_shards <= 0:
            raise ValueError("num_shards must be > 0")
        self._num_shards = num_shards

    def shard_for(self, user_id: str) -> int:
        if not user_id:
            raise ValueError("user_id required")
        h = hashlib.md5(user_id.encode("utf-8")).hexdigest()
        return int(h, 16) % self._num_shards

    def channel_for(self, user_id: str) -> str:
        sid = self.shard_for(user_id)
        return f"ws:shard:{sid}:user:{user_id}"

    @property
    def num_shards(self) -> int:
        return self._num_shards


__all__ = ["WebSocketChannelShard"]
