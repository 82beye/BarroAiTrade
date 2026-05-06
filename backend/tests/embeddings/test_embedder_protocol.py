"""BAR-58 — Embedder Protocol + FakeDeterministicEmbedder (4 cases)."""
from __future__ import annotations

import numpy as np
import pytest

from backend.core.embeddings.embedder import (
    Embedder,
    FakeDeterministicEmbedder,
)


class TestProtocol:
    def test_protocol_runtime_checkable(self):
        e = FakeDeterministicEmbedder()
        assert isinstance(e, Embedder)


class TestFakeEmbedder:
    @pytest.mark.asyncio
    async def test_dim_768_and_l2_normalized(self):
        e = FakeDeterministicEmbedder()
        vecs = await e.encode(["hello world"])
        assert len(vecs) == 1
        assert vecs[0].shape == (768,)
        assert vecs[0].dtype == np.float32
        # L2 normalized
        assert abs(float(np.linalg.norm(vecs[0])) - 1.0) < 1e-5

    @pytest.mark.asyncio
    async def test_deterministic_same_input(self):
        e = FakeDeterministicEmbedder()
        v1 = (await e.encode(["abc"]))[0]
        v2 = (await e.encode(["abc"]))[0]
        assert np.array_equal(v1, v2)

    @pytest.mark.asyncio
    async def test_empty_input(self):
        e = FakeDeterministicEmbedder()
        vecs = await e.encode([])
        assert vecs == []
