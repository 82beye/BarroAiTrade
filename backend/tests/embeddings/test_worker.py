"""BAR-58 — EmbeddingWorker (6 cases)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from backend.core.embeddings.embedder import FakeDeterministicEmbedder
from backend.core.embeddings.worker import EmbeddingWorker


class _FakeRepo:
    expected_dim = 768

    def __init__(self):
        self.inserted = []

    async def insert(self, result):
        self.inserted.append(result)
        return True


def _redis_mock(entries=None):
    fake = AsyncMock()
    fake.xgroup_create = AsyncMock()
    if entries is None:
        fake.xreadgroup = AsyncMock(return_value=[])
    else:
        fake.xreadgroup = AsyncMock(return_value=[("news_items", entries)])
    fake.xack = AsyncMock()
    fake.aclose = AsyncMock()
    return fake


class TestWorkerInit:
    def test_redis_url_must_be_secretstr(self):
        with pytest.raises(ValueError, match="SecretStr"):
            EmbeddingWorker(
                FakeDeterministicEmbedder(),
                _FakeRepo(),
                "plain_url",  # type: ignore[arg-type]
            )

    def test_dim_mismatch_raises(self):
        class _Repo:
            expected_dim = 512

        with pytest.raises(ValueError, match="dim mismatch"):
            EmbeddingWorker(
                FakeDeterministicEmbedder(),
                _Repo(),
                SecretStr("redis://x:6379"),
            )

    def test_batch_size_out_of_range(self):
        with pytest.raises(ValueError, match="batch_size out of range"):
            EmbeddingWorker(
                FakeDeterministicEmbedder(),
                _FakeRepo(),
                SecretStr("redis://x:6379"),
                batch_size=128,
            )


class TestWorkerProcess:
    @pytest.mark.asyncio
    async def test_xgroup_create_invoked_on_connect(self):
        fake = _redis_mock(entries=[])
        with patch("redis.asyncio.from_url", return_value=fake):
            w = EmbeddingWorker(
                FakeDeterministicEmbedder(),
                _FakeRepo(),
                SecretStr("redis://x:6379"),
            )
            await w._connect()
            fake.xgroup_create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_batch_encode_and_ack(self):
        repo = _FakeRepo()
        payload = {
            "id": 42,
            "source": "dart",
            "source_id": "20260507-1",
            "title": "t",
            "body": "본문",
            "url": "https://x",
            "published_at": "2026-05-07T00:00:00+00:00",
            "fetched_at": "2026-05-07T00:00:00+00:00",
            "tags": [],
        }
        entries = [("1700000-0", {"payload": json.dumps(payload)})]
        fake = _redis_mock(entries=entries)
        with patch("redis.asyncio.from_url", return_value=fake):
            w = EmbeddingWorker(
                FakeDeterministicEmbedder(),
                repo,
                SecretStr("redis://x:6379"),
                batch_size=10,
            )
            n = await w.run_once()
            assert n == 1
            assert w.processed == 1
            assert len(repo.inserted) == 1
            assert repo.inserted[0].news_db_id == 42
            fake.xack.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_poison_pill_acked_and_counted(self):
        # invalid JSON payload
        entries = [("1700000-1", {"payload": "not json"})]
        fake = _redis_mock(entries=entries)
        with patch("redis.asyncio.from_url", return_value=fake):
            w = EmbeddingWorker(
                FakeDeterministicEmbedder(),
                _FakeRepo(),
                SecretStr("redis://x:6379"),
            )
            await w.run_once()
            assert w.errors == 1
            fake.xack.assert_awaited()

    @pytest.mark.asyncio
    async def test_encode_failure_keeps_pel(self):
        class _BrokenEmbedder:
            name = "broken"
            dim = 768

            async def encode(self, texts):
                raise RuntimeError("encode boom")

        payload = {
            "id": 1,
            "source": "dart",
            "source_id": "x",
            "title": "t",
            "body": "b",
            "url": "https://x",
            "published_at": "2026-05-07T00:00:00+00:00",
            "fetched_at": "2026-05-07T00:00:00+00:00",
            "tags": [],
        }
        entries = [("1700000-2", {"payload": json.dumps(payload)})]
        fake = _redis_mock(entries=entries)
        with patch("redis.asyncio.from_url", return_value=fake):
            w = EmbeddingWorker(
                _BrokenEmbedder(),
                _FakeRepo(),
                SecretStr("redis://x:6379"),
            )
            await w.run_once()
            assert w.errors == 1
            assert w.processed == 0
            # NACK — xack 호출 안 됨 (poison 케이스 외)
            fake.xack.assert_not_awaited()
