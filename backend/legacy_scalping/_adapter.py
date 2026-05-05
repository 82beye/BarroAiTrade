"""
BAR-41: legacy_scalping ↔ EntrySignal 모델 호환 어댑터

ai-trade 의 ScalpingAnalysis dataclass / dict 시그널을 BarroAiTrade 의 표준
backend.models.signal.EntrySignal 로 변환한다.

Reference:
- Plan: docs/01-plan/features/bar-41-model-adapter.plan.md
- Design: docs/02-design/features/bar-41-model-adapter.design.md
- ScalpingAnalysis 정의: backend.legacy_scalping.strategy.scalping_team.base_agent:57

원칙:
- Single Conversion Direction Primary (legacy → EntrySignal)
- Fail Fast (silent default 회피)
- Original Preservation (metadata 에 원본 보존)
- Decimal Awareness (산술은 Decimal, 출력만 float)
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.market import MarketType
from backend.models.signal import EntrySignal


# ── 정규화 스키마 ──────────────────────────────────────────────

class LegacySignalSchema(BaseModel):
    """ai-trade 시그널의 정규화 중간 표현 (BAR-41 design §3.1)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    code: str = Field(..., min_length=1)
    name: str | None = None
    price: float = Field(..., gt=0)
    total_score: float = Field(..., ge=0, le=100)
    timing: str = ""
    consensus_level: str = ""
    market_type: Literal["stock", "crypto"] = "stock"
    strategy_id: str = "legacy_scalping_consensus"
    timestamp: datetime | None = None

    confidence: float | None = None
    optimal_entry_price: float | None = None
    scalp_tp_pct: float | None = None
    scalp_sl_pct: float | None = None
    hold_minutes: int | None = None
    surge_type: str | None = None
    intraday_atr: float | None = None
    rank: int | None = None
    top_reasons: list[str] = Field(default_factory=list)
    raw: dict[str, Any] | None = None


# ── 매핑 정책 ──────────────────────────────────────────────

_TIMING_TO_SIGNAL_TYPE: dict[str, str] = {
    "즉시": "f_zone",
    "대기": "sf_zone",
    "눌림목대기": "blue_line",
    "관망": "blue_line",
}


def _resolve_signal_type(timing: str, market_type: str) -> str:
    """timing/market_type → EntrySignal.signal_type 5 enum 매핑.

    crypto 우선. 미매칭 timing 은 'blue_line' 기본값.
    """
    if market_type == "crypto":
        return "crypto_breakout"
    return _TIMING_TO_SIGNAL_TYPE.get(timing, "blue_line")


def _normalize_score(total_score: float) -> float:
    """0~100 점수를 0~1 로 Decimal quantize 후 float 캐스팅."""
    normalized = (Decimal(str(total_score)) / Decimal(100)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    return float(normalized)


def _coerce_to_dict(legacy_data: Any) -> dict[str, Any]:
    """ScalpingAnalysis dataclass / dict 를 단일 dict 로 정규화."""
    if legacy_data is None:
        raise TypeError("legacy_data must not be None")

    if isinstance(legacy_data, dict):
        return dict(legacy_data)

    if is_dataclass(legacy_data):
        return asdict(legacy_data)

    raise TypeError(
        f"unsupported legacy_data type: {type(legacy_data).__name__} "
        "(expected ScalpingAnalysis dataclass or dict)"
    )


def _derive_price(data: dict[str, Any]) -> float:
    """price 도출: 명시 price > snapshot.price > optimal_entry_price.

    셋 다 없거나 0 이하면 ValueError.
    """
    explicit = data.get("price")
    if explicit and explicit > 0:
        return float(explicit)

    snapshot = data.get("snapshot")
    if isinstance(snapshot, dict):
        snap_price = snapshot.get("price")
        if snap_price and snap_price > 0:
            return float(snap_price)

    optimal = data.get("optimal_entry_price")
    if optimal and optimal > 0:
        return float(optimal)

    raise ValueError(
        "price not derivable from legacy_data "
        "(checked: price, snapshot.price, optimal_entry_price)"
    )


def _build_metadata(schema: LegacySignalSchema, raw: dict[str, Any]) -> dict[str, Any]:
    """EntrySignal.metadata 구성. 원본 보존 + 옵션 필드."""
    meta: dict[str, Any] = {
        "legacy_timing": schema.timing,
        "consensus_level": schema.consensus_level,
        "confidence": schema.confidence,
        "optimal_entry_price": schema.optimal_entry_price,
        "tp_pct": schema.scalp_tp_pct,
        "sl_pct": schema.scalp_sl_pct,
        "hold_minutes": schema.hold_minutes,
        "surge_type": schema.surge_type,
        "intraday_atr": schema.intraday_atr,
        "rank": schema.rank,
    }

    if "agent_signals" in raw:
        meta["agent_signals"] = raw["agent_signals"]

    return {k: v for k, v in meta.items() if v is not None}


def _format_reason(top_reasons: list[str], timing: str, fallback: str = "legacy signal") -> str:
    """top_reasons 결합 → reason. 비어있으면 timing 또는 fallback."""
    if top_reasons:
        return "; ".join(top_reasons[:3])
    if timing:
        return f"legacy timing: {timing}"
    return fallback


# ── 공개 API ──────────────────────────────────────────────

def to_entry_signal(
    legacy_data: Any,
    *,
    fallback_market_type: MarketType = MarketType.STOCK,
) -> EntrySignal:
    """legacy ScalpingAnalysis 또는 dict 시그널을 EntrySignal 로 변환.

    Args:
        legacy_data: ScalpingAnalysis dataclass 또는 dict 형태 시그널.
        fallback_market_type: market_type 키가 누락 시 사용할 기본값.

    Raises:
        TypeError: legacy_data 가 None 또는 지원되지 않는 타입.
        ValueError: price 도출 불가.
        pydantic.ValidationError: schema 검증 실패 (range, length 등).
    """
    raw = _coerce_to_dict(legacy_data)

    market_type_raw = raw.get("market_type")
    market_type_str = (
        market_type_raw.value if isinstance(market_type_raw, MarketType)
        else (market_type_raw or fallback_market_type.value)
    )

    schema_input = {
        "code": raw.get("code"),
        "name": raw.get("name"),
        "price": _derive_price(raw),
        "total_score": raw.get("total_score", 0),
        "timing": raw.get("timing", ""),
        "consensus_level": raw.get("consensus_level", ""),
        "market_type": market_type_str,
        "strategy_id": raw.get("strategy_id", "legacy_scalping_consensus"),
        "timestamp": raw.get("timestamp"),
        "confidence": raw.get("confidence"),
        "optimal_entry_price": raw.get("optimal_entry_price"),
        "scalp_tp_pct": raw.get("scalp_tp_pct"),
        "scalp_sl_pct": raw.get("scalp_sl_pct"),
        "hold_minutes": raw.get("hold_minutes"),
        "surge_type": raw.get("surge_type"),
        "intraday_atr": raw.get("intraday_atr"),
        "rank": raw.get("rank"),
        "top_reasons": raw.get("top_reasons", []),
        "raw": raw,
    }

    schema = LegacySignalSchema.model_validate(schema_input)

    market_type = MarketType(schema.market_type)
    timestamp = schema.timestamp or datetime.now(timezone.utc)

    entry_signal = EntrySignal(
        symbol=schema.code,
        name=schema.name or schema.code,
        price=schema.price,
        signal_type=_resolve_signal_type(schema.timing, schema.market_type),
        score=_normalize_score(schema.total_score),
        reason=_format_reason(schema.top_reasons, schema.timing),
        market_type=market_type,
        strategy_id=schema.strategy_id,
        timestamp=timestamp,
        risk_approved=False,
        metadata=_build_metadata(schema, raw),
    )

    return entry_signal


def to_legacy_dict(signal: EntrySignal) -> dict[str, Any]:
    """EntrySignal 을 legacy 모니터링 호환 dict 로 역변환 (FR-02, MVP).

    BAR-41 단계에서는 *최소 구현* — top-level keys 만 미러. 정밀 호환은
    legacy 모니터링 통합 시점(후속 BAR) 에서 강화.
    """
    meta = signal.metadata or {}
    return {
        "code": signal.symbol,
        "name": signal.name,
        "price": signal.price,
        "total_score": signal.score * 100,
        "timing": meta.get("legacy_timing", ""),
        "consensus_level": meta.get("consensus_level", ""),
        "confidence": meta.get("confidence"),
        "scalp_tp_pct": meta.get("tp_pct"),
        "scalp_sl_pct": meta.get("sl_pct"),
        "hold_minutes": meta.get("hold_minutes"),
        "top_reasons": [signal.reason] if signal.reason else [],
        "market_type": signal.market_type.value,
        "strategy_id": signal.strategy_id,
        "timestamp": signal.timestamp.isoformat(),
    }


__all__ = ["to_entry_signal", "to_legacy_dict", "LegacySignalSchema"]
