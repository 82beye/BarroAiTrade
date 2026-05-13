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
from backend.core.backtester.intraday_simulator import (
    _atr_pct,
    _build_strategies,
    _exit_plan_for_strategy,
    _scaled_exit_plan,
)
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

    def test_build_autoloads_provider_when_unset(self):
        """provider 명시 미지정 시 backend.legacy_scalping._provider 가 auto-load 됨."""
        strats = _build_strategies(["scalping_consensus"])
        assert strats[0].health_check()["provider_registered"] is True

    def test_build_warns_when_autoload_fails(self, caplog, monkeypatch):
        """auto-load 실패(예: legacy 모듈 import 에러) 시 warning + provider 미등록."""
        import backend.legacy_scalping._provider as legacy_provider_mod

        def _boom(*_a, **_kw):
            raise RuntimeError("simulated legacy module failure")

        monkeypatch.setattr(legacy_provider_mod, "build_scalping_provider", _boom)
        with caplog.at_level(
            logging.WARNING,
            logger="backend.core.backtester.intraday_simulator",
        ):
            strats = _build_strategies(["scalping_consensus"])
        assert any("auto-provider 로드 실패" in r.message for r in caplog.records)
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

    def test_default_simulator_uses_autoloaded_provider(self):
        """provider 미지정 → auto-load 된 ScalpingCoordinator wrapper 가 적용된다.

        synthetic 캔들로는 진짜 ScalpingCoordinator 의 진입 조건이 충족되지 않을
        가능성이 높음(빈약한 데이터). 본 테스트는 'auto-load 자체가 동작'함을
        검증하는 데 집중 — sim.run() 이 예외 없이 정상 완료되는지 확인."""
        sim = IntradaySimulator(warmup_candles=15, position_qty=Decimal("10"))
        result = sim.run(
            _synthetic_candles(80),
            symbol="005930",
            strategies=["scalping_consensus"],
        )
        # 정상 완료 + 결과 객체 검증 (trades 수는 ScalpingCoordinator 판단에 따라 0+)
        assert "scalping_consensus" in result.strategies_run
        assert isinstance(result.pnl_by_strategy.get("scalping_consensus"), Decimal)


# ─── D: ATR 기반 동적 SL (2026-05-14) ──────────────────────────────────────


class TestAtrDynamicSL:
    """ATR 기반 동적 SL — 종목 변동성 적응 (LESSON_FZONE_MAX_GAIN.md 후속)."""

    def _flat_then_volatile(self, n: int = 30) -> list[OHLCV]:
        """일정 가격 base 100 + 마지막 봉만 high-low 변동성 큰 합성."""
        out = []
        t0 = datetime(2026, 5, 1, 9, 0)
        for i in range(n - 1):
            out.append(OHLCV(
                symbol="TEST", timestamp=t0 + timedelta(minutes=i),
                open=100, high=101, low=99, close=100, volume=1000,
                market_type=MarketType.STOCK,
            ))
        # 마지막 봉: TR 큰 봉 (high=110, low=95 → TR=15, atr%≈3% 정도)
        out.append(OHLCV(
            symbol="TEST", timestamp=t0 + timedelta(minutes=n - 1),
            open=100, high=110, low=95, close=100, volume=2000,
            market_type=MarketType.STOCK,
        ))
        return out

    def test_atr_pct_basic(self):
        """평탄 캔들의 ATR% 는 약 2% (high-low=2 / close=100)."""
        out = []
        t0 = datetime(2026, 5, 1, 9, 0)
        for i in range(20):
            out.append(OHLCV(
                symbol="X", timestamp=t0 + timedelta(minutes=i),
                open=100, high=101, low=99, close=100, volume=1000,
                market_type=MarketType.STOCK,
            ))
        atr_pct = _atr_pct(out, n=14)
        assert Decimal("0.015") <= atr_pct <= Decimal("0.025"), (
            f"평탄 캔들 atr_pct={atr_pct}, 약 2% 예상"
        )

    def test_atr_pct_empty_safe(self):
        """캔들 1개 이하 → 0 반환 (안전)."""
        assert _atr_pct([], n=14) == Decimal("0")

    def test_scaled_exit_plan_default_sl(self):
        """sl_pct 미지정 시 default -1.5% 적용 (기존 동작 보존)."""
        plan = _scaled_exit_plan(Decimal("100"))
        assert plan.stop_loss.fixed_pct == Decimal("-0.015")

    def test_scaled_exit_plan_custom_sl(self):
        """sl_pct 지정 시 적용."""
        plan = _scaled_exit_plan(Decimal("100"), sl_pct=Decimal("-0.04"))
        assert plan.stop_loss.fixed_pct == Decimal("-0.04")

    def test_exit_plan_fzone_uses_fixed(self):
        """f_zone → 고정 _scaled_exit_plan (BEFORE 동작 보존)."""
        plan = _exit_plan_for_strategy(
            "f_zone", Decimal("100"), self._flat_then_volatile(30),
        )
        assert plan.stop_loss.fixed_pct == Decimal("-0.015")
        # 고정 TP +3/+5/+7%
        assert plan.take_profits[0].price == Decimal("103")
        assert plan.take_profits[1].price == Decimal("105")
        assert plan.take_profits[2].price == Decimal("107")

    def test_exit_plan_sfzone_uses_atr(self):
        """sf_zone → ATR 기반 동적 TP·SL. R:R 균형 유지 (SL=2×ATR, TP=1.5/2.5/3.5×ATR)."""
        candles = self._flat_then_volatile(30)
        plan = _exit_plan_for_strategy("sf_zone", Decimal("100"), candles)
        # SL 은 ATR×2, floor 1.5% / cap 8% 클램프 적용 후 음수
        assert plan.stop_loss.fixed_pct < Decimal("0")
        # SL 절대값 >= floor 1.5% × 2 = 3% (clamp 적용)
        assert plan.stop_loss.fixed_pct <= Decimal("-0.03"), (
            f"sf_zone SL={plan.stop_loss.fixed_pct} — 평탄+큰봉 1개 시 |SL|≥3% 예상"
        )
        # TP1 < TP2 < TP3 (오름차순) — R:R 균형
        tps = [t.price for t in plan.take_profits]
        assert tps[0] < tps[1] < tps[2]

    def test_exit_plan_other_strategies_use_fixed(self):
        """gold_zone, swing_38, scalping_consensus → 고정 (BEFORE 보존)."""
        candles = self._flat_then_volatile(30)
        for sid in ("gold_zone", "swing_38", "scalping_consensus"):
            plan = _exit_plan_for_strategy(sid, Decimal("100"), candles)
            assert plan.stop_loss.fixed_pct == Decimal("-0.015"), f"{sid} SL drift"
            assert plan.take_profits[0].price == Decimal("103"), f"{sid} TP drift"

    def test_sfzone_atr_floor_clamp(self):
        """sf_zone — 극도로 평탄한 캔들이면 SL floor (×2 적용 후 −3%) 로 클램프."""
        out = []
        t0 = datetime(2026, 5, 1, 9, 0)
        for i in range(20):
            out.append(OHLCV(
                symbol="X", timestamp=t0 + timedelta(minutes=i),
                open=100, high=100.05, low=99.95, close=100,
                volume=1000, market_type=MarketType.STOCK,
            ))
        plan = _exit_plan_for_strategy("sf_zone", Decimal("100"), out)
        # ATR≈0.001, floor 0.015 적용 → SL = -0.015×2 = -0.03
        assert plan.stop_loss.fixed_pct == Decimal("-0.030")

    def test_position_value_default_disabled(self):
        """position_value 미지정 → position_qty 그대로 (기존 100주 고정)."""
        sim = IntradaySimulator(position_qty=Decimal("100"))
        assert sim._compute_entry_qty(Decimal("10000")) == Decimal("100")

    def test_position_value_normal_price(self):
        """가격 ≤ 100만원 → 1M 한도. price=1000 → qty=1000, price=50000 → qty=20."""
        sim = IntradaySimulator(position_value=Decimal("1000000"))
        assert sim._compute_entry_qty(Decimal("1000")) == Decimal("1000")
        assert sim._compute_entry_qty(Decimal("50000")) == Decimal("20")
        assert sim._compute_entry_qty(Decimal("111300")) == Decimal("8")  # LG전자 8주

    def test_position_value_high_price_threshold(self):
        """가격 > 100만원 → 200만원 한도 적용."""
        sim = IntradaySimulator(position_value=Decimal("1000000"))
        # price = 1,500,000 (>1M) → budget 2M → qty = 1 (floor(2M/1.5M))
        assert sim._compute_entry_qty(Decimal("1500000")) == Decimal("1")
        # price = 2,000,000 → qty = 1 (floor(2M/2M))
        assert sim._compute_entry_qty(Decimal("2000000")) == Decimal("1")
        # price = 2,500,000 → qty = 0 (>200만원 한도 → 진입 거부)
        assert sim._compute_entry_qty(Decimal("2500000")) == Decimal("0")

    def test_position_value_exactly_at_threshold(self):
        """가격 = 100만원 정확히 → 일반 분기 (1M 한도) → qty=1."""
        sim = IntradaySimulator(position_value=Decimal("1000000"))
        assert sim._compute_entry_qty(Decimal("1000000")) == Decimal("1")

    def test_position_value_custom_threshold(self):
        """threshold/budget 커스텀."""
        sim = IntradaySimulator(
            position_value=Decimal("500000"),
            high_price_threshold=Decimal("100000"),
            high_price_budget=Decimal("1000000"),
        )
        assert sim._compute_entry_qty(Decimal("50000")) == Decimal("10")  # 일반
        assert sim._compute_entry_qty(Decimal("200000")) == Decimal("5")  # 고가주: 1M/200k=5

    def test_sfzone_atr_cap_clamp(self):
        """sf_zone — 극도로 변동성 큰 캔들이면 SL cap (×2 = −16%) 로 클램프."""
        out = []
        t0 = datetime(2026, 5, 1, 9, 0)
        for i in range(20):
            out.append(OHLCV(
                symbol="X", timestamp=t0 + timedelta(minutes=i),
                open=100, high=130, low=70, close=100,
                volume=1000, market_type=MarketType.STOCK,
            ))
        plan = _exit_plan_for_strategy("sf_zone", Decimal("100"), out)
        # ATR≈0.60, cap 0.08 적용 → SL = -0.08×2 = -0.16
        assert plan.stop_loss.fixed_pct == Decimal("-0.16")
