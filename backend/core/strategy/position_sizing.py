"""BAR-OPS-09 Phase 9 — 공통 균등 position sizing (2026-05-23).

종목당 동일 금액 진입을 위한 공통 함수. 모든 strategy 의 position_size()
default 로 사용.

배경:
- 5/22 운영 결과 보유 8 종목 비중 3% ~ 19% (약 6배 편차) → 손실 종목 비중
  과대로 손실 폭 약 57% 악화. score 기반 차등(strategy 별 10~35%) 제거.
- 균등 비율 = max_total_position_ratio / max_concurrent_positions
            = 0.80 / 10 = 0.08 (8%)

운영 매수 qty 는 balance_gate.evaluate_risk_gate() 가 최종 결정 (균등 분배
정책 동시 적용). 본 함수는 strategy 단위 백테스트/테스트 일관성 용도.
"""
from __future__ import annotations

from decimal import Decimal

from backend.models.signal import EntrySignal
from backend.models.strategy import Account

# default 균등 비율 = max_total_position_ratio / max_concurrent_positions
DEFAULT_EVEN_RATIO = Decimal("0.08")


def even_position_size(
    signal: EntrySignal,
    account: Account,
    ratio: Decimal = DEFAULT_EVEN_RATIO,
) -> Decimal:
    """균등 비율 기반 position size — score 무관, 모든 strategy 동일.

    BAR-OPS-09 Phase 9 (2026-05-23): 종목별 동일 금액 진입.

    Args:
        signal: EntrySignal — score 는 진입 게이트로만 사용 (여기서는 무시).
        account: Account — available 사용.
        ratio: 진입 비율 (default 0.08 = 1/10 슬롯 정책).

    Returns:
        주식 수량 (Decimal, KRX 1주 quantize). available ≤ 0 또는 price ≤ 0 면 Decimal(0).
    """
    if account.available <= 0:
        return Decimal(0)
    price = Decimal(str(signal.price))
    if price <= 0:
        return Decimal(0)
    max_invest = account.available * ratio
    return (max_invest / price).quantize(Decimal("1"))


__all__ = ["DEFAULT_EVEN_RATIO", "even_position_size"]
