"""BAR-OPS-19 — markdown_report 테스트."""
from __future__ import annotations

from decimal import Decimal

from backend.core.gateway.kiwoom_native_rank import LeaderCandidate
from backend.core.journal.markdown_report import (
    render_daily_report,
    render_gate_recommendations,
    render_history_by_run,
    render_history_by_strategy,
    render_leader_table,
    render_simulation_summary,
)
from backend.core.journal.simulation_log import SimulationLogEntry
from backend.core.risk.balance_gate import (
    PositionRecommendation,
    RiskGateResult,
)


def _candidate(**kw) -> LeaderCandidate:
    base = dict(
        symbol="005930", name="삼성전자",
        cur_price=276500.0, flu_rate=1.84,
        rank_trade_value=1, rank_flu_rate=None, rank_volume=6,
        score=0.685,
    )
    base.update(kw)
    return LeaderCandidate(**base)


def _entry(**kw) -> SimulationLogEntry:
    base = dict(
        run_at="2026-05-08T13:00:00+00:00", mode="daily",
        symbol="005930", name="삼성전자", strategy="swing_38",
        candle_count=600, trades=4, pnl=337600.0, win_rate=1.0,
        score=0.685, flu_rate=1.84,
    )
    base.update(kw)
    return SimulationLogEntry(**base)


def _gate() -> RiskGateResult:
    return RiskGateResult(
        cash=Decimal("48930069"),
        current_eval=Decimal("0"),
        available=Decimal("44037062"),
        max_per_position=Decimal("14679021"),
        max_total_position=Decimal("44037062"),
        recommendations=[
            PositionRecommendation(
                symbol="319400", name="현대무벡스",
                cur_price=Decimal("37700"), max_value=Decimal("14679021"),
                recommended_qty=389, blocked=False,
            ),
            PositionRecommendation(
                symbol="010170", name="대한광통신",
                cur_price=Decimal("22350"), max_value=Decimal("0"),
                recommended_qty=0, blocked=True, reason="자금 한도 소진",
            ),
        ],
    )


# -- individual renders ------------------------------------------------------


def test_render_leader_table_includes_all_columns():
    md = render_leader_table([_candidate(), _candidate(symbol="012330", name="현대모비스")])
    assert "| rank |" in md
    assert "005930" in md
    assert "삼성전자" in md
    assert "012330" in md
    # 헤더 + 구분선 + 2 데이터 = 4 줄
    assert len(md.split("\n")) == 4


def test_render_simulation_summary():
    md = render_simulation_summary(
        total_trades=110,
        total_pnl=7458231.0,
        per_strategy_pnl={"swing_38": 7458231.0, "f_zone": 0.0},
    )
    assert "| 총 거래 | 110 |" in md
    assert "+7,458,231" in md
    assert "swing_38" in md


def test_render_gate_recommendations():
    md = render_gate_recommendations(_gate())
    assert "| 예수금 | 48,930,069" in md
    assert "현대무벡스" in md
    assert "OK" in md
    assert "자금 한도 소진" in md


def test_render_history_by_strategy_sorts_by_pnl():
    entries = [
        _entry(strategy="A", pnl=100.0),
        _entry(strategy="B", pnl=500.0),
        _entry(strategy="C", pnl=-200.0),
    ]
    md = render_history_by_strategy(entries)
    # B 가 첫 데이터 행 (PnL 가장 큼)
    rows = [l for l in md.split("\n") if l.startswith("| B ") or l.startswith("| A ") or l.startswith("| C ")]
    assert rows[0].startswith("| B ")
    assert rows[-1].startswith("| C ")


def test_render_history_by_run_sorts_chronologically():
    entries = [
        _entry(run_at="2026-05-09T13:00", pnl=1.0),
        _entry(run_at="2026-05-08T13:00", pnl=2.0),
    ]
    md = render_history_by_run(entries)
    lines = md.split("\n")
    data = [l for l in lines if "2026-05-0" in l]
    assert data[0].startswith("| 2026-05-08")
    assert data[1].startswith("| 2026-05-09")


# -- combined daily report ---------------------------------------------------


def test_render_daily_report_full():
    md = render_daily_report(
        title="2026-05-08 일일 시뮬 리포트",
        leaders=[_candidate()],
        total_trades=110,
        total_pnl=7458231.0,
        per_strategy_pnl={"swing_38": 7458231.0},
        gate=_gate(),
        history_entries=[_entry()],
        executed_orders=[
            {"symbol": "319400", "name": "현대무벡스", "qty": 389,
             "order_no": "DRY_RUN", "status": "DRY_RUN"},
        ],
    )
    assert "# 2026-05-08 일일 시뮬 리포트" in md
    assert "## 1. 당일 주도주" in md
    assert "## 2. 시뮬 결과" in md
    assert "## 3. 잔고 + 추천 매수" in md
    assert "## 4. 주문 실행 결과" in md
    assert "## 5. 누적 history" in md
    # 종목·전략 모두 포함
    assert "005930" in md
    assert "swing_38" in md
    assert "319400" in md
    assert "현대무벡스" in md


def test_render_daily_report_minimal_skips_optional_sections():
    md = render_daily_report(
        title="minimal",
        leaders=[_candidate()],
        total_trades=0,
        total_pnl=0.0,
        per_strategy_pnl={},
    )
    assert "## 3." not in md
    assert "## 4." not in md
    assert "## 5." not in md
