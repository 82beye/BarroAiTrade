"""한국 주식 가격대별 '라운드피겨(round-figure)' 지지/저항 + 손절선 보정 — 2026-06-12 신규.

배경
----
손절선이 기계적 고정 %(예 -2%/-4%)로 잡혀, 한국 투자자가 심리적으로 의식하는
**원화 라운드피겨**(만원·5만·10만·백만 단위 등)와 무관하게 흔들린다. 라운드피겨는
실제 매물대/지지·저항으로 작동하므로, 손절을 '가장 가까운 라운드 지지선 바로 아래'에
두면 (a) 의미 없는 자리에서의 조기 손절을 줄이고 (b) 지지선이 깨질 때만 청산한다.

라운드피겨(심리적 가격대)는 krx 호가단위(`ob_scalp.krx_tick_size`, 기계적 체결단위)와
**다른 개념**이다. 호가단위는 1·5·10·50·100·500·1000원의 체결 격자이고, 라운드피겨는
사람이 의식하는 '굵은' 가격선이다. 본 모듈은 둘 다 사용한다(라운드선 산출 + 틱 정렬).

가격대별 증분(minor=잔 라운드선, major=굵은 라운드선)
--------------------------------------------------------
    <1,000        minor 100      major 1,000
    1k–5k         minor 500      major 1,000
    5k–10k        minor 1,000    major 5,000
    10k–50k       minor 1,000    major 10,000
    50k–200k      minor 5,000    major 50,000
    200k–500k     minor 10,000   major 100,000
    ≥500k         minor 50,000   major 100,000

검증(설계 시 확인): 153,900→S150,000/R155,000 · 51,200→S50,000 · 201,500→S200,000 ·
8,700→S8,000 · 350,761→S350,000.

손절 규칙
--------
    support  = nearest_round_support(entry)
    buffer   = max(support × buffer_pct, krx_tick_size(support))      # 지지선 살짝 아래
    raw_stop = floor_to_tick(support − buffer)                        # 체결 격자 정렬
    rf_pct   = (raw_stop − entry) / entry                             # 음수 비율(fraction)
    sl       = clamp( looser(base_pct, rf_pct), −max_stop_pct )       # base보다 넉넉하되 상한

`looser` = 더 음수(폭이 넓은) 쪽. 즉 RF는 기존 손절을 라운드 지지선까지 **넓혀** 주되,
전략별 최대 손절폭(`max_stop_pct`)을 절대 넘지 않는다. 항상 < 0 을 보장한다
(StopLoss.fixed_pct 는 lt=0).

운영 토글(전부 env, default OFF/관찰 우선)
-----------------------------------------
    RF_STOP_ENABLED        (default 0)  — 0 이면 resolve_sl_pct 는 base_pct 그대로(완전 무영향).
    RF_STOP_DRY_RUN        (default 1)  — 1 이면 RF 계산은 하되 base_pct 반환(로그로 관찰만).
    RF_MAX_STOP_PCT_INTRADAY (0.04)     — f/sf/gold 등 단타 최대 손절폭(fraction).
    RF_MAX_STOP_PCT_SWING    (0.15)     — swing_38 최대 손절폭(fraction).
    RF_MAJOR_WINDOW_PCT      (1.5)      — 굵은 라운드선을 지지로 채택하는 근접 창(%).
    RF_BUFFER_PCT            (0.003)    — 지지선 아래 버퍼(0.3%).
"""
from __future__ import annotations

import logging
import math
import os
from decimal import Decimal
from typing import Optional, Union

from backend.core.strategy.ob_scalp import krx_tick_size

logger = logging.getLogger(__name__)

Number = Union[int, float, Decimal]

# ── 가격대별 라운드 증분 ──────────────────────────────────────────────────────
# (상한가격, minor, major) — 가격 < 상한 인 첫 구간 적용. 마지막은 무한대.
_INCREMENT_TIERS: tuple[tuple[float, int, int], ...] = (
    (1_000,    100,    1_000),
    (5_000,    500,    1_000),
    (10_000,   1_000,  5_000),
    (50_000,   1_000,  10_000),
    (200_000,  5_000,  50_000),
    (500_000,  10_000, 100_000),
    (math.inf, 50_000, 100_000),
)


def _minor_increment(price: float) -> int:
    for hi, minor, _major in _INCREMENT_TIERS:
        if price < hi:
            return minor
    return _INCREMENT_TIERS[-1][1]


def _major_increment(price: float) -> int:
    for hi, _minor, major in _INCREMENT_TIERS:
        if price < hi:
            return major
    return _INCREMENT_TIERS[-1][2]


def nearest_round_support(price: float, major_window_pct: float = 1.5) -> float:
    """price 이하의 가장 가까운 라운드 지지선.

    굵은 라운드선(major)이 price 의 major_window_pct% 이내 바로 아래면 그것을, 아니면
    잔 라운드선(minor)을 채택. (예: 201,500 → 200,000(major, 0.74%≤1.5%) /
    153,900 → 150,000(minor) / 8,700 → 8,000(minor).)
    """
    if price <= 0:
        return 0.0
    major = _major_increment(price)
    major_level = math.floor(price / major) * major
    if major_level <= price and major_level >= price * (1.0 - major_window_pct / 100.0):
        if major_level > 0:
            return float(major_level)
    minor = _minor_increment(price)
    minor_level = math.floor(price / minor) * minor
    return float(minor_level)


def nearest_round_resistance(price: float, major_window_pct: float = 1.5) -> float:
    """price 초과의 가장 가까운 라운드 저항선 (지지선과 대칭, 위쪽)."""
    if price <= 0:
        return 0.0
    major = _major_increment(price)
    major_level = math.ceil(price / major) * major
    if major_level <= price:
        major_level += major
    if major_level <= price * (1.0 + major_window_pct / 100.0):
        return float(major_level)
    minor = _minor_increment(price)
    minor_level = math.ceil(price / minor) * minor
    if minor_level <= price:
        minor_level += minor
    return float(minor_level)


def floor_to_tick(price: float) -> float:
    """가격을 KRX 체결 격자(호가단위) 아래로 정렬."""
    tick = krx_tick_size(price)
    if tick <= 0:
        return float(price)
    return float(math.floor(price / tick) * tick)


# ── env 토글(call-time 평가 — 테스트 monkeypatch 가능) ────────────────────────
def _truthy(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def rf_enabled() -> bool:
    return _truthy("RF_STOP_ENABLED", "0")


def rf_dry_run() -> bool:
    return _truthy("RF_STOP_DRY_RUN", "1")


def _max_stop_for(strategy_id: str) -> float:
    key = (strategy_id or "").split("_v")[0]
    if key.startswith("swing"):
        return _float_env("RF_MAX_STOP_PCT_SWING", 0.15)
    return _float_env("RF_MAX_STOP_PCT_INTRADAY", 0.04)


def round_figure_stop_pct(
    entry: float, base_pct: float, max_stop_pct: float, buffer_pct: float = 0.003
) -> float:
    """라운드 지지선 기반 손절률(fraction, 음수) — base 보다 넉넉하되 max_stop 이내.

    base_pct/반환값 모두 fraction(예 -0.02 = -2%). 항상 < 0 보장.
    """
    if entry <= 0:
        return base_pct
    support = nearest_round_support(entry, _float_env("RF_MAJOR_WINDOW_PCT", 1.5))
    if support <= 0 or support >= entry:
        # 지지선이 진입가 이상(엣지) → RF 미적용, base 유지.
        return base_pct
    buffer = max(support * buffer_pct, float(krx_tick_size(support)))
    raw_stop = floor_to_tick(support - buffer)
    if raw_stop <= 0 or raw_stop >= entry:
        return base_pct
    rf_pct = (raw_stop - entry) / entry          # 음수
    # base 와 rf 중 더 넉넉한(더 음수) 쪽을 택하되, max_stop 보다 깊지 않게 클램프.
    looser = min(base_pct, rf_pct)
    clamped = max(looser, -abs(max_stop_pct))
    # 안전: 반드시 < 0. (수치 엣지로 0 이상이면 base 로 폴백)
    if clamped >= 0:
        return base_pct if base_pct < 0 else -abs(max_stop_pct)
    return clamped


def resolve_sl_pct(
    strategy_id: str,
    entry: Number,
    base_pct: Number,
    *,
    unit: str = "fraction",
    symbol: str = "",
) -> Number:
    """전략 손절률에 라운드피겨 보정을 적용(또는 OFF/관찰 시 base 그대로 반환).

    unit="fraction": base_pct/반환 = -0.02 형태(ExitPlan StopLoss.fixed_pct).
    unit="percent" : base_pct/반환 = -2.0 형태(HoldingEvaluator stop_loss_pct).
    반환 타입은 base_pct 타입(Decimal/float)을 보존한다.
    """
    if not rf_enabled():
        return base_pct
    scale = 100.0 if unit == "percent" else 1.0
    base_f = float(base_pct) / scale
    entry_f = float(entry)
    max_stop = _max_stop_for(strategy_id)
    rf_f = round_figure_stop_pct(entry_f, base_f, max_stop,
                                 _float_env("RF_BUFFER_PCT", 0.003))
    dry = rf_dry_run()
    if rf_f != base_f:
        logger.info(
            "[RF] %s%s entry=%.2f base=%.4f rf=%.4f → %s (max_stop=%.4f)%s",
            strategy_id, f"/{symbol}" if symbol else "", entry_f,
            base_f, rf_f, f"{(base_f if dry else rf_f):.4f}", max_stop,
            " [DRY_RUN: base 유지]" if dry else " [APPLIED]",
        )
    if dry or rf_f == base_f:
        return base_pct                      # 관찰 전용/무변화 — 원본 그대로(정밀도 보존)
    out_f = rf_f * scale
    return Decimal(str(out_f)) if isinstance(base_pct, Decimal) else out_f


__all__ = [
    "nearest_round_support",
    "nearest_round_resistance",
    "floor_to_tick",
    "round_figure_stop_pct",
    "resolve_sl_pct",
    "rf_enabled",
    "rf_dry_run",
]
