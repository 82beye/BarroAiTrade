"""BAR-OPS-39 — 6/11 매매복기 권고 구현 테스트.

비용 중앙화 / fill 실측 매핑 / 데몬 컷오프 / 재진입 가격조건 / 매도 경합 가드.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── 비용 중앙화 ──────────────────────────────────────────────────────────────

class TestTradingCosts:
    def test_measured_defaults(self):
        """실측 기본값(2026-06-21 정정) — 편도 0.35% / 매도세 0.20% / 왕복 0.90%.

        fill_audit 298행 재도출: 수수료 1,768,040 / (매수+매도) 505,588,092 = 0.3497%/leg.
        종전 0.00175 는 2배 과소(왕복을 편도로 오라벨 후 반감)였음.
        """
        from backend.core import trading_costs as tc
        assert float(tc.COMMISSION_RATE) == 0.0035
        assert float(tc.TAX_RATE_SELL) == 0.0020
        assert abs(float(tc.ROUND_TRIP_COST_RATE) - 0.009) < 1e-12
        assert tc.COMMISSION_PCT == 0.35
        assert tc.TAX_PCT_ON_SELL == 0.20

    def test_etf_sell_tax_exempt(self):
        from backend.core.trading_costs import sell_tax_rate, TAX_RATE_SELL
        assert sell_tax_rate(is_etf=True) == 0
        assert sell_tax_rate(is_etf=False) == TAX_RATE_SELL

    def test_intraday_simulator_default_costs(self):
        """[6/11 발견] 라이브 선정 경로가 무인자 생성 — default 가 실측 비용이어야 함."""
        from backend.core.backtester.intraday_simulator import IntradaySimulator
        s = IntradaySimulator()
        assert float(s._commission) == 0.0035
        assert float(s._tax) == 0.002

    def test_audit_constants_follow_central(self):
        from scripts._daily_strategy_audit import COMMISSION_RATE, TAX_RATE
        assert COMMISSION_RATE == 0.0035
        assert TAX_RATE == 0.0020

    def test_ob_scalp_breakeven_reflects_measured(self):
        """왕복 0.90%(정정) — 10,000원/틱10 본전틱 ≈ 9.0틱."""
        from backend.core.strategy.ob_scalp import breakeven_ticks, ROUND_TRIP_COST_PCT
        assert abs(ROUND_TRIP_COST_PCT - 0.009) < 1e-12
        assert abs(breakeven_ticks(10_000, 10) - 9.0) < 1e-9


# ── fill_audit 실측 매핑 ─────────────────────────────────────────────────────

class TestMatchFillsToSells:
    def _sell(self, ts, symbol, qty, sid):
        return {"ts": datetime.fromisoformat(ts), "side": "sell",
                "symbol": symbol, "qty": float(qty), "sid": sid}

    def test_multi_strategy_split(self):
        """6/11 미래에셋생명 재현 — gold 86주(40+46) / st 44+45주, 매수기준가 상이."""
        from scripts._daily_strategy_audit import match_fills_to_sells
        fills = [
            {"symbol": "085620", "name": "미래에셋생명", "qty": "40", "buy_price": "25631.1",
             "sell_price": "24500", "pnl": "-54214.19", "commission": "7010", "tax": "1960"},
            {"symbol": "085620", "name": "미래에셋생명", "qty": "46", "buy_price": "25631.1",
             "sell_price": "24550", "pnl": "-60057.81", "commission": "8070", "tax": "2257"},
            {"symbol": "085620", "name": "미래에셋생명", "qty": "44", "buy_price": "24650",
             "sell_price": "26042", "pnl": "51190", "commission": "0", "tax": "0"},
            {"symbol": "085620", "name": "미래에셋생명", "qty": "45", "buy_price": "24650",
             "sell_price": "26300", "pnl": "63876", "commission": "0", "tax": "0"},
        ]
        sells = [
            self._sell("2026-06-11T02:21:33+00:00", "085620", 86, "gold_zone"),
            self._sell("2026-06-11T02:30:04+00:00", "085620", 44, "supertrend"),
            self._sell("2026-06-11T02:35:22+00:00", "085620", 45, "supertrend"),
        ]
        trips, warns = match_fills_to_sells(fills, sells)
        # 다중 매도주문 정보성 경고(순서 가정 매칭 고지)만 허용 — 수량 불일치/미매칭은 없어야 함
        assert all("다중 매도주문" in w for w in warns)
        assert len(trips) == 3
        gold = [t for t in trips if t["strategy"] == "gold_zone"]
        st = [t for t in trips if t["strategy"] == "supertrend"]
        assert len(gold) == 1 and gold[0]["qty"] == 86
        assert abs(gold[0]["net"] - (-114272.0)) < 0.01
        assert abs(gold[0]["buy_px"] - 25631.1) < 0.01     # 전략별 기준가 교차 오염 없음
        assert len(st) == 2 and abs(sum(t["net"] for t in st) - 115066.0) < 0.01

    def test_unmatched_fill_warns(self):
        from scripts._daily_strategy_audit import match_fills_to_sells
        fills = [{"symbol": "005930", "name": "삼성전자", "qty": "10", "buy_price": "100",
                  "sell_price": "110", "pnl": "100", "commission": "0", "tax": "0"}]
        trips, warns = match_fills_to_sells(fills, [])
        assert trips == []
        assert any("미매칭" in w for w in warns)


class TestLoadFillAudit:
    def test_loads_only_target_date(self, tmp_path, monkeypatch):
        import scripts._daily_strategy_audit as audit
        d = tmp_path / "data"
        d.mkdir()
        (d / "fill_audit.csv").write_text(
            "date,symbol,name,qty,buy_price,sell_price,pnl,pnl_rate,commission,tax\n"
            "20260611,005930,삼성전자,10,100,110,100,1.0,0,0\n"
            "20260610,005930,삼성전자,5,100,90,-50,-1.0,0,0\n",
            encoding="utf-8")
        monkeypatch.setattr(audit, "_REPO", tmp_path)
        rows = audit.load_fill_audit("2026-06-11")
        assert len(rows) == 1 and rows[0]["qty"] == "10"
        assert audit.load_fill_audit("2026-06-09") == []

    def test_missing_file_safe(self, tmp_path, monkeypatch):
        import scripts._daily_strategy_audit as audit
        monkeypatch.setattr(audit, "_REPO", tmp_path)
        assert audit.load_fill_audit("2026-06-11") == []


def test_prev_day_close():
    from scripts._daily_strategy_audit import prev_day_close
    bars = [
        (datetime(2026, 6, 10, 15, 19), 95.0, 105.0, 100.0),
        (datetime(2026, 6, 10, 15, 30), 95.0, 105.0, 102.0),
        (datetime(2026, 6, 11, 9, 0), 110.0, 112.0, 111.0),
    ]
    assert prev_day_close(bars, "2026-06-11") == 102.0
    assert prev_day_close(bars, "2026-06-10") is None


# ── 데몬 진입 컷오프 (gold/f/sf, swing_38 예외) ─────────────────────────────

class TestZoneEntryCutoff:
    def test_exempt_set(self):
        from scripts.intraday_buy_daemon import _CUTOFF_EXEMPT_STRATEGIES
        assert "swing_38" in _CUTOFF_EXEMPT_STRATEGIES
        assert "gold_zone" not in _CUTOFF_EXEMPT_STRATEGIES

    def test_cutoff_time_logic(self, monkeypatch):
        import scripts.intraday_buy_daemon as daemon
        monkeypatch.setattr(daemon, "_ZONE_ENTRY_CUTOFF", "14:30")
        from datetime import timedelta, timezone as tz

        class _T1430:
            @staticmethod
            def time():
                from datetime import time as t
                return t(14, 31)
        monkeypatch.setattr(daemon, "_now_kst", lambda: _T1430)
        assert daemon._zone_entry_cutoff_passed() is True

        class _T1429:
            @staticmethod
            def time():
                from datetime import time as t
                return t(14, 29)
        monkeypatch.setattr(daemon, "_now_kst", lambda: _T1429)
        assert daemon._zone_entry_cutoff_passed() is False

    def test_disabled_when_empty(self, monkeypatch):
        import scripts.intraday_buy_daemon as daemon
        monkeypatch.setattr(daemon, "_ZONE_ENTRY_CUTOFF", "")
        assert daemon._zone_entry_cutoff_passed() is False


# ── st 재진입 가격조건 (opt-in) ─────────────────────────────────────────────

class TestReentryPriceCondition:
    def test_config_defaults_off(self):
        from backend.core.supertrend_auto_trader import SupertrendAutoConfig
        c = SupertrendAutoConfig()
        assert c.reentry_only_below_prev_entry is False
        assert c.reentry_below_tolerance_pct == 0.0

    def test_state_reset_on_roll_day(self):
        from backend.core.supertrend_auto_trader import (
            SupertrendAutoConfig, SupertrendAutoTrader,
        )

        async def _uni():
            return []
        tr = SupertrendAutoTrader(
            candle_fetcher=None, account_fetcher=None, order_gate=None,
            pos_store=None, universe_provider=_uni,
            config=SupertrendAutoConfig(),
        )
        tr._last_entry_px["005930"] = 70000.0
        tr._entry_day = "2000-01-01"   # 강제 롤오버
        tr._roll_day()
        assert tr._last_entry_px == {}
