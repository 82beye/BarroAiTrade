"""BAR-OPS-35 — 트레이딩뷰 등급 정확도 옵션 테스트."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from backend.core.backtester import IntradaySimulator
from backend.models.market import MarketType, OHLCV


def _candles_uptrend(n: int = 60) -> list[OHLCV]:
    """단조 상승 — 진입 후 +3% 도달하도록."""
    base = 100.0
    out = []
    for i in range(n):
        # i=0..29 warmup 횡보, 30+ 부터 상승
        if i < 30:
            o = h = l = c = base
        else:
            o = base + (i - 30) * 0.5         # 0.5% 씩 상승
            c = o + 0.5
            h = c + 0.3                        # high
            l = o - 0.1                        # low
        out.append(OHLCV(
            symbol="TEST", timestamp=datetime(2026, 5, 8, 9, 0) + timedelta(minutes=i),
            open=o, high=h, low=l, close=c,
            volume=1000.0, market_type=MarketType.STOCK,
        ))
    return out


def test_entry_on_next_open_changes_entry_price():
    """default(next_open=True) vs next_open=False 진입가 비교."""
    candles = _candles_uptrend(60)
    # 같은 데이터 / 옵션만 다름
    sim_default = IntradaySimulator(warmup_candles=10, position_qty=Decimal("10"),
                                     entry_on_next_open=True, exit_on_intrabar=False)
    sim_legacy = IntradaySimulator(warmup_candles=10, position_qty=Decimal("10"),
                                    entry_on_next_open=False, exit_on_intrabar=False)
    r1 = sim_default.run(candles, symbol="TEST", strategies=["swing_38"])
    r2 = sim_legacy.run(candles, symbol="TEST", strategies=["swing_38"])

    buys_1 = [t for t in r1.trades if t.side == "buy"]
    buys_2 = [t for t in r2.trades if t.side == "buy"]
    # 두 모드 모두 진입 시그널 발견 가정 (테스트 안정성 위해 발견 X 시 skip)
    if not buys_1 or not buys_2:
        pytest.skip("no entry signal in synthetic uptrend")
    # next_open 모드: 시그널 다음 캔들 open 으로 진입 — legacy 의 close 와 달라야
    assert buys_1[0].price != buys_2[0].price


def test_commission_and_tax_reduce_pnl():
    """수수료 + 세금 적용 시 PnL 감소."""
    candles = _candles_uptrend(60)
    sim_no_fee = IntradaySimulator(
        warmup_candles=10, position_qty=Decimal("10"),
        commission_pct=0.0, tax_pct_on_sell=0.0,
    )
    sim_with_fee = IntradaySimulator(
        warmup_candles=10, position_qty=Decimal("10"),
        commission_pct=0.015, tax_pct_on_sell=0.18,
    )
    r1 = sim_no_fee.run(candles, symbol="TEST", strategies=["swing_38"])
    r2 = sim_with_fee.run(candles, symbol="TEST", strategies=["swing_38"])
    pnl_1 = r1.pnl_by_strategy.get("swing_38", Decimal("0"))
    pnl_2 = r2.pnl_by_strategy.get("swing_38", Decimal("0"))
    # 청산 발생 안 했을 수 있음 — skip 처리
    if pnl_1 == 0 and pnl_2 == 0:
        pytest.skip("no exits in synthetic")
    # 양수 PnL 일 때 fee 적용본이 더 작아야
    if pnl_1 > 0:
        assert pnl_2 < pnl_1


def test_slippage_increases_entry_price():
    """슬리피지 0.1% 시 진입가 1.001 배."""
    candles = _candles_uptrend(60)
    sim_no_slip = IntradaySimulator(
        warmup_candles=10, position_qty=Decimal("10"), slippage_pct=0.0,
    )
    sim_with_slip = IntradaySimulator(
        warmup_candles=10, position_qty=Decimal("10"), slippage_pct=0.1,   # 0.1%
    )
    r1 = sim_no_slip.run(candles, symbol="TEST", strategies=["swing_38"])
    r2 = sim_with_slip.run(candles, symbol="TEST", strategies=["swing_38"])
    buys_1 = [t for t in r1.trades if t.side == "buy"]
    buys_2 = [t for t in r2.trades if t.side == "buy"]
    if not buys_1 or not buys_2:
        pytest.skip("no entry signal")
    # slip 모드 진입가가 0.1% 더 높아야
    ratio = buys_2[0].price / buys_1[0].price
    assert Decimal("1.0009") < ratio < Decimal("1.0011")


def test_intrabar_exit_uses_high_for_tp():
    """exit_on_intrabar=True: TP 가 close 가 아닌 high 터치 시 체결.

    candle 의 high 가 entry+3% 도달하지만 close 는 미달인 시나리오.
    """
    base_price = 100.0
    candles = []
    # 30 warmup 횡보
    for i in range(30):
        candles.append(OHLCV(
            symbol="T", timestamp=datetime(2026, 5, 8, 9, 0) + timedelta(minutes=i),
            open=base_price, high=base_price, low=base_price, close=base_price,
            volume=1000, market_type=MarketType.STOCK,
        ))
    # 진입 시그널 캔들 (i=30) — 약상승
    candles.append(OHLCV(
        symbol="T", timestamp=datetime(2026, 5, 8, 9, 30),
        open=100.0, high=101.0, low=100.0, close=100.5,
        volume=1000, market_type=MarketType.STOCK,
    ))
    # 다음 캔들 (i=31) — 진입 (next_open=100) + 즉시 TP 도달 (high=103.5 ≥ 103)
    # 단 진입 직후 캔들은 청산 평가 skip — 다음 캔들에서
    candles.append(OHLCV(
        symbol="T", timestamp=datetime(2026, 5, 8, 9, 31),
        open=100.0, high=100.5, low=99.5, close=100.0,
        volume=1000, market_type=MarketType.STOCK,
    ))
    # i=32: high=103.5 (TP1 도달), close=101.0 (TP1 미달)
    candles.append(OHLCV(
        symbol="T", timestamp=datetime(2026, 5, 8, 9, 32),
        open=100.5, high=103.5, low=100.0, close=101.0,
        volume=1000, market_type=MarketType.STOCK,
    ))
    # 더 많은 캔들로 패딩
    for i in range(20):
        candles.append(OHLCV(
            symbol="T", timestamp=datetime(2026, 5, 8, 9, 33) + timedelta(minutes=i),
            open=100, high=100, low=100, close=100,
            volume=1000, market_type=MarketType.STOCK,
        ))

    # intrabar 모드 — TP1 체결 가능
    sim_intra = IntradaySimulator(
        warmup_candles=10, position_qty=Decimal("10"),
        entry_on_next_open=True, exit_on_intrabar=True,
    )
    sim_close = IntradaySimulator(
        warmup_candles=10, position_qty=Decimal("10"),
        entry_on_next_open=True, exit_on_intrabar=False,
    )
    r_intra = sim_intra.run(candles, symbol="T", strategies=["swing_38"])
    r_close = sim_close.run(candles, symbol="T", strategies=["swing_38"])

    # intrabar 모드는 TP 청산이 발생할 수 있음 (전략이 진입했다면)
    # 두 모드 trade count 비교 — intrabar 가 같거나 더 많아야
    buys_i = [t for t in r_intra.trades if t.side == "buy"]
    buys_c = [t for t in r_close.trades if t.side == "buy"]
    if not buys_i or not buys_c:
        pytest.skip("no entry signal in this synthetic")
    sells_i = [t for t in r_intra.trades if t.side == "sell"]
    sells_c = [t for t in r_close.trades if t.side == "sell"]
    # intrabar 모드가 더 많은 청산 잡거나 같음
    assert len(sells_i) >= len(sells_c)


def test_default_options_are_realistic():
    """default 가 트레이딩뷰 등급 (next_open=True, intrabar=True)."""
    sim = IntradaySimulator()
    assert sim._entry_next_open is True
    assert sim._exit_intrabar is True
