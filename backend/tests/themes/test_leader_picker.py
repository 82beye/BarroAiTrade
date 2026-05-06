"""BAR-60 — LeaderStockScorer 12 케이스."""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.core.themes.leader_picker import LeaderStockScorer
from backend.models.leader import LeaderScore, StockMetrics


def _candidates_basic():
    return [
        ("005930", 0.9, 0.8, 1_000_000, Decimal("400000000000000")),
        ("000660", 0.7, 0.6, 500_000, Decimal("100000000000000")),
        ("035420", 0.5, 0.5, 200_000, Decimal("50000000000000")),
    ]


class TestModel:
    def test_leader_score_frozen(self):
        ls = LeaderScore(symbol="005930", theme_id=1, score=0.5)
        with pytest.raises(Exception):
            ls.score = 0.9  # type: ignore[misc]

    def test_score_range_validation(self):
        with pytest.raises(Exception):
            LeaderScore(symbol="x", theme_id=1, score=1.5)

    def test_stock_metrics_frozen_decimal(self):
        m = StockMetrics(symbol="x", daily_volume=100, market_cap=Decimal("1000"))
        assert isinstance(m.market_cap, Decimal)
        with pytest.raises(Exception):
            m.daily_volume = 200  # type: ignore[misc]


class TestScorerInit:
    def test_default_weights(self):
        s = LeaderStockScorer()
        assert abs(sum(s._w.values()) - 1.0) < 1e-6

    def test_invalid_sum_raises(self):
        with pytest.raises(ValueError, match="sum to 1.0"):
            LeaderStockScorer({"theme": 0.5, "embed": 0.3, "volume": 0.1, "cap": 0.05})

    def test_invalid_keys_raises(self):
        with pytest.raises(ValueError, match="weights keys"):
            LeaderStockScorer({"theme": 0.5, "embed": 0.5})

    def test_custom_weights(self):
        s = LeaderStockScorer({"theme": 1.0, "embed": 0.0, "volume": 0.0, "cap": 0.0})
        assert s._w["theme"] == 1.0


class TestScore:
    def test_score_basic(self):
        s = LeaderStockScorer()
        # 0.9 * 0.4 + 0.8 * 0.3 + 0.5 * 0.15 + 0.5 * 0.15 = 0.36 + 0.24 + 0.075 + 0.075 = 0.75
        v = s.score(theme_match=0.9, embed_sim=0.8, volume_norm=0.5, cap_norm=0.5)
        assert abs(v - 0.75) < 1e-6

    def test_score_zero_weights_for_volume_cap(self):
        s = LeaderStockScorer({"theme": 0.5, "embed": 0.5, "volume": 0.0, "cap": 0.0})
        v = s.score(theme_match=0.8, embed_sim=0.6, volume_norm=99.0, cap_norm=99.0)
        assert abs(v - 0.7) < 1e-6  # 0.8*0.5 + 0.6*0.5


class TestSelectLeaders:
    @pytest.mark.asyncio
    async def test_empty_candidates(self):
        s = LeaderStockScorer()
        results = await s.select_leaders(theme_id=1, candidates=[])
        assert results == []

    @pytest.mark.asyncio
    async def test_min_max_normalization_and_sort(self):
        s = LeaderStockScorer()
        results = await s.select_leaders(theme_id=1, candidates=_candidates_basic())
        # 005930 가 가장 높은 score (theme_match 0.9 + 큰 vol/cap → norm 1.0)
        assert results[0].symbol == "005930"
        # 정렬 — score 내림차순
        for a, b in zip(results, results[1:]):
            assert a.score >= b.score

    @pytest.mark.asyncio
    async def test_top_k_limit(self):
        s = LeaderStockScorer()
        results = await s.select_leaders(
            theme_id=1, candidates=_candidates_basic(), top_k=2
        )
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_components_present(self):
        s = LeaderStockScorer()
        results = await s.select_leaders(theme_id=1, candidates=_candidates_basic())
        assert "theme_match" in results[0].components
        assert "volume_norm" in results[0].components
