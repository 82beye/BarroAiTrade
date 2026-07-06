"""전략별 기준가 사다리 파생 모듈 (티마 앱 벤치마킹 P0).

전략 신호가(또는 현재가)와 선택적 OHLCV 캔들로부터 티마식 기준가 사다리
(F존 SF/B1~B3, 골드존 G1~G3, 38스윙 J1~J3)를 **결정적**으로 산출하는 순수 함수 모듈.

⚠️ 본 모듈이 산출하는 기준가는 **시스템 파생 계산값**이며 원앱(티마)의 산출식과
   다를 수 있습니다. 매수·매도 권유가 아닌 참고용 계산 결과입니다.

설계 원칙:
  - 순수 함수 (외부 IO 없음, temperature 0.0 결정성).
  - 캔들 없이도 동작 (신호가 배율 폴백).
  - 가격 서열 강제 (F존 SF > B1 > B2 > B3, target 사다리는 오름차순).
  - KRX 원화 정수 round (호가단위 보정은 하지 않음 — 표시용 근사값).
"""
from __future__ import annotations

from typing import Any, List, Optional

# 전략 → 기준가 kind
#   support : 현재가 아래 지지선 (F존 눌림목 이평 지지)
#   target  : 현재가 위 목표가 (골드존/38스윙 상방 사다리)
#   anchor  : 신호 발생 기준가 (F존 SF)
SUPPORTED_STRATEGIES = ("f_zone", "sf_zone", "gold_zone", "swing_38")

# F존/SF존 폴백 지지선 배율 (캔들 없을 때 MA5/MA10/MA20 대용)
_FZONE_SUPPORT_RATIOS = (0.99, 0.98, 0.97)
_FZONE_SUPPORT_PERIODS = (5, 10, 20)
# 골드존 상방 목표 배율 (F존 exit_plan TP 배율 계열과 일관: +3/+5/+8%)
_GOLD_TARGET_RATIOS = (1.03, 1.05, 1.08)
# 38스윙 상방 스윙 목표 배율 (+5/+10/+15%)
_SWING_TARGET_RATIOS = (1.05, 1.10, 1.15)


def _closes(candles: Optional[List[Any]]) -> List[float]:
    """OHLCV 객체/딕셔너리 리스트에서 종가만 추출 (오래된→최신 순 유지)."""
    if not candles:
        return []
    out: List[float] = []
    for c in candles:
        close = getattr(c, "close", None)
        if close is None and isinstance(c, dict):
            close = c.get("close")
        if close is not None:
            out.append(float(close))
    return out


def _ma(closes: List[float], period: int) -> Optional[float]:
    """최근 period 종가 단순이동평균 (캔들 부족 시 None)."""
    if len(closes) < period or period <= 0:
        return None
    window = closes[-period:]
    return sum(window) / len(window)


def _enforce_descending(vals: List[float], ceiling: Optional[float]) -> List[float]:
    """값들을 (ceiling 미만으로) 엄격히 내림차순 정렬·클램프."""
    out: List[float] = []
    prev = ceiling
    for v in sorted(vals, reverse=True):
        if prev is not None and v >= prev:
            v = prev * 0.99
        out.append(v)
        prev = v
    return out


def _enforce_ascending(vals: List[float], floor: Optional[float]) -> List[float]:
    """값들을 (floor 초과로) 엄격히 오름차순 정렬·클램프."""
    out: List[float] = []
    prev = floor
    for v in sorted(vals):
        if prev is not None and v <= prev:
            v = prev * 1.01
        out.append(v)
        prev = v
    return out


def _mark_active(levels: List[dict], current_price: float) -> None:
    """active 규칙 적용 (in-place).

    - support: 현재가 아래 중 가장 높은(근접한) 1개만 active=True
    - target : 현재가 위 중 가장 낮은(근접한) 1개만 active=True
    - anchor : 항상 active=False
    """
    for lv in levels:
        lv["active"] = False

    supports = [lv for lv in levels if lv["kind"] == "support" and lv["price"] < current_price]
    if supports:
        max(supports, key=lambda lv: lv["price"])["active"] = True

    targets = [lv for lv in levels if lv["kind"] == "target" and lv["price"] > current_price]
    if targets:
        min(targets, key=lambda lv: lv["price"])["active"] = True


def compute_levels(
    signal_type: str,
    signal_price: float,
    current_price: Optional[float] = None,
    candles: Optional[List[Any]] = None,
) -> List[dict]:
    """전략별 기준가 사다리를 산출한다.

    Args:
        signal_type: "f_zone" | "sf_zone" | "gold_zone" | "swing_38".
        signal_price: 신호 발생 기준가 (F존 SF, 목표 사다리 기준).
        current_price: 현재가 (active 판정용). 미지정 시 signal_price 사용.
        candles: MA 계산용 OHLCV 리스트 (선택). 없으면 배율 폴백.

    Returns:
        [{label, price, kind, active}, ...]. 미지원 전략은 빈 리스트.

    Note:
        시스템 파생 계산값이며 원앱(티마) 산출식과 다를 수 있음.
    """
    if signal_type not in SUPPORTED_STRATEGIES or signal_price <= 0:
        return []

    cur = float(current_price) if current_price is not None else float(signal_price)
    closes = _closes(candles)

    if signal_type in ("f_zone", "sf_zone"):
        return _fzone_levels(float(signal_price), cur, closes)
    if signal_type == "gold_zone":
        return _target_levels(
            float(signal_price), cur, _GOLD_TARGET_RATIOS, prefix="G"
        )
    # swing_38
    return _target_levels(
        float(signal_price), cur, _SWING_TARGET_RATIOS, prefix="J"
    )


def _fzone_levels(signal_price: float, cur: float, closes: List[float]) -> List[dict]:
    """F존/SF존 세트 [SF, B1, B2, B3] — SF > B1 > B2 > B3 강제."""
    sf = signal_price
    raw_supports: List[float] = []
    for period, ratio in zip(_FZONE_SUPPORT_PERIODS, _FZONE_SUPPORT_RATIOS):
        ma = _ma(closes, period)
        raw_supports.append(ma if ma is not None else signal_price * ratio)

    supports = _enforce_descending(raw_supports, ceiling=sf)

    levels = [{"label": "SF", "price": round(sf), "kind": "anchor", "active": False}]
    for i, price in enumerate(supports, start=1):
        levels.append(
            {"label": f"B{i}", "price": round(price), "kind": "support", "active": False}
        )
    _mark_active(levels, cur)
    return levels


def _target_levels(
    signal_price: float, cur: float, ratios: tuple, prefix: str
) -> List[dict]:
    """상방 목표 사다리 (골드존 G1~G3 / 38스윙 J1~J3) — 오름차순 강제."""
    raw = [signal_price * r for r in ratios]
    ordered = _enforce_ascending(raw, floor=None)
    levels = [
        {"label": f"{prefix}{i}", "price": round(price), "kind": "target", "active": False}
        for i, price in enumerate(ordered, start=1)
    ]
    _mark_active(levels, cur)
    return levels


__all__ = ["compute_levels", "SUPPORTED_STRATEGIES"]
