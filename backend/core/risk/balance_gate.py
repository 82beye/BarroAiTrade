"""BAR-OPS-16 — 잔고 기반 자금 한도 게이트.

당일 주도주 시뮬 후 실 매수 진입 전 자금 한도 체크.

정책 (조정 가능):
- 종목당 최대 비중: deposit.cash * max_per_position_ratio (기본 30%)
- 총 보유 최대 비중: deposit.cash * max_total_position_ratio (기본 90%)

산출: 종목별 추천 매수 qty + 자금 부족 여부.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

from backend.core.gateway.kiwoom_native_account import (
    AccountBalance,
    AccountDeposit,
)


@dataclass(frozen=True)
class PositionRecommendation:
    symbol: str
    name: str
    cur_price: Decimal
    max_value: Decimal              # 종목당 한도 금액
    recommended_qty: int            # 추천 매수 수량
    blocked: bool                   # 자금 부족 여부
    reason: str = ""


@dataclass(frozen=True)
class RiskGateResult:
    cash: Decimal
    current_eval: Decimal
    available: Decimal              # 진입 가능 총액
    max_per_position: Decimal
    max_total_position: Decimal
    recommendations: list[PositionRecommendation] = field(default_factory=list)


def evaluate_risk_gate(
    deposit: AccountDeposit,
    balance: AccountBalance,
    candidates: list[tuple[str, str, Decimal]],  # (symbol, name, cur_price)
    max_per_position_ratio: Decimal = Decimal("0.30"),
    max_total_position_ratio: Decimal = Decimal("0.90"),
) -> RiskGateResult:
    """잔고 + 한도 정책 → 종목별 추천 qty.

    candidates: [(symbol, name, cur_price)] — LeaderPicker 의 출력에서 추출.
    """
    if not (Decimal("0") < max_per_position_ratio <= Decimal("1")):
        raise ValueError(f"max_per_position_ratio must be in (0, 1], got {max_per_position_ratio}")
    if not (Decimal("0") < max_total_position_ratio <= Decimal("1")):
        raise ValueError(f"max_total_position_ratio must be in (0, 1], got {max_total_position_ratio}")

    cash = deposit.cash
    current_eval = balance.total_eval
    max_total = cash * max_total_position_ratio
    max_per = cash * max_per_position_ratio
    available = max_total - current_eval
    if available < 0:
        available = Decimal("0")

    recommendations: list[PositionRecommendation] = []
    consumed = Decimal("0")

    for sym, name, price in candidates:
        if price <= 0:
            recommendations.append(PositionRecommendation(
                symbol=sym, name=name, cur_price=price,
                max_value=Decimal("0"), recommended_qty=0,
                blocked=True, reason="invalid price",
            ))
            continue

        # 종목당 한도 + 남은 가용 자금 중 작은 값
        slot_remaining = available - consumed
        slot = min(max_per, slot_remaining)
        if slot <= 0:
            recommendations.append(PositionRecommendation(
                symbol=sym, name=name, cur_price=price,
                max_value=Decimal("0"), recommended_qty=0,
                blocked=True, reason="자금 한도 소진",
            ))
            continue

        qty = int((slot / price).quantize(Decimal("1"), rounding=ROUND_DOWN))
        if qty <= 0:
            recommendations.append(PositionRecommendation(
                symbol=sym, name=name, cur_price=price,
                max_value=slot, recommended_qty=0,
                blocked=True, reason=f"가격 {price} > 한도 {slot}",
            ))
            continue

        actual_value = Decimal(qty) * price
        consumed += actual_value
        recommendations.append(PositionRecommendation(
            symbol=sym, name=name, cur_price=price,
            max_value=slot, recommended_qty=qty,
            blocked=False,
        ))

    return RiskGateResult(
        cash=cash,
        current_eval=current_eval,
        available=available,
        max_per_position=max_per,
        max_total_position=max_total,
        recommendations=recommendations,
    )


__all__ = ["PositionRecommendation", "RiskGateResult", "evaluate_risk_gate"]
