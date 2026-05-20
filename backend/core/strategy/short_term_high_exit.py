"""단기 고점 캔들 인식 매도 시그널 (2026-05-21).

이미지 패턴 (142280 5/20 09:18 매도 기준선 / 09:43 매도 시그널) 코드화.

조건 (3가지 패턴):
  1. DOJI         — 단기 고점 봉이 도지 (body/range < doji_body_ratio)
  2. UPPER_WICK   — 단기 고점 봉의 위 꼬리가 body × upper_wick_min_ratio 이상 (셀러 출현)
  3. RED_FOLLOW   — 단기 고점 직후 음봉 출현 (close < open + 직전 high 이하)

선행 조건 (필수):
  - 최근 N봉 내 high 갱신 (peak_lookback_min 봉, default 30)
  - peak 시점 대비 현재 봉 high 가 peak 가깝거나 같음 (within tolerance)

backend/core/risk/holding_evaluator.py 에서 평가 가능하도록 함수형.
입력: 1분봉 시퀀스 (최근 N봉)
출력: ShortTermHighExitResult (signal: bool, reason, pattern)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from backend.models.market import OHLCV


PatternType = Literal["doji", "upper_wick", "red_follow"]


@dataclass(frozen=True)
class ShortTermHighExitResult:
    signal: bool                     # True 면 매도 권고
    pattern: Optional[PatternType]   # 발동된 패턴
    peak_high: float                 # 단기 고점 가격
    current_high: float              # 현재 봉 high
    body_pct: float                  # body / range (도지 판정)
    upper_wick_ratio: float          # upper_wick / body (위 꼬리 판정)
    reason: str                      # 설명 문자열


def _body_metrics(bar: OHLCV) -> tuple[float, float, float]:
    """봉 분석 → (body_abs, range_abs, body_pct)."""
    body = abs(bar.close - bar.open)
    rng = bar.high - bar.low
    body_pct = body / rng if rng > 0 else 0.0
    return body, rng, body_pct


def _upper_wick_ratio(bar: OHLCV) -> float:
    """위 꼬리 / body 비율. body=0 (도지) 면 무한대."""
    body = abs(bar.close - bar.open)
    upper_wick = bar.high - max(bar.open, bar.close)
    if body <= 0:
        return float("inf") if upper_wick > 0 else 0.0
    return upper_wick / body


def detect_short_term_high_exit(
    candles: list[OHLCV],
    *,
    peak_lookback: int = 30,
    peak_tolerance_pct: float = 0.003,  # peak 대비 0.3% 안이면 "고점 근접"
    doji_body_ratio: float = 0.15,       # body/range < 0.15 → 도지
    upper_wick_min_ratio: float = 1.0,   # upper_wick > body × 1.0 (위꼬리 ≥ body)
    upper_wick_min_pct: float = 0.005,   # 위꼬리 ≥ 가격 × 0.5% (절대 길이 필터)
) -> ShortTermHighExitResult:
    """단기 고점 캔들 인식 매도 신호.

    candles : 분봉 시퀀스 (시간 오름차순). 최소 2봉 필요.
              마지막 봉이 평가 대상 (현재 봉).

    return  : ShortTermHighExitResult — signal=True 면 매도 권고
    """
    n = len(candles)
    if n < 2:
        return ShortTermHighExitResult(
            signal=False, pattern=None, peak_high=0.0, current_high=0.0,
            body_pct=0.0, upper_wick_ratio=0.0, reason="데이터 부족",
        )

    cur = candles[-1]
    # peak_lookback 봉 (현재봉 포함) 의 최고가
    lookback = min(peak_lookback, n)
    window = candles[-lookback:]
    peak_high = max(b.high for b in window)

    # 현재 봉 high 가 peak 근접인지 (peak 갱신 또는 직전 peak 근방)
    if peak_high <= 0:
        return ShortTermHighExitResult(
            signal=False, pattern=None, peak_high=peak_high, current_high=cur.high,
            body_pct=0.0, upper_wick_ratio=0.0, reason="peak 0",
        )
    proximity = (peak_high - cur.high) / peak_high
    near_peak = proximity <= peak_tolerance_pct

    if not near_peak:
        return ShortTermHighExitResult(
            signal=False, pattern=None, peak_high=peak_high, current_high=cur.high,
            body_pct=0.0, upper_wick_ratio=0.0,
            reason=f"고점 미근접 (peak {peak_high:.0f} vs cur high {cur.high:.0f}, prox {proximity*100:.2f}%)",
        )

    _, rng, body_pct = _body_metrics(cur)
    wick_ratio = _upper_wick_ratio(cur)
    upper_wick_abs = cur.high - max(cur.open, cur.close)

    # 1. DOJI — body/range 작음
    if body_pct < doji_body_ratio:
        return ShortTermHighExitResult(
            signal=True, pattern="doji", peak_high=peak_high, current_high=cur.high,
            body_pct=body_pct, upper_wick_ratio=wick_ratio,
            reason=f"단기 고점 도지 (body/range {body_pct:.2%} < {doji_body_ratio:.2%})",
        )

    # 2. UPPER_WICK — 위 꼬리 긴 음봉 또는 양봉 (셀러 출현)
    wick_pct = upper_wick_abs / cur.close if cur.close > 0 else 0.0
    if wick_ratio >= upper_wick_min_ratio and wick_pct >= upper_wick_min_pct:
        return ShortTermHighExitResult(
            signal=True, pattern="upper_wick", peak_high=peak_high, current_high=cur.high,
            body_pct=body_pct, upper_wick_ratio=wick_ratio,
            reason=(
                f"단기 고점 위꼬리 (wick/body {wick_ratio:.2f}x ≥ "
                f"{upper_wick_min_ratio}x, wick/price {wick_pct:.2%})"
            ),
        )

    # 3. RED_FOLLOW — 현재 봉이 음봉 + 직전 봉이 peak 봉 (또는 양봉)
    is_red = cur.close < cur.open
    if is_red and n >= 2:
        prev = candles[-2]
        prev_was_peak = abs(prev.high - peak_high) / peak_high <= peak_tolerance_pct
        if prev_was_peak:
            return ShortTermHighExitResult(
                signal=True, pattern="red_follow", peak_high=peak_high, current_high=cur.high,
                body_pct=body_pct, upper_wick_ratio=wick_ratio,
                reason=f"단기 고점 직후 음봉 (peak {peak_high:.0f}, cur close {cur.close:.0f})",
            )

    return ShortTermHighExitResult(
        signal=False, pattern=None, peak_high=peak_high, current_high=cur.high,
        body_pct=body_pct, upper_wick_ratio=wick_ratio,
        reason="고점 근접이나 도지·위꼬리·음봉 미충족",
    )


__all__ = [
    "ShortTermHighExitResult",
    "PatternType",
    "detect_short_term_high_exit",
]
