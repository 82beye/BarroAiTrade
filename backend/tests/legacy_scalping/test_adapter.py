"""
BAR-41 어댑터 8 케이스 단위 테스트 (Plan §4.2 / Design §4).

T1~T3 정상 변환 / T4~T5 fallback / T6~T7 거부 / T8 경계.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from backend.legacy_scalping._adapter import (
    LegacySignalSchema,
    to_entry_signal,
)
from backend.models.market import MarketType
from backend.models.signal import EntrySignal


class TestToEntrySignal:
    """BAR-41 어댑터 8 케이스."""

    # ── 정상 변환 (T1~T3) ──

    def test_t1_dict_form_full_fields(self, sample_legacy_dict: dict) -> None:
        """T1: dict 형태 legacy signal (모든 필드) → EntrySignal."""
        result = to_entry_signal(sample_legacy_dict)

        assert isinstance(result, EntrySignal)
        assert result.symbol == "005930"
        assert result.name == "삼성전자"
        assert result.price == 72000.0
        assert result.signal_type == "f_zone"  # timing="즉시" → f_zone
        assert result.score == pytest.approx(0.85, abs=1e-4)
        assert result.market_type == MarketType.STOCK
        assert result.strategy_id == "legacy_scalping_consensus"
        assert result.risk_approved is False
        assert "VWAP" in result.reason
        assert result.metadata.get("consensus_level") == "다수합의"
        assert result.metadata.get("tp_pct") == 3.0

    def test_t2_scalping_analysis_dataclass(self, sample_scalping_analysis) -> None:
        """T2: ScalpingAnalysis dataclass → EntrySignal (timing 매핑)."""
        result = to_entry_signal(sample_scalping_analysis)

        assert result.symbol == "005930"
        assert result.name == "삼성전자"
        assert result.signal_type == "f_zone"
        assert result.metadata.get("legacy_timing") == "즉시"
        assert result.metadata.get("rank") == 1
        assert result.metadata.get("surge_type") == "intraday"
        assert result.metadata.get("intraday_atr") == 850.0

    def test_t3_score_normalization(self, sample_scalping_analysis) -> None:
        """T3: total_score=85 → score=0.85 (Decimal quantize ROUND_HALF_UP)."""
        for raw_score, expected in [
            (85.0, 0.85),
            (50.5, 0.505),
            (99.99, 0.9999),
            (33.333, 0.3333),
        ]:
            data = replace(sample_scalping_analysis, total_score=raw_score)
            result = to_entry_signal(data)
            assert result.score == pytest.approx(expected, abs=1e-4)

    # ── Fallback (T4~T5) ──

    def test_t4_name_fallback_to_symbol(self, sample_legacy_dict: dict) -> None:
        """T4: name 누락 → symbol 을 name 으로 사용."""
        data = {**sample_legacy_dict, "name": None}
        result = to_entry_signal(data)
        assert result.name == "005930"

    def test_t5_price_missing_raises_valueerror(self, sample_scalping_analysis) -> None:
        """T5: price 도출 불가 (snapshot 없고 optimal=0) → ValueError."""
        data = replace(
            sample_scalping_analysis,
            snapshot=None,
            optimal_entry_price=0,
        )
        with pytest.raises(ValueError, match="price not derivable"):
            to_entry_signal(data)

    # ── 거부 (T6~T7) ──

    def test_t6_none_input_raises_typeerror(self) -> None:
        """T6: legacy_data=None → TypeError."""
        with pytest.raises(TypeError, match="must not be None"):
            to_entry_signal(None)

    def test_t7_score_out_of_range_raises_validation(self, sample_legacy_dict: dict) -> None:
        """T7: total_score=120 (범위 초과) → ValidationError."""
        data = {**sample_legacy_dict, "total_score": 120.0}
        with pytest.raises(ValidationError):
            to_entry_signal(data)

    # ── 경계 (T8) ──

    def test_t8_score_zero_boundary(self, sample_legacy_dict: dict) -> None:
        """T8: total_score=0 → score=0.0 (정상 변환)."""
        data = {**sample_legacy_dict, "total_score": 0.0}
        result = to_entry_signal(data)
        assert result.score == 0.0
        assert isinstance(result, EntrySignal)


class TestUnsupportedTypes:
    """타입 거부 보강 (T6 의 확장)."""

    def test_str_input_raises(self) -> None:
        with pytest.raises(TypeError, match="unsupported"):
            to_entry_signal("not a dict")

    def test_int_input_raises(self) -> None:
        with pytest.raises(TypeError, match="unsupported"):
            to_entry_signal(42)


class TestSignalTypeMapping:
    """timing/market_type → signal_type 매핑 정책 (Design §3.3)."""

    @pytest.mark.parametrize(
        "timing, market_type, expected",
        [
            ("즉시", "stock", "f_zone"),
            ("대기", "stock", "sf_zone"),
            ("눌림목대기", "stock", "blue_line"),
            ("관망", "stock", "blue_line"),
            ("unknown_timing", "stock", "blue_line"),
            ("즉시", "crypto", "crypto_breakout"),
        ],
    )
    def test_signal_type_mapping(
        self, sample_legacy_dict: dict, timing: str, market_type: str, expected: str
    ) -> None:
        data = {**sample_legacy_dict, "timing": timing, "market_type": market_type}
        result = to_entry_signal(data)
        assert result.signal_type == expected


class TestSchemaIsolated:
    """LegacySignalSchema 단독 검증."""

    def test_schema_extra_ignored(self) -> None:
        """extra='ignore' 정책 — 미정의 필드는 무시."""
        schema = LegacySignalSchema(
            code="005930",
            price=72000.0,
            total_score=85.0,
            unknown_field="should be ignored",  # type: ignore[call-arg]
        )
        assert schema.code == "005930"

    def test_schema_negative_score_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LegacySignalSchema(code="005930", price=72000.0, total_score=-1.0)

    def test_schema_zero_price_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LegacySignalSchema(code="005930", price=0.0, total_score=85.0)
