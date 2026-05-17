"""수박지표 — ai-trade 포팅 (2026-05-17).

세력 매집 신호 감지. 3조건 동시 충족 시 signal=True:
  1. 거래량 폭증: vol > avg(N) × β  (default N=20, β=2.5)
  2. 캔들 변동폭 확장: (high - low) > ATR(M) × γ  (default M=14, γ=1.5)
  3. 바닥권: close < MA(P) × buffer  (default P=224, buffer=1.1)

발생 캔들의 중심값 (high+low)/2 = 세력 평단가 추정.

BarroAiTrade 적용 방안:
- F존 _score_and_classify 에 watermelon_bonus (선택) — 기준봉 보강
- 별도 signal 로 picker 후보 필터
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from backend.models.market import OHLCV


@dataclass(frozen=True)
class WatermelonResult:
    signal: bool
    avg_price: Optional[Decimal]   # (high+low)/2, signal=True 시
    volume_ratio: Decimal           # vol / vol_avg
    range_ratio: Decimal            # (high-low) / ATR
    bottom_ratio: Decimal           # close / MA


def _sma(values: list[float], n: int) -> float:
    if not values:
        return 0.0
    n = min(n, len(values))
    return sum(values[-n:]) / n


def _atr(candles: list[OHLCV], n: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    n = min(n, len(candles) - 1)
    trs: list[float] = []
    for i in range(1, n + 1):
        c = candles[-i]
        prev = candles[-i - 1]
        tr = max(
            c.high - c.low,
            abs(c.high - prev.close),
            abs(c.low - prev.close),
        )
        trs.append(tr)
    return sum(trs) / len(trs) if trs else 0.0


def watermelon_signal(
    candles: list[OHLCV],
    *,
    vol_avg_period: int = 20,
    vol_spike_ratio: float = 2.5,
    atr_period: int = 14,
    price_move_ratio: float = 1.5,
    ma_period: int = 224,
    ma_buffer: float = 1.1,
) -> WatermelonResult:
    """마지막 candle 기준 수박지표 판정. candles 부족 시 signal=False."""
    if len(candles) < max(vol_avg_period, atr_period + 1):
        return WatermelonResult(
            signal=False, avg_price=None,
            volume_ratio=Decimal("0"), range_ratio=Decimal("0"),
            bottom_ratio=Decimal("0"),
        )
    last = candles[-1]
    vols = [c.volume for c in candles[-vol_avg_period:]]
    vol_avg = sum(vols) / len(vols) if vols else 0.0
    vol_ratio = (last.volume / vol_avg) if vol_avg > 0 else 0.0
    vol_spike = vol_ratio > vol_spike_ratio

    atr = _atr(candles, atr_period)
    rng = last.high - last.low
    range_ratio = (rng / atr) if atr > 0 else 0.0
    range_expand = range_ratio > price_move_ratio

    closes = [c.close for c in candles[-ma_period:]]
    ma = _sma(closes, ma_period) if closes else 0.0
    bottom_ratio = (last.close / ma) if ma > 0 else 0.0
    is_bottom = bottom_ratio < ma_buffer

    signal = vol_spike and range_expand and is_bottom
    avg_price = (
        Decimal(str((last.high + last.low) / 2)) if signal else None
    )
    return WatermelonResult(
        signal=signal,
        avg_price=avg_price,
        volume_ratio=Decimal(str(round(vol_ratio, 4))),
        range_ratio=Decimal(str(round(range_ratio, 4))),
        bottom_ratio=Decimal(str(round(bottom_ratio, 4))),
    )


__all__ = ["WatermelonResult", "watermelon_signal"]
