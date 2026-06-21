"""BAR-OPS-39 — 거래 비용 단일 진실원천 (브로커 실측 기반).

근거: reports/2026-06-11/2026-06-11_매매복기.md 핵심분석 1 + 2026-06-21 fill_audit 재도출.
거래세가 매도대금의 0.200%로 확인됐다 — 기존 가정(편도 0.015% + 세 0.18%)의 11.6배.

[2026-06-21 정정] 수수료율 2배 과소 확정(fill_audit 298행 재도출): 수수료합 1,768,040 /
왕복거래액(매수+매도) 505,588,092 = **편도(per-leg) 0.3497%**. fee 공식이
`(매수+매도)×COMMISSION_RATE` 이므로 COMMISSION_RATE = per-leg rate = 0.003497 여야 한다.
종전 0.00175 는 6/11 계산이 '왕복 0.3494%'를 편도로 오라벨 후 반으로 나눈 오류(모델이 실
수수료의 절반만 반영 → 시뮬/선정이 한계셋업 과다 통과). default 0.00175 → 0.0035 정정.
요율 협의/우대계좌 전환 시 env BARRO_COMMISSION_RATE 로 하향.

단위 규약 (일괄 치환 금지 — 100배 오차 함정):
- *_RATE  = 소수 단위 (0.0035 = 0.35%). 일일감사·이브닝 파이프라인·ob_scalp 등.
- *_PCT   = 퍼센트 단위 (0.35 = 0.35%). backend/core/backtester/* 생성자 인자 전용
            (IntradaySimulator(commission_pct=...), PortfolioSimulator(...)).

env 로 조정 가능 (요율 협의/우대 계좌 전환 시):
    BARRO_COMMISSION_RATE=0.00015 BARRO_TAX_RATE_SELL=0.0018  (소수 단위로 지정)
"""
from __future__ import annotations

import os
from decimal import Decimal

# 편도(per-leg) 수수료율 (소수). 실측: fill_audit 298행 — 수수료 1,768,040 / (매수+매도)
#   505,588,092 = 0.3497%/leg → 0.0035. (종전 0.00175 는 2배 과소 오류, docstring 참조.)
#   협의/우대 요율 적용 시 env BARRO_COMMISSION_RATE 로 내릴 것.
COMMISSION_RATE = Decimal(os.environ.get("BARRO_COMMISSION_RATE", "0.0035"))
# 매도 거래세율 (소수). 실측: 41,687 / 20,858,020 = 0.1999% → 0.0020.
TAX_RATE_SELL = Decimal(os.environ.get("BARRO_TAX_RATE_SELL", "0.0020"))
# 왕복 총비용률 (소수) — 진입+청산 수수료 + 매도세. 트립 손익분기 기준선(≈0.55%).
ROUND_TRIP_COST_RATE = COMMISSION_RATE * 2 + TAX_RATE_SELL

# 퍼센트 단위 (backtester 패키지 생성자 인자 전용 — 내부에서 /100 변환).
COMMISSION_PCT = float(COMMISSION_RATE * 100)   # 0.35 (편도)
TAX_PCT_ON_SELL = float(TAX_RATE_SELL * 100)    # 0.20

# 단위 자가검증 — 소수/퍼센트 이원화 실수 방지.
assert abs(COMMISSION_PCT - float(COMMISSION_RATE) * 100) < 1e-12
assert abs(TAX_PCT_ON_SELL - float(TAX_RATE_SELL) * 100) < 1e-12


def sell_tax_rate(is_etf: bool = False) -> Decimal:
    """매도 거래세율 — ETF/ETN 은 거래세 면제(0). 추정기(일일감사 등)용 훅.

    fill_audit(ka10073) 실측이 있으면 그것이 우선 — 본 함수는 추정 경로 전용.
    """
    return Decimal("0") if is_etf else TAX_RATE_SELL


__all__ = [
    "COMMISSION_RATE", "TAX_RATE_SELL", "ROUND_TRIP_COST_RATE",
    "COMMISSION_PCT", "TAX_PCT_ON_SELL", "sell_tax_rate",
]
