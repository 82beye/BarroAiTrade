"""고도화 #6 — 진입 재검증 게이트 헬퍼 (hot-path 안전성 단위검증).

핵심 안전속성: 데이터 부족/예외/미지원전략은 '보수적 통과'(ok=True) — over-block 방지.
명확한 차단(ok=False)은 analyze() 가 None 을 반환할 때만.
"""
from __future__ import annotations

from types import SimpleNamespace

from scripts.intraday_buy_daemon import (
    _build_reval_strategy,
    _revalidate_entry,
    _REVAL_MIN_ATR,
    _REVAL_MIN_BARS,
    _MEANREV_STRATEGIES,
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


class TestMeanRevClassification:
    """⑦⑧ — 되돌림(바닥) 전략 분류. gold만 고점차단·DCA비활성 대상."""

    def test_gold_is_meanrev(self):
        assert "gold_zone" in _MEANREV_STRATEGIES

    def test_momentum_strategies_not_meanrev(self):
        assert "f_zone" not in _MEANREV_STRATEGIES
        assert "sf_zone" not in _MEANREV_STRATEGIES


class TestFlagGating:
    """플래그 default off = 동작 불변. on + gold 일 때만 게이트 발동."""

    @staticmethod
    def _gate(flag_name: str, flag_val: bool, strategy: str) -> bool:
        args = SimpleNamespace(**{flag_name: flag_val})
        return getattr(args, flag_name, False) and strategy in _MEANREV_STRATEGIES

    def test_entry_reval_off_no_gate(self):
        # 플래그 off → gold 라도 ⑦ 미발동 (동작 불변)
        assert self._gate("entry_revalidate", False, "gold_zone") is False

    def test_entry_reval_on_gold_gates(self):
        assert self._gate("entry_revalidate", True, "gold_zone") is True

    def test_entry_reval_on_fzone_no_gate(self):
        # 플래그 on 이어도 f_zone 은 ⑦ 미발동 (모멘텀형 momentum 예외 유지)
        assert self._gate("entry_revalidate", True, "f_zone") is False

    def test_dca_gate_off_no_skip(self):
        assert self._gate("dca_strategy_gate", False, "gold_zone") is False

    def test_dca_gate_on_gold_skips(self):
        assert self._gate("dca_strategy_gate", True, "gold_zone") is True

    def test_dca_gate_on_fzone_no_skip(self):
        assert self._gate("dca_strategy_gate", True, "f_zone") is False
