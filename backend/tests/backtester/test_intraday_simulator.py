"""BAR-OPS-08 — IntradaySimulator (10 cases)."""
from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from backend.core.backtester import (
    IntradaySimulator,
    SimulationResult,
    TradeRecord,
    load_csv_candles,
)
from backend.core.backtester.intraday_simulator import _build_strategies
from backend.core.strategy.scalping_consensus import ScalpingConsensusStrategy
from backend.models.market import MarketType, OHLCV


def _synthetic_candles(n: int = 50, base: float = 70000) -> list[OHLCV]:
    """단순 상승 추세 합성 데이터."""
    out = []
    t0 = datetime(2026, 5, 8, 9, 0)
    price = base
    for i in range(n):
        # 사인 + 노이즈 — 상승 추세
        delta = (i % 10 - 4) * 50 + i * 30
        c = base + delta
        out.append(
            OHLCV(
                symbol="005930",
                timestamp=t0 + timedelta(minutes=i),
                open=c,
                high=c + 100,
                low=c - 80,
                close=c + 20,
                volume=10000 + i * 100,
                market_type=MarketType.STOCK,
            )
        )
    return out


# ─── CSV 로더 ─────────────────────────────────────────────


class TestCSVLoader:
    def test_load_csv(self, tmp_path):
        p = tmp_path / "data.csv"
        with open(p, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            w.writerow(["2026-05-08 09:00:00", 70000, 70200, 69900, 70100, 10000])
            w.writerow(["2026-05-08 09:01:00", 70100, 70300, 70000, 70250, 12000])
        candles = load_csv_candles(p, symbol="005930")
        assert len(candles) == 2
        assert candles[0].symbol == "005930"
        assert candles[0].close == 70100

    def test_load_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_csv_candles("nonexistent.csv")


# ─── Simulator ────────────────────────────────────────────


class TestSimulatorBasics:
    def test_too_few_candles_raises(self):
        sim = IntradaySimulator()
        with pytest.raises(ValueError, match="≥"):
            sim.run(_synthetic_candles(5), symbol="005930")

    def test_unknown_strategy(self):
        sim = IntradaySimulator(warmup_candles=10)
        with pytest.raises(ValueError, match="unknown strategy"):
            sim.run(_synthetic_candles(50), symbol="005930", strategies=["invalid"])

    def test_run_returns_result(self):
        sim = IntradaySimulator(warmup_candles=10)
        result = sim.run(
            _synthetic_candles(50),
            symbol="005930",
            strategies=["f_zone"],
        )
        assert isinstance(result, SimulationResult)
        assert result.symbol == "005930"
        assert result.candle_count == 50
        assert "f_zone" in result.strategies_run


class TestStrategyExecution:
    def test_single_strategy_runs(self):
        sim = IntradaySimulator(warmup_candles=15, position_qty=Decimal("10"))
        result = sim.run(
            _synthetic_candles(60),
            symbol="005930",
            strategies=["f_zone"],
        )
        # 시그널이 발생하면 trades 가 0 보다 크고, pnl 항목 존재
        assert "f_zone" in result.pnl_by_strategy
        assert result.pnl_by_strategy["f_zone"] == result.pnl_by_strategy["f_zone"]  # not nan

    def test_multiple_strategies(self):
        sim = IntradaySimulator(warmup_candles=15, position_qty=Decimal("10"))
        result = sim.run(
            _synthetic_candles(80),
            symbol="005930",
            strategies=["f_zone", "sf_zone"],
        )
        assert len(result.strategies_run) == 2
        assert "f_zone" in result.pnl_by_strategy
        assert "sf_zone" in result.pnl_by_strategy

    def test_summary_format(self):
        sim = IntradaySimulator(warmup_candles=15)
        result = sim.run(
            _synthetic_candles(50),
            symbol="005930",
            strategies=["f_zone"],
        )
        s = result.summary()
        assert "005930" in s
        assert "f_zone" in s
        assert "PnL" in s


class TestTradeRecord:
    def test_frozen(self):
        t = TradeRecord(
            strategy_id="x", symbol="y", side="buy",
            qty=Decimal("10"), price=Decimal("100"),
            timestamp=datetime.now(),
        )
        with pytest.raises(Exception):
            t.qty = Decimal("20")  # type: ignore[misc]

    def test_side_value(self):
        t = TradeRecord(
            strategy_id="x", symbol="y", side="sell",
            qty=Decimal("5"), price=Decimal("100"),
            timestamp=datetime.now(),
        )
        assert t.side == "sell"


# ─── BAR-OPS-09: scalping_consensus provider 주입 ──────────────────────────


class TestScalpingProviderInjection:
    """scalping_consensus 가 시뮬에 포함될 때 provider 미주입 → 0건 영구 버그
    해소를 위한 인터페이스 검증."""

    def _high_score_provider(self, ctx):
        last = ctx.candles[-1]
        return {
            "code": ctx.symbol,
            "name": "TEST",
            "price": float(last.close),
            "total_score": 85.0,
            "timing": "즉시",
            "consensus_level": "다수합의",
            "top_reasons": ["unit test"],
        }

    def _low_score_provider(self, ctx):
        last = ctx.candles[-1]
        return {
            "code": ctx.symbol,
            "name": "TEST",
            "price": float(last.close),
            "total_score": 30.0,  # 0.30 < threshold 0.65 → 차단
            "timing": "관망",
        }

    def test_build_injects_provider(self):
        """_build_strategies 가 ScalpingConsensusStrategy 에 provider 를 주입한다."""
        provider = self._high_score_provider
        strats = _build_strategies(["scalping_consensus"], scalping_provider=provider)
        assert len(strats) == 1
        s = strats[0]
        assert isinstance(s, ScalpingConsensusStrategy)
        assert s.health_check()["provider_registered"] is True

    def test_build_without_provider_warns(self, caplog):
        """provider 미주입 시 warning 로그 — 조용한 0건 방지."""
        with caplog.at_level(logging.WARNING, logger="backend.core.backtester.intraday_simulator"):
            strats = _build_strategies(["scalping_consensus"])
        assert any("scalping_consensus" in r.message for r in caplog.records)
        assert strats[0].health_check()["provider_registered"] is False

    def test_simulator_propagates_provider(self):
        """IntradaySimulator(scalping_provider=...) 가 run() 시 주입된다."""
        sim = IntradaySimulator(
            warmup_candles=15,
            position_qty=Decimal("10"),
            scalping_provider=self._high_score_provider,
        )
        result = sim.run(
            _synthetic_candles(80),
            symbol="005930",
            strategies=["scalping_consensus"],
        )
        # provider 가 매 캔들 high_score 반환 → 적어도 1회 진입 발생
        sc_trades = [t for t in result.trades if t.strategy_id == "scalping_consensus"]
        assert len(sc_trades) >= 1, "provider 주입에도 trade 0 — 주입 실패"
        # buy 가 최소 한 번 발생
        assert any(t.side == "buy" for t in sc_trades)

    def test_low_score_provider_no_entry(self):
        """provider 가 threshold 미달 score 반환 → 진입 0건 (정상 차단)."""
        sim = IntradaySimulator(
            warmup_candles=15,
            position_qty=Decimal("10"),
            scalping_provider=self._low_score_provider,
        )
        result = sim.run(
            _synthetic_candles(80),
            symbol="005930",
            strategies=["scalping_consensus"],
        )
        sc_trades = [t for t in result.trades if t.strategy_id == "scalping_consensus"]
        assert len(sc_trades) == 0

    def test_default_simulator_no_provider_backward_compat(self):
        """기존 호출(provider 미지정) 동작 보존: scalping_consensus 포함돼도 0건."""
        sim = IntradaySimulator(warmup_candles=15, position_qty=Decimal("10"))
        result = sim.run(
            _synthetic_candles(80),
            symbol="005930",
            strategies=["scalping_consensus"],
        )
        sc_trades = [t for t in result.trades if t.strategy_id == "scalping_consensus"]
        assert len(sc_trades) == 0
