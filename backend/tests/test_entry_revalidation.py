"""고도화 #6 — 진입 재검증 게이트 헬퍼 (hot-path 안전성 단위검증).

핵심 안전속성: 데이터 부족/예외/미지원전략은 '보수적 통과'(ok=True) — over-block 방지.
명확한 차단(ok=False)은 analyze() 가 None 을 반환할 때만.
"""
from __future__ import annotations

from scripts.intraday_buy_daemon import (
    _build_reval_strategy,
    _revalidate_entry,
    _REVAL_MIN_ATR,
    _REVAL_MIN_BARS,
)


class TestBuildRevalStrategy:
    def test_gold_min_atr_intraday(self):
        g = _build_reval_strategy("gold_zone")
        assert g is not None
        assert float(g.params.min_atr_pct) == _REVAL_MIN_ATR  # 분봉 0.01 (일봉 0.035 와 분리)

    def test_f_zone_uses_intraday_preset(self):
        f = _build_reval_strategy("f_zone")
        assert f is not None
        assert f.params.min_candles == 120  # for_intraday preset
        assert float(f.params.min_atr_pct) == _REVAL_MIN_ATR

    def test_sf_zone_built(self):
        sf = _build_reval_strategy("sf_zone")
        assert sf is not None

    def test_unknown_strategy_none(self):
        assert _build_reval_strategy("blue_line") is None


class TestRevalidateSafeFallback:
    def test_empty_bars_pass(self):
        ok, reason = _revalidate_entry("gold_zone", "005930", "삼성", [])
        assert ok is True and "분봉부족" in reason

    def test_insufficient_bars_pass(self):
        # _REVAL_MIN_BARS 미만이면 보수적 통과
        bars = [object()] * (_REVAL_MIN_BARS - 1)
        ok, reason = _revalidate_entry("gold_zone", "005930", "삼성", bars)
        assert ok is True and "통과" in reason

    def test_unknown_strategy_pass(self):
        ok, reason = _revalidate_entry("blue_line", "005930", "삼성", [])
        assert ok is True and "미지원" in reason

    def test_returns_tuple(self):
        r = _revalidate_entry("gold_zone", "005930", "삼성", [])
        assert isinstance(r, tuple) and len(r) == 2
        assert isinstance(r[0], bool) and isinstance(r[1], str)
