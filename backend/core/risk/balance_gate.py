"""BAR-OPS-16 / BAR-OPS-09 Phase 9 — 잔고 기반 자금 한도 게이트 (균등 분배).

당일 주도주 시뮬 후 실 매수 진입 전 자금 한도 체크.

정책 (2026-05-23 균등 분배 도입 + max_concurrent 고정):
1. 투자 가능 총액 = cash * max_total_position_ratio (기본 80%) - current_eval
2. 종목당 균등 슬롯 = cash * max_total_position_ratio / max_concurrent_positions (기본 1/10 = 8%)
3. 안전 캡 = cash * max_per_position_ratio (기본 10%) — 균등 슬롯이 캡 초과 시 캡으로 제한
4. 시그널 수가 max_concurrent_positions 미만이어도 슬롯 크기 고정 (잔여 cash 보존)

main 5/14 균등 분배 (시그널 수 기준 variable per_slot) 진화 형태:
시그널 수 변동에 따라 슬롯 크기가 바뀌면 동일 종목이 다른 영업일에 다른 비중으로
진입 → 수익률 비교 불가. max_concurrent 고정 슬롯으로 모든 영업일 일관성 확보.

산출: 종목별 추천 매수 qty + 자금 부족 여부.

운영 영향:
- 본 모듈의 recommended_qty 는 scripts/intraday_buy_daemon.py (50% 1차 진입) 와
  scripts/simulate_leaders.py (--execute) 와 scripts/run_telegram_bot.py (/sim_execute)
  의 매수 qty 결정자. 균등 분배는 모든 진입점에 즉시 반영.

배경:
- 5/22 운영 결과 보유 8 종목 비중 3% ~ 19% (약 6배 편차). 손실 상위 3종목이
  자금 54% 흡수 → 손실 폭 약 57% 악화. 본 정책으로 비중 균등화하여 정확한
  수익률 판단 가능.
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
    max_value: Decimal              # 종목당 슬롯 금액 (균등 분배)
    recommended_qty: int            # 추천 매수 수량
    blocked: bool                   # 자금 부족 여부
    reason: str = ""


@dataclass(frozen=True)
class RiskGateResult:
    cash: Decimal
    current_eval: Decimal
    available: Decimal              # 진입 가능 총액
    max_per_position: Decimal       # 종목당 균등 슬롯 (캡 적용 후)
    max_total_position: Decimal
    max_concurrent_positions: int   # NEW (2026-05-23): 최대 동시 보유 종목 수
    recommendations: list[PositionRecommendation] = field(default_factory=list)


def evaluate_risk_gate(
    deposit: AccountDeposit,
    balance: AccountBalance,
    candidates: list[tuple[str, str, Decimal]],  # (symbol, name, cur_price)
    max_per_position_ratio: Decimal = Decimal("0.10"),
    max_total_position_ratio: Decimal = Decimal("0.80"),
    max_concurrent_positions: int = 10,
) -> RiskGateResult:
    """잔고 + 한도 정책 → 종목별 추천 qty (균등 분배).

    candidates: [(symbol, name, cur_price)] — LeaderPicker 의 출력에서 추출.

    균등 분배 공식:
        even_slot = cash * max_total_position_ratio / max_concurrent_positions
        slot_size = min(even_slot, cash * max_per_position_ratio)   # 안전 캡
        qty       = floor(slot_size / price)

    시그널 수 < max_concurrent_positions 인 경우에도 slot_size 는 고정 (옵션 a).
    잔여 cash 는 다음 영업일 또는 보유 슬롯 비면 사용.
    """
    if not (Decimal("0") < max_per_position_ratio <= Decimal("1")):
        raise ValueError(f"max_per_position_ratio must be in (0, 1], got {max_per_position_ratio}")
    if not (Decimal("0") < max_total_position_ratio <= Decimal("1")):
        raise ValueError(f"max_total_position_ratio must be in (0, 1], got {max_total_position_ratio}")
    if max_concurrent_positions < 1:
        raise ValueError(f"max_concurrent_positions must be >= 1, got {max_concurrent_positions}")

    cash = deposit.cash
    current_eval = balance.total_eval
    max_total = cash * max_total_position_ratio
    max_per_cap = cash * max_per_position_ratio
    available = max_total - current_eval
    if available < 0:
        available = Decimal("0")

    # 균등 슬롯 = 투자 가능 총액 / 최대 동시 보유 종목 수
    even_slot = cash * max_total_position_ratio / Decimal(max_concurrent_positions)
    # 안전 캡 (균등 슬롯이 캡 초과 시 캡 적용)
    slot_size = min(even_slot, max_per_cap)

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

        # 균등 슬롯(max_concurrent 고정) + 남은 가용 자금 중 작은 값
        slot_remaining = available - consumed
        slot = min(slot_size, slot_remaining)
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
                blocked=True, reason=f"가격 {price} > 균등 슬롯 {slot}",
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
        max_per_position=slot_size,
        max_total_position=max_total,
        max_concurrent_positions=max_concurrent_positions,
        recommendations=recommendations,
    )


__all__ = ["PositionRecommendation", "RiskGateResult", "evaluate_risk_gate"]
