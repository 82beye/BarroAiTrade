"""6월 트랩(가짜 상승/개미 꼬시기) 방어 — ATR 정규화 진입 가드.

config-gated, **default-OFF**. `TrapGuardConfig` 의 모든 서브룰 임계가 0 이면
`evaluate_trap_guard` 는 항상 `(False, "off")` 를 반환 → 기존 진입 경로 byte-identical.

6월 장 반복 패턴(사용자 관찰):
- 고갭/장대양봉으로 개미 유인 후 페이드 (전일比 +15~20% 갭 추격 손실)
- 상단 긴 윗꼬리 = 상단 매도세 출현 = 가짜 돌파
- 기준선 대비 과확장 = "너무 멀리 너무 빨리" 추격

세 룰 모두 `atr_pct`(종목 변동성)로 정규화 → 저변동주엔 타이트하게, 고변동주엔
관대하게 작동(절대 임계의 한계 보완). ATR 은 1회 계산해 룰들이 공유.

근거 재사용:
- 윗꼬리 룰은 `closing_bet.py:143-147` 의 `upper_wick_ratio` 정의를 zone 전략으로
  일반화(중복 제거·공백 메우기 — closing_bet/short_term_high_exit 에만 있던 룰).
- ATR% 는 `indicators.atr_pct` 단일 소스 사용(전략별 `_atr_pct` wrapper 와 수치 동일).

활성화는 측정 후 (d) HITL — 본 모듈은 기능만 제공하며 default 로는 라이브 무변경.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from backend.core.strategy.indicators import atr_pct
from backend.models.market import OHLCV


@dataclass(frozen=True)
class TrapGuardConfig:
    """트랩 가드 임계. 모든 필드 0 = 비활성(default-OFF parity)."""

    # (a) 과확장(over-extension): close 가 기준선 대비 over_ext_k_atr × ATR% 초과 시 차단.
    #     "너무 멀리 너무 빨리" 추격 차단. 0 = 비활성.
    over_ext_k_atr: float = 0.0
    over_ext_baseline: str = "ma"          # "ma" | "vwap" | "impulse_open"
    over_ext_ma_period: int = 20
    # (b) 윗꼬리 거부: (high-close)/(close-open) > upper_wick_max 시 차단. 0 = 비활성.
    upper_wick_max: float = 0.0
    # (c) 고갭 ATR화: gap%(전일比 또는 bar-gap proxy) > gap_atr_mult × ATR%×100 차단.
    #     기존 절대% 게이트(_ZONE_MAX_FLU)와 직교/보완 — 저변동주에서만 추가 차단. 0 = 비활성.
    gap_atr_mult: float = 0.0
    gap_abs_max_pct: float = 0.0           # 절대% OR 게이트(내부 backstop). 0 = 비활성.
    atr_n: int = 14

    def any_enabled(self) -> bool:
        return (
            self.over_ext_k_atr > 0
            or self.upper_wick_max > 0
            or self.gap_atr_mult > 0
            or self.gap_abs_max_pct > 0
        )


def _sma(values: List[float], period: int) -> Optional[float]:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def _vwap(candles: List[OHLCV]) -> Optional[float]:
    tot_v = sum(float(c.volume) for c in candles)
    if tot_v <= 0:
        return None
    tp = sum(((float(c.high) + float(c.low) + float(c.close)) / 3.0) * float(c.volume) for c in candles)
    return tp / tot_v


def evaluate_trap_guard(
    candles: List[OHLCV],
    cfg: TrapGuardConfig,
    *,
    flu_rate: Optional[float] = None,
) -> Tuple[bool, str]:
    """트랩 가드 평가 → (blocked, reason).

    - 모든 서브룰 비활성(임계 0) 또는 캔들 < 2 → (False, "off") [default-OFF parity].
    - 데이터 부족(기준선 산출 불가 등) 시 해당 룰만 skip(보수적으로 통과시킴 = 시그널 안 죽임).
    - flu_rate(전일比 등락률 %) 가 주어지면 고갭 룰에 사용, 없으면 bar-gap proxy
      `(last.open - prev.close)/prev.close×100` 로 대체.
    """
    if not cfg.any_enabled() or len(candles) < 2:
        return (False, "off")

    last = candles[-1]
    close = float(last.close)
    if close <= 0:
        return (False, "off")

    av = atr_pct(candles, n=cfg.atr_n)     # 0.0 ~ 1.0 (ATR/close)

    # (b) 윗꼬리 거부 — closing_bet.py:143-147 수치 parity (양봉일 때만).
    if cfg.upper_wick_max > 0:
        body_abs = float(last.close) - float(last.open)
        if body_abs > 0:
            wick = (float(last.high) - float(last.close)) / body_abs
            if wick > cfg.upper_wick_max:
                return (True, f"upper_wick {wick:.2f} > {cfg.upper_wick_max:.2f}")

    # (a) 과확장 — close 가 기준선 대비 k×ATR% 초과(변동성 정규화 거리).
    if cfg.over_ext_k_atr > 0 and av > 0:
        if cfg.over_ext_baseline == "vwap":
            baseline = _vwap(candles)
        elif cfg.over_ext_baseline == "impulse_open":
            baseline = float(last.open)
        else:  # "ma"
            baseline = _sma([float(c.close) for c in candles], cfg.over_ext_ma_period)
        if baseline and baseline > 0:
            ext = (close - baseline) / baseline
            if ext > cfg.over_ext_k_atr * av:
                return (
                    True,
                    f"over_ext {ext:.3f} > {cfg.over_ext_k_atr:.1f}xATR {av:.3f}",
                )

    # (c) 고갭 ATR화 — flu_rate(전일比%) 또는 bar-gap proxy. ATR 게이트 OR 절대% 게이트.
    if cfg.gap_atr_mult > 0 or cfg.gap_abs_max_pct > 0:
        gap = flu_rate
        if gap is None:
            prev_close = float(candles[-2].close)
            if prev_close > 0:
                gap = (float(last.open) - prev_close) / prev_close * 100.0
        if gap is not None:
            atr_gate = cfg.gap_atr_mult * av * 100.0 if cfg.gap_atr_mult > 0 else None
            abs_gate = cfg.gap_abs_max_pct if cfg.gap_abs_max_pct > 0 else None
            blocked_atr = atr_gate is not None and gap > atr_gate
            blocked_abs = abs_gate is not None and gap > abs_gate
            if blocked_atr or blocked_abs:
                fired = []
                if blocked_atr:
                    fired.append(f"atr={atr_gate:.1f}")
                if blocked_abs:
                    fired.append(f"abs={abs_gate:.1f}")
                return (True, f"gap {gap:.1f}% > {'/'.join(fired)}")

    return (False, "ok")


__all__ = ["TrapGuardConfig", "evaluate_trap_guard"]
