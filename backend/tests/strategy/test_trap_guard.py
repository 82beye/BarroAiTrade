"""trap_guard 단위테스트 — 6월 트랩(가짜 상승/개미 꼬시기) 합성 패턴 차단 + default-OFF parity.

검증 축:
- 고갭 추격(+15~20% 갭 후 페이드) 차단 (flu_rate / bar-gap proxy 양 경로)
- 긴 윗꼬리 장대양봉 차단 (closing_bet 수치 parity)
- 기준선 대비 과확장 차단
- 정상 눌림목은 통과 (휩쏘 방지 — winning 시그널 안 죽임)
- 모든 룰 0 → 항상 (False, "off") [default-OFF]
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.core.strategy.trap_guard import TrapGuardConfig, evaluate_trap_guard
from backend.models.market import MarketType, OHLCV

_KST = timezone(timedelta(hours=9))


def _flat(n: int, price: float = 100_000.0, rng: float = 0.02) -> list[OHLCV]:
    """n개 평탄 캔들: open=close=price, high/low=price×(1±rng/2) → TR≈price×rng → ATR%≈rng."""
    base = datetime(2026, 6, 1, tzinfo=_KST)
    return [
        OHLCV(
            symbol="T", timestamp=base + timedelta(minutes=i),
            open=price, high=price * (1 + rng / 2), low=price * (1 - rng / 2),
            close=price, volume=1_000_000.0, market_type=MarketType.STOCK,
        )
        for i in range(n)
    ]


def _bar(open_px: float, close_px: float, high_px: float, low_px: float) -> OHLCV:
    return OHLCV(
        symbol="T", timestamp=datetime(2026, 6, 18, 15, 0, tzinfo=_KST),
        open=open_px, high=high_px, low=low_px, close=close_px,
        volume=3_000_000.0, market_type=MarketType.STOCK,
    )


# ── (c) 고갭 ATR화 ──────────────────────────────────────────────────────────
class TestGapAtr:
    def test_gap_atr_blocks_fade_with_flu_rate(self):
        """전일比 +18% 고갭 · ATR% 2% · mult 3(gate=6%) → 18%>6% 차단."""
        candles = _flat(20, rng=0.02) + [_bar(118_000, 119_000, 119_500, 117_500)]
        cfg = TrapGuardConfig(gap_atr_mult=3.0)
        blocked, reason = evaluate_trap_guard(candles, cfg, flu_rate=18.0)
        assert blocked, reason
        assert "gap" in reason

    def test_gap_proxy_blocks_without_flu_rate(self):
        """flu_rate 없으면 bar-gap proxy: 마지막봉 시가갭 +18% → 차단."""
        candles = _flat(20, price=100_000.0, rng=0.02)
        candles.append(_bar(118_000, 119_000, 119_500, 117_500))  # open 118k vs prev close 100k = +18%
        cfg = TrapGuardConfig(gap_atr_mult=3.0)
        blocked, reason = evaluate_trap_guard(candles, cfg)
        assert blocked, reason

    def test_gap_abs_backstop(self):
        """절대% 게이트(OR): gap 16% > gap_abs_max_pct 15% → 차단."""
        candles = _flat(20, rng=0.05) + [_bar(116_000, 117_000, 117_500, 115_500)]
        cfg = TrapGuardConfig(gap_abs_max_pct=15.0)
        blocked, _ = evaluate_trap_guard(candles, cfg, flu_rate=16.0)
        assert blocked

    def test_low_gap_passes(self):
        """+3% 갭은 통과(gate 6% 미만)."""
        candles = _flat(20, rng=0.02) + [_bar(103_000, 103_500, 104_000, 102_500)]
        cfg = TrapGuardConfig(gap_atr_mult=3.0)
        blocked, _ = evaluate_trap_guard(candles, cfg, flu_rate=3.0)
        assert not blocked


# ── (b) 윗꼬리 거부 ─────────────────────────────────────────────────────────
class TestUpperWick:
    def test_long_upper_wick_rejected(self):
        """몸통 +5% · 윗꼬리=몸통×2 → wick 2.0 > 1.0 차단."""
        # open 100k, close 105k(body_abs 5k), high = close + 2×body = 115k
        candles = _flat(20) + [_bar(100_000, 105_000, 115_000, 99_000)]
        cfg = TrapGuardConfig(upper_wick_max=1.0)
        blocked, reason = evaluate_trap_guard(candles, cfg)
        assert blocked
        assert "upper_wick" in reason

    def test_closing_bet_parity(self):
        """closing_bet 와 동일 정의: (high-close)/(close-open). 경계 1.0 — 동일 임계 동일 판정."""
        # wick ratio = (high-close)/(close-open) = (1.0 경계 직상)
        candles = _flat(20) + [_bar(100_000, 105_000, 110_100, 99_000)]  # (110100-105000)/5000=1.02
        cfg = TrapGuardConfig(upper_wick_max=1.0)
        blocked, _ = evaluate_trap_guard(candles, cfg)
        assert blocked  # 1.02 > 1.0

    def test_small_wick_passes(self):
        """짧은 윗꼬리(0.2) → 통과."""
        candles = _flat(20) + [_bar(100_000, 105_000, 106_000, 99_000)]  # (106000-105000)/5000=0.2
        cfg = TrapGuardConfig(upper_wick_max=1.0)
        blocked, _ = evaluate_trap_guard(candles, cfg)
        assert not blocked

    def test_red_candle_no_wick_block(self):
        """음봉(body_abs<=0)은 윗꼬리 룰 미적용(closing_bet 동일)."""
        candles = _flat(20) + [_bar(105_000, 100_000, 120_000, 99_000)]  # 음봉
        cfg = TrapGuardConfig(upper_wick_max=1.0)
        blocked, _ = evaluate_trap_guard(candles, cfg)
        assert not blocked


# ── (a) 과확장 ──────────────────────────────────────────────────────────────
class TestOverExtension:
    def test_over_extension_blocks(self):
        """close 가 MA20 대비 +12% · k 2.5 → ext > k×ATR% 차단."""
        candles = _flat(20, price=100_000.0, rng=0.02)
        candles.append(_bar(111_000, 112_000, 112_100, 110_900))  # close 12% 위
        cfg = TrapGuardConfig(over_ext_k_atr=2.5, over_ext_ma_period=20)
        blocked, reason = evaluate_trap_guard(candles, cfg)
        assert blocked, reason
        assert "over_ext" in reason

    def test_near_ma_passes(self):
        """close 가 MA 근처(+0.5%) → 과확장 아님."""
        candles = _flat(20, price=100_000.0, rng=0.02)
        candles.append(_bar(100_000, 100_500, 100_600, 99_500))
        cfg = TrapGuardConfig(over_ext_k_atr=2.5, over_ext_ma_period=20)
        blocked, _ = evaluate_trap_guard(candles, cfg)
        assert not blocked


# ── 정상 시그널 통과 (휩쏘 방지) + default-OFF ──────────────────────────────
class TestSafety:
    def test_normal_pullback_passes_all_rules_on(self):
        """정상 F존 눌림목: 모든 룰 ON 이어도 통과(winning 시그널 안 죽임)."""
        candles = _flat(20, price=100_000.0, rng=0.02)
        candles.append(_bar(100_000, 100_500, 100_600, 99_500))  # 작은 양봉, MA 근처, 갭 0
        cfg = TrapGuardConfig(
            over_ext_k_atr=2.5, upper_wick_max=1.0, gap_atr_mult=3.0,
            over_ext_ma_period=20,
        )
        blocked, reason = evaluate_trap_guard(candles, cfg)
        assert not blocked, reason

    def test_all_rules_off_is_noop(self):
        """모든 임계 0 → 항상 (False, 'off') [default-OFF parity]. 어떤 캔들이든."""
        trap = _flat(20) + [_bar(100_000, 105_000, 130_000, 99_000)]  # 극단 트랩
        cfg = TrapGuardConfig()  # 전부 default 0
        assert not cfg.any_enabled()
        blocked, reason = evaluate_trap_guard(trap, cfg)
        assert (blocked, reason) == (False, "off")

    def test_too_few_candles_off(self):
        cfg = TrapGuardConfig(upper_wick_max=1.0)
        assert evaluate_trap_guard([_bar(100_000, 105_000, 130_000, 99_000)], cfg) == (False, "off")
