"""distribution 청산 게이트 (d) 테스트 — DistributionExitConfig + holding_evaluator 통합.

거버넌스: config-gated default-OFF. enabled=False(default) → 청산 평가 무변경(byte-identical).
OOS 검증: docs/04-report/features/2026-06-22-dante-oos-validation.report.md.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from backend.core.gateway.kiwoom_native_account import HoldingPosition
from backend.core.risk.holding_evaluator import (
    ExitPolicy,
    PositionContext,
    SellSignal,
    evaluate_holding,
)
from backend.core.strategy.dante_filters import DistributionExitConfig
from backend.models.market import MarketType, OHLCV

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _c(close, *, open=None, vol=1.0):
    o = open if open is not None else close
    return OHLCV(symbol="X", timestamp=_TS, open=o, high=max(o, close),
                 low=min(o, close), close=close, volume=vol, market_type=MarketType.STOCK)


def _uptrend_distribution_candles():
    """정배열(종가>SMA60) + 마지막봉 거래량 전일×4 장대음봉(몸통 5%)."""
    base = [_c(100 + i * 0.3, vol=100) for i in range(60)]   # 완만 상승(종가>SMA60)
    prev = _c(118, vol=100)
    drop = _c(112, open=118, vol=400)                         # 음봉 몸통 ~5%, 거래량 4배
    return base + [prev, drop]


def _h(pnl_rate="3.0"):
    return HoldingPosition(
        symbol="005930", name="삼성전자", qty=10,
        avg_buy_price=Decimal("100"), cur_price=Decimal("112"),
        eval_amount=Decimal("1120"), pnl=Decimal("120"), pnl_rate=Decimal(pnl_rate),
    )


# ── DistributionExitConfig ──
def test_config_default_off_fires_false():
    cfg = DistributionExitConfig()
    assert cfg.enabled is False
    assert cfg.fires(_uptrend_distribution_candles()) is False  # disabled → 항상 False


def test_config_from_policy_config_defaults_off():
    class P:  # PolicyConfig 동등 (필드 없음 → default)
        pass
    cfg = DistributionExitConfig.from_policy_config(P())
    assert cfg.enabled is False and cfg.vol_mult == 3.0 and cfg.body_min == 0.03


def test_config_fires_true_on_uptrend_distribution():
    cfg = DistributionExitConfig(enabled=True)
    assert cfg.fires(_uptrend_distribution_candles()) is True


def test_config_no_fire_without_uptrend():
    cfg = DistributionExitConfig(enabled=True)
    # 하락 추세(종가<SMA60)에서는 정배열 게이트로 차단
    down = [_c(200 - i, vol=100) for i in range(60)]
    down += [_c(140, vol=100), _c(133, open=140, vol=400)]  # 장대음봉이나 추세 하방
    assert cfg.fires(down) is False


def test_config_no_fire_without_volume_spike():
    cfg = DistributionExitConfig(enabled=True)
    base = [_c(100 + i * 0.3, vol=100) for i in range(60)]
    weak = base + [_c(118, vol=100), _c(112, open=118, vol=150)]  # 거래량 1.5배 < 3배
    assert cfg.fires(weak) is False


def test_config_from_policy_config_enabled():
    class P:
        distribution_exit_enabled = True
        distribution_exit_vol_mult = 3.0
        distribution_exit_body_min = 0.03
    cfg = DistributionExitConfig.from_policy_config(P())
    assert cfg.enabled is True
    assert cfg.fires(_uptrend_distribution_candles()) is True


# ── holding_evaluator 통합 ──
def test_evaluate_holding_disabled_is_parity():
    """distribution_exit 미주입/disabled → 기존 평가와 동일(여기선 HOLD)."""
    h = _h("1.0")  # 익절·손절·트레일링 미발동 구간
    base = evaluate_holding(h, ExitPolicy(), PositionContext(strategy=""))
    with_disabled = evaluate_holding(
        h, ExitPolicy(),
        PositionContext(strategy="", daily_candles=_uptrend_distribution_candles(),
                        distribution_exit=DistributionExitConfig(enabled=False)),
    )
    assert base.signal == with_disabled.signal == SellSignal.HOLD


def test_evaluate_holding_enabled_fires_full_exit():
    h = _h("1.0")
    d = evaluate_holding(
        h, ExitPolicy(),
        PositionContext(strategy="", daily_candles=_uptrend_distribution_candles(),
                        distribution_exit=DistributionExitConfig(enabled=True)),
    )
    assert d.signal == SellSignal.DISTRIBUTION
    assert d.sell_qty == h.qty  # 전량 청산


def test_evaluate_holding_enabled_no_signal_holds():
    """enabled 라도 distribution 미발생(거래량 약함) → DISTRIBUTION 아님."""
    h = _h("1.0")
    base = [_c(100 + i * 0.3, vol=100) for i in range(60)]
    weak = base + [_c(118, vol=100), _c(112, open=118, vol=150)]
    d = evaluate_holding(
        h, ExitPolicy(),
        PositionContext(strategy="", daily_candles=weak,
                        distribution_exit=DistributionExitConfig(enabled=True)),
    )
    assert d.signal != SellSignal.DISTRIBUTION


def test_evaluate_holding_no_daily_candles_holds():
    h = _h("1.0")
    d = evaluate_holding(
        h, ExitPolicy(),
        PositionContext(strategy="", daily_candles=None,
                        distribution_exit=DistributionExitConfig(enabled=True)),
    )
    assert d.signal != SellSignal.DISTRIBUTION
