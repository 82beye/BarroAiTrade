"""고도화 Phase 0 — _daily_strategy_audit 순수 로직 테스트 (I/O 없음).

FIFO 실현손익 / 진입위치 / §6.3 알람 로직 검증.
"""
from __future__ import annotations

from scripts._daily_strategy_audit import (
    fifo_roundtrip_pnl,
    entry_position_pct,
    strategy_alarms,
)

COMM = 0.00015
TAX = 0.0018


def _cost(sell_value: float, basis: float) -> float:
    return (sell_value + basis) * COMM + sell_value * TAX


class TestFifoRoundtripPnl:
    def test_simple_win(self):
        r = fifo_roundtrip_pnl([
            {"side": "buy", "qty": 10, "price": 100},
            {"side": "sell", "qty": 10, "price": 110},
        ])
        expected = (1100 - 1000) - _cost(1100, 1000)
        assert abs(r["realized"] - expected) < 1e-6
        assert r["wins"] == 1 and r["sells"] == 1 and r["n_buys"] == 1

    def test_simple_loss(self):
        r = fifo_roundtrip_pnl([
            {"side": "buy", "qty": 10, "price": 100},
            {"side": "sell", "qty": 10, "price": 95},
        ])
        assert r["realized"] < 0
        assert r["wins"] == 0 and r["sells"] == 1

    def test_fifo_partial_match(self):
        r = fifo_roundtrip_pnl([
            {"side": "buy", "qty": 10, "price": 100},
            {"side": "buy", "qty": 10, "price": 120},
            {"side": "sell", "qty": 15, "price": 130},
        ])
        # FIFO: 10@100 + 5@120 = basis 1600, sval 1950
        expected = (1950 - 1600) - _cost(1950, 1600)
        assert abs(r["realized"] - expected) < 1e-6
        assert r["matched_basis"] == 1600

    def test_dca_then_sell(self):
        # T1 60% + T2(DCA) 40% 후 전량 매도 — 평단 기반 실현
        r = fifo_roundtrip_pnl([
            {"side": "buy", "qty": 6, "price": 5770},
            {"side": "buy", "qty": 4, "price": 5690},
            {"side": "sell", "qty": 10, "price": 5400},
        ])
        assert r["realized"] < 0  # 고점매수→하락 = 손실
        assert r["n_buys"] == 2 and r["sells"] == 1

    def test_zero_and_negative_skipped(self):
        r = fifo_roundtrip_pnl([
            {"side": "buy", "qty": 0, "price": 100},
            {"side": "buy", "qty": 10, "price": -5},
            {"side": "sell", "qty": 10, "price": 110},
        ])
        assert r["n_buys"] == 0  # qty<=0, price<=0 무시


class TestEntryPosition:
    def test_at_low(self):
        assert entry_position_pct(80, 80, 90) == 0.0

    def test_at_high(self):
        assert entry_position_pct(90, 80, 90) == 100.0

    def test_mid(self):
        assert entry_position_pct(87, 80, 90) == 70.0

    def test_above_high_clamped(self):
        assert entry_position_pct(95, 80, 90) == 100.0

    def test_zero_range(self):
        assert entry_position_pct(80, 80, 80) is None


class TestStrategyAlarms:
    def test_gold_overtrade_low_winrate(self):
        per = {"gold_zone": {"realized": -1.0, "matched_basis": 100.0, "wins": 5, "sells": 25}}
        alarms = strategy_alarms(per)
        assert any("gold_zone" in a and "비활성" in a for a in alarms)

    def test_capital_loss_alarm(self):
        per = {"f_zone": {"realized": -100.0, "matched_basis": 1000.0, "wins": 1, "sells": 3}}
        alarms = strategy_alarms(per)
        assert any("자본가중" in a for a in alarms)

    def test_no_alarm_healthy(self):
        per = {"f_zone": {"realized": 50.0, "matched_basis": 1000.0, "wins": 3, "sells": 4}}
        assert strategy_alarms(per) == []

    def test_gold_winrate_ok_no_alarm(self):
        # 거래 적으면(<20) 과매매 알람 미발화
        per = {"gold_zone": {"realized": 10.0, "matched_basis": 1000.0, "wins": 1, "sells": 2}}
        assert strategy_alarms(per) == []
