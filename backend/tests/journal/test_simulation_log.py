"""BAR-OPS-13 — SimulationLogger + summary 테스트."""
from __future__ import annotations

from backend.core.journal.simulation_log import (
    SimulationLogEntry,
    SimulationLogger,
    summarize_by_run,
    summarize_by_strategy,
)


def _entry(run_at: str = "2026-05-08T13:00:00+00:00", **kw) -> SimulationLogEntry:
    base = dict(
        run_at=run_at, mode="daily", symbol="005930", name="삼성전자",
        strategy="swing_38", candle_count=600, trades=4,
        pnl=337600.0, win_rate=1.0, score=0.685, flu_rate=1.84,
    )
    base.update(kw)
    return SimulationLogEntry(**base)


def test_append_creates_file_with_header(tmp_path):
    p = tmp_path / "log.csv"
    logger = SimulationLogger(p)
    n = logger.append([_entry()])
    assert n == 1
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert content.startswith("run_at,mode,symbol")
    assert "005930" in content


def test_append_existing_file_no_duplicate_header(tmp_path):
    p = tmp_path / "log.csv"
    logger = SimulationLogger(p)
    logger.append([_entry()])
    logger.append([_entry(symbol="000660", name="SK하이닉스")])
    content = p.read_text(encoding="utf-8")
    # header 1줄 + data 2줄 = 3 lines
    assert content.count("run_at,mode") == 1
    assert "005930" in content
    assert "000660" in content


def test_read_all_round_trip(tmp_path):
    p = tmp_path / "log.csv"
    logger = SimulationLogger(p)
    logger.append([
        _entry(symbol="005930"),
        _entry(symbol="000660", name="SK하이닉스", pnl=-50000.0),
    ])
    rows = logger.read_all()
    assert len(rows) == 2
    assert rows[0].symbol == "005930"
    assert rows[0].pnl == 337600.0
    assert rows[1].symbol == "000660"
    assert rows[1].pnl == -50000.0


def test_read_all_missing_file_returns_empty(tmp_path):
    p = tmp_path / "nonexistent.csv"
    assert SimulationLogger(p).read_all() == []


def test_append_empty_iterable_noop(tmp_path):
    p = tmp_path / "log.csv"
    logger = SimulationLogger(p)
    assert logger.append([]) == 0
    assert not p.exists()


def test_summarize_by_strategy():
    entries = [
        _entry(strategy="swing_38", trades=4, pnl=300.0, win_rate=1.0),
        _entry(strategy="swing_38", trades=2, pnl=-100.0, win_rate=0.5, symbol="A"),
        _entry(strategy="gold_zone", trades=10, pnl=500.0, win_rate=0.8, symbol="B"),
    ]
    s = summarize_by_strategy(entries)
    assert s["swing_38"]["runs"] == 2
    assert s["swing_38"]["total_pnl"] == 200.0
    assert s["swing_38"]["total_trades"] == 6
    # weighted win_rate: (1.0*4 + 0.5*2) / 6 = 5/6
    assert abs(s["swing_38"]["win_rate"] - 5 / 6) < 1e-6
    assert s["gold_zone"]["total_pnl"] == 500.0


def test_summarize_by_run_groups_and_sorts():
    entries = [
        _entry(run_at="2026-05-08T13:00", symbol="A", pnl=100.0),
        _entry(run_at="2026-05-08T13:00", symbol="B", pnl=200.0),
        _entry(run_at="2026-05-09T13:00", symbol="A", pnl=300.0),
    ]
    runs = summarize_by_run(entries)
    assert len(runs) == 2
    assert runs[0]["run_at"] == "2026-05-08T13:00"
    assert runs[0]["total_pnl"] == 300.0
    assert runs[0]["symbol_count"] == 2
    assert runs[1]["run_at"] == "2026-05-09T13:00"
    assert runs[1]["total_pnl"] == 300.0
    assert runs[1]["symbol_count"] == 1
