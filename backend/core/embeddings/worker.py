"""
BAR-58 — EmbeddingWorker.

Redis Streams news_items consumer group embedder_v1.
council 흡수:
- consumer_name = f"embedder-{hostname}-{pid}" (reviewer)
- BLOCK_MS=1000 (shutdown race 단축, architect/reviewer)
- batch encode 부분 실패 → entire batch NACK (PEL 잔존, architect/developer)
- error log payload 제외 (security CWE-532)
- body 트렁케이션 MAX_EMBED_CHARS (security CWE-1284)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from typing import Literal, Optional

from pydantic import SecretStr

from backend.core.embeddings.embedder import Embedder
from backend.models.embedding import (
    MAX_EMBED_CHARS,
    EmbeddingJob,
    EmbeddingResult,
)

logger = logging.getLogger(__name__)

STREAM_KEY = "news_items"
GROUP_NAME = "embedder_v1"


class EmbeddingWorker:
    """Redis Streams consumer."""

    BATCH_SIZE: int = 16
    BLOCK_MS: int = 1000

    def __init__(
        self,
        embedder: Embedder,
        repo,
        redis_url: SecretStr,
        stream_start: Literal["$", "0"] = "$",
        batch_size: int = BATCH_SIZE,
    ) -> None:
        if not isinstance(redis_url, SecretStr):
            raise ValueError("redis_url must be SecretStr (CWE-522)")
        repo_dim = getattr(repo, "expected_dim", embedder.dim)
        if embedder.dim != repo_dim:
            raise ValueError(
                f"dim mismatch: embedder.dim={embedder.dim} "
                f"repo.expected_dim={repo_dim}"
            )
        if not (1 <= batch_size <= 64):
            raise ValueError(f"batch_size out of range [1, 64]: {batch_size}")
        self.consumer_name = f"embedder-{socket.gethostname()}-{os.getpid()}"
        self._embedder = embedder
        self._repo = repo
        self._redis_url = redis_url
        self._stream_start = stream_start
        self._batch_size = batch_size
        self._stop = asyncio.Event()
        self._client: Optional[object] = None
        self.processed: int = 0
        self.errors: int = 0

    async def _connect(self):
        if self._client is None:
            import redis.asyncio as redis_async

            self._client = redis_async.from_url(
                self._redis_url.get_secret_value(),
                decode_responses=True,
            )
            try:
                await self._client.xgroup_create(
                    STREAM_KEY,
                    GROUP_NAME,
                    id=self._stream_start,
                    mkstream=True,
                )
            except Exception:
                # 이미 존재 — 정상
                pass
        return self._client

    async def run_once(self) -> int:
        """1 batch 처리. 테스트용 — 무한 loop 대신 결정적 처리.

        Returns: 처리된 entries 수.
        """
        client = await self._connect()
        try:
            resp = await client.xreadgroup(
                GROUP_NAME,
                self.consumer_name,
                streams={STREAM_KEY: ">"},
                count=self._batch_size,
                block=self.BLOCK_MS,
            )
        except Exception as exc:
            logger.error("xreadgroup err=%s", type(exc).__name__)
            self.errors += 1
            return 0
        if not resp:
            return 0
        entries = resp[0][1]
        await self._process_batch(client, entries)
        return len(entries)

    async def run(self) -> None:
        """무한 loop. stop_event set 시 종료. shutdown race 단축 (BLOCK=1s)."""
        client = await self._connect()
        while not self._stop.is_set():
            try:
                resp = await client.xreadgroup(
                    GROUP_NAME,
                    self.consumer_name,
                    streams={STREAM_KEY: ">"},
                    count=self._batch_size,
                    block=self.BLOCK_MS,
                )
            except Exception as exc:
                logger.error("xreadgroup err=%s", type(exc).__name__)
                self.errors += 1
                await asyncio.sleep(1)
                continue
            if not resp:
                continue
            await self._process_batch(client, resp[0][1])

    async def _process_batch(self, client, entries) -> None:
        """entries: [(stream_id, {"payload": ...}), ...]."""
        jobs: list[EmbeddingJob] = []
        for stream_id, fields in entries:
            try:
                payload_json = fields.get("payload") or fields.get(b"payload")
                if isinstance(payload_json, bytes):
                    payload_json = payload_json.decode("utf-8")
                data = json.loads(payload_json)
                body = (data.get("body") or "")[:MAX_EMBED_CHARS]
                news_id = int(data.get("id") or 0)
                if news_id <= 0:
                    raise ValueError("missing news id")
                jobs.append(
                    EmbeddingJob(
                        news_db_id=news_id,
                        body=body or " ",  # 빈 body 도 인코딩 (검색 용)
                        stream_id=stream_id,
                    )
                )
            except Exception:
                # poison pill — ACK + counter (재시도 무의미)
                self.errors += 1
                try:
                    await client.xack(STREAM_KEY, GROUP_NAME, stream_id)
                except Exception:
                    pass

        if not jobs:
            return

        try:
            vectors = await self._embedder.encode([j.body for j in jobs])
        except Exception as exc:
            self.errors += 1
            logger.error("encode batch failed: %s", type(exc).__name__)
            return  # NACK — PEL 잔존, BAR-58b 에서 claim 회복

        for job, vec in zip(jobs, vectors):
            try:
                await self._repo.insert(
                    EmbeddingResult(
                        news_db_id=job.news_db_id,
                        model=self._embedder.name,
                        vector=tuple(float(x) for x in vec),
                    )
                )
                await client.xack(STREAM_KEY, GROUP_NAME, job.stream_id)
                self.processed += 1
            except Exception as exc:
                self.errors += 1
                # security: payload/body 제외
                logger.error(
                    "insert failed news_id=%s stream_id=%s err=%s",
                    job.news_db_id,
                    job.stream_id,
                    type(exc).__name__,
                )

    async def stop(self) -> None:
        self._stop.set()
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass


__all__ = ["EmbeddingWorker", "STREAM_KEY", "GROUP_NAME"]
