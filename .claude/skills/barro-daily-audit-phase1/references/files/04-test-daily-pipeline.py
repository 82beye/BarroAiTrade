"""Phase 1 — daily 운영 audit 도구 단위 + 스모크 테스트."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal

from backend.models.market import MarketType, OHLCV
from scripts._daily_evening_pipeline import (
    aggregate_by_symbol,
    attribute_from_logs,
    attribute_strategy,
    compute_net,
    normalize_execution,
    run_pipeline,
    update_ledger,
)
from scripts._loss_drill_down import diagnose
from scripts._strategy_perf_track import aggregate, load_ledger


# ─── normalize_execution ─────────────────────────────────────────────────


def test_normalize_kt00009_buy_fill():
    raw = {
        "code": "A027360", "name": "오공", "trade_type": "현금매수",
        "filled_qty": "10", "filled_price": "5230", "time": "090530",
    }
    e = normalize_execution(raw)
    assert e["code"] == "027360"        # A 접두 제거
    assert e["side"] == "buy"
    assert e["qty"] == Decimal("10")
    assert e["price"] == Decimal("5230")


def test_normalize_detects_sell():
    e = normalize_execution({"code": "005930", "order_type": "현금매도",
                             "qty": 5, "price": 70000})
    assert e["side"] == "sell"


# ─── compute_net ─────────────────────────────────────────────────────────


def test_compute_net_includes_commission_and_tax():
    # gross 50,000 − 수수료 307.5 − 세금 1,890 = 47,802.5
    net = compute_net(Decimal("10000"), Decimal("10500"), Decimal("100"))
    assert net == Decimal("47802.5")


def test_compute_net_loss_is_negative():
    net = compute_net(Decimal("10000"), Decimal("9800"), Decimal("100"))
    assert net < 0


# ─── aggregate_by_symbol ─────────────────────────────────────────────────


def test_aggregate_matches_buy_sell_and_marks_result():
    execs = [
        normalize_execution({"code": "111111", "trade_type": "매수",
                             "qty": 100, "price": 10000, "time": "090100"}),
        normalize_execution({"code": "111111", "trade_type": "매도",
                             "qty": 100, "price": 10500, "time": "100000"}),
    ]
    out = aggregate_by_symbol(execs)
    assert out["111111"]["result"] == "익절"
    assert out["111111"]["qty"] == Decimal("100")
    assert out["111111"]["net"] > 0


def test_aggregate_open_position_when_no_sell():
    execs = [normalize_execution({"code": "222222", "trade_type": "매수",
                                  "qty": 10, "price": 5000})]
    out = aggregate_by_symbol(execs)
    assert out["222222"]["result"] == "open"
    assert out["222222"]["net"] == Decimal(0)


# ─── attribute_strategy (다단계 fallback) ─────────────────────────────────


class _FakePos:
    def __init__(self, strategy):
        self.strategy = strategy


def test_attribute_tier1_active_positions():
    aps = {"027360": _FakePos("gold_zone")}
    assert attribute_strategy("027360", aps, "", None) == "gold_zone"


def test_attribute_tier2_logs_fallback():
    logs = "09:05 [BUY] 027360 오공 swing_38 진입 score=0.8"
    assert attribute_from_logs("027360", logs) == "swing_38"
    assert attribute_strategy("027360", {}, logs, None) == "swing_38"


def test_attribute_tier3_sim_fallback():
    assert attribute_strategy("027360", {}, "", lambda c: "f_zone") == "f_zone"


def test_attribute_unknown_when_all_fail():
    assert attribute_strategy("027360", {}, "", None) == "unknown"


# ─── update_ledger (idempotent) ──────────────────────────────────────────


def test_update_ledger_replaces_same_date(tmp_path):
    ledger = tmp_path / "strategy_ledger.csv"
    row = lambda d, net: {"date": d, "symbol": "111111", "name": "x",
                          "strategy": "f_zone", "buy_avg": 1, "sell_avg": 2,
                          "qty": 1, "net": net, "result": "익절"}
    update_ledger(ledger, "2026-05-21", [row("2026-05-21", 100)])
    update_ledger(ledger, "2026-05-21", [row("2026-05-21", 200)])   # 재실행
    rows = load_ledger(ledger)
    assert len(rows) == 1 and rows[0]["net"] == "200"

    update_ledger(ledger, "2026-05-22", [row("2026-05-22", 300)])
    assert len(load_ledger(ledger)) == 2


# ─── _strategy_perf_track.aggregate ──────────────────────────────────────


def test_perf_aggregate_groups_by_date_strategy():
    rows = [
        {"date": "2026-05-21", "strategy": "gold_zone", "net": "318000",
         "result": "익절"},
        {"date": "2026-05-21", "strategy": "swing_38", "net": "-389000",
         "result": "손실"},
        {"date": "2026-05-21", "strategy": "swing_38", "net": "317000",
         "result": "익절"},
    ]
    agg = aggregate(rows)
    swing = next(a for a in agg if a["strategy"] == "swing_38")
    assert swing["net"] == -72000
    assert swing["n_symbols"] == 2
    assert swing["win_rate"] == 0.5


# ─── 스모크: run_pipeline end-to-end ─────────────────────────────────────


def test_run_pipeline_smoke(tmp_path):
    import_dir = tmp_path / "imports" / "2026-05-21"
    data_dir = import_dir / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "active_positions.json").write_text(json.dumps({
        "027360": {
            "symbol": "027360", "name": "오공", "strategy": "f_zone",
            "entry_price": 5230, "entry_time": "2026-05-21T00:05:30",
            "total_recommended_qty": 100, "tranches": [],
        }
    }), encoding="utf-8")

    executions = [
        {"code": "027360", "trade_type": "매수", "filled_qty": 100,
         "filled_price": 5230, "time": "090530"},
        {"code": "027360", "trade_type": "매도", "filled_qty": 100,
         "filled_price": 4700, "time": "143000"},
    ]
    rows = run_pipeline("2026-05-21", executions, import_dir)

    assert len(rows) == 1
    r = rows[0]
    assert r["symbol"] == "027360"
    assert r["strategy"] == "f_zone"        # Tier 1 active_positions
    assert r["result"] == "손실"
    assert r["net"] < 0
    assert (import_dir / "executions.json").exists()  # drill-down 입력


# ─── _loss_drill_down.diagnose ───────────────────────────────────────────


def _candle(minute: int, close: float, high: float, low: float):
    ts = datetime(2026, 5, 21, 9, 0) + timedelta(minutes=minute)
    return OHLCV(symbol="T", timestamp=ts, open=close, high=high, low=low,
                 close=close, volume=1000, market_type=MarketType.STOCK)


def test_diagnose_handles_float_candles_and_detects_immediate_drop():
    # 캔들 high/low 는 float, entry_price 는 Decimal — 혼용 TypeError 회귀 방지
    candles = [_candle(i, 10000, 10010, 9990) for i in range(8)]
    candles += [_candle(i, 9700, 9720, 9680) for i in range(8, 12)]  # 진입 후 하락
    tags = diagnose(candles, entry_idx=8, exit_idx=11,
                    entry_price=Decimal("10000"), exit_price=Decimal("9700"))
    assert any("진입 직후 즉시 하락" in t for t in tags)
    assert any("매물대 미인식" in t for t in tags)
