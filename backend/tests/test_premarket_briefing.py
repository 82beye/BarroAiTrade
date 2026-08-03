from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.premarket_briefing import (
    KST,
    DeliveryState,
    build_predictions_payload,
    build_watchlist_payload,
    deliver_once,
    format_prediction_message,
    format_scan_message,
    format_strategy_message,
    inspect_cache_readiness,
    split_message,
    write_json_atomic,
)
from backend.core.scanner.ai_trade_universe import load_ai_trade_universe
from scripts.premarket_telegram_briefing import load_audit_trade_records
from scripts.update_ohlcv_cache import load_symbols, safe_base_date


NOW = datetime(2026, 8, 3, 8, 25, tzinfo=KST)


@dataclass
class ScanRow:
    code: str
    name: str
    blue_line_status: str = "above"
    watermelon_signal: bool = True
    score: float = 95.0
    volume_ratio: float = 2.4


@dataclass
class PredictionRow:
    rank: int
    code: str
    name: str
    total_score: float = 80.4
    confidence: float = 1.0
    agent_scores: dict[str, float] = field(default_factory=lambda: {
        "momentum": 94, "volume": 33, "technical": 42,
        "breakout": 74, "timing": 40,
    })
    top_reasons: list[str] = field(default_factory=lambda: [
        "[momentum] 4일 연속 상승", "[volume] OBV 상승추세(x0.5)",
    ])
    consensus_level: str = "만장일치"


def test_scan_message_matches_requested_shape_and_truncates_to_ten():
    rows = [ScanRow(f"{i:06d}", f"종목{i}") for i in range(1, 21)]
    text = format_scan_message(rows, generated_at=NOW)
    assert text.startswith("📊 <b>종목 스캔 완료</b> (08:25)\n감시 종목: 20개\n")
    assert "1. 🍉 [000001] 종목1 | above | 점수:95.0" in text
    assert "10. 🍉 [000010] 종목10" in text
    assert "[000011]" not in text
    assert text.endswith("... 외 10종목")


def test_prediction_message_matches_agent_bar_scores_and_reasons():
    rows = [PredictionRow(1, "047040", "대우건설")]
    text = format_prediction_message(rows, generated_at=NOW)
    assert text.startswith("<b>팀 에이전트 상승 예측</b> (08:25)")
    assert "만장일치:1" in text
    assert "1. [047040] 대우건설 <b>80.4점</b> [OOOO] 만장일치" in text
    assert "신뢰도:100%" in text
    assert "MOM:94 | VOL:33 | TEC:42 | BRE:74 | TIM:40" in text
    assert "[momentum] 4일 연속 상승" in text


def test_strategy_message_matches_requested_sections():
    params = SimpleNamespace(
        confidence=.95, entry_start_delay_minutes=5, cooldown_minutes=15,
        max_entries_per_stock=3, max_bb_excess_pct=8.0, max_breakout_pct=7.0,
        stop_loss_pct=-5.0, take_profit_1_pct=3.5, take_profit_2_pct=6.0,
        breakeven_buffer_pct=.3, position_size_multiplier=.5,
        blacklist_codes=["005860", "008350"], stock_boost={},
        stock_penalty={"042940": .5},
        agent_reports=["[trade_pattern] (신뢰도 100%): 반복매수 감지"],
    )
    text = format_strategy_message(params)
    assert "<b>전략 최적화 팀 분석 결과</b>" in text
    assert "종합 신뢰도: 95%" in text
    assert "진입 시작: 09:05" in text
    assert "손절: -5.0%" in text
    assert "익절: +3.5% / +6.0%" in text
    assert "포지션 배율: 50%" in text
    assert "<b>블랙리스트</b>: 005860, 008350" in text
    assert "042940(-50%)" in text
    assert "<b>에이전트 상세</b>" in text


def test_source_payloads_are_directly_consumable_by_ai_swing(tmp_path: Path):
    scan = [ScanRow("047040", "대우건설")]
    pred = [PredictionRow(1, "047040", "대우건설")]
    write_json_atomic(
        tmp_path / "watchlist_2026-08-03.json",
        build_watchlist_payload(scan, day=NOW.date(), generated_at=NOW),
    )
    write_json_atomic(
        tmp_path / "predictions_2026-08-03.json",
        build_predictions_payload(pred, day=NOW.date(), generated_at=NOW),
    )
    result = load_ai_trade_universe(str(tmp_path), today=NOW.date())
    assert result.status == "ok"
    assert result.scan_count == result.pred_count == result.intersect_count == 1
    assert result.items[0].symbol == "047040"
    assert result.items[0].pred_score == 80.4


def test_cache_readiness_requires_real_coverage(tmp_path: Path):
    (tmp_path / "000001.json").write_text('{"data": []}', encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({
        "updated": "2026-08-02", "total_requested": 1,
    }), encoding="utf-8")
    result = inspect_cache_readiness(tmp_path, 100, today=date(2026, 8, 3))
    assert not result.ready
    assert result.reason.startswith("cache_coverage:")


def test_cache_readiness_accepts_weekend_age_with_coverage(tmp_path: Path):
    for i in range(90):
        (tmp_path / f"{i:06d}.json").write_text('{"data": []}', encoding="utf-8")
    (tmp_path / "meta.json").write_text(json.dumps({
        "updated": "2026-07-31", "total_requested": 90,
    }), encoding="utf-8")
    result = inspect_cache_readiness(tmp_path, 100, today=date(2026, 8, 3))
    assert result.ready


class _Notifier:
    def __init__(self, fail_on: int | None = None):
        self.messages: list[str] = []
        self.fail_on = fail_on

    async def send(self, text: str):
        if self.fail_on is not None and len(self.messages) + 1 == self.fail_on:
            raise RuntimeError("send failed")
        self.messages.append(text)
        return {"ok": True}


@pytest.mark.asyncio
async def test_delivery_state_skips_successful_blocks_on_retry(tmp_path: Path):
    state = DeliveryState(tmp_path / "state.json")
    messages = {"scan": "scan", "prediction": "prediction", "strategy": "strategy"}
    first = _Notifier(fail_on=2)
    with pytest.raises(RuntimeError):
        await deliver_once(first, messages, day=NOW.date(), state=state, now=NOW)
    assert first.messages == ["scan"]
    assert state.was_sent(NOW.date(), "scan")
    second = _Notifier()
    result = await deliver_once(second, messages, day=NOW.date(), state=state, now=NOW)
    assert result == {"sent": ["prediction", "strategy"], "skipped": ["scan"]}
    assert second.messages == ["prediction", "strategy"]


def test_split_message_never_exceeds_limit():
    chunks = split_message("a" * 20 + "\n" + "b" * 20, limit=25)
    assert chunks == ["a" * 20, "b" * 20]
    assert all(len(chunk) <= 25 for chunk in chunks)


def test_update_cache_symbols_bootstraps_from_master(tmp_path: Path):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "005930.json").write_text('{"data": []}', encoding="utf-8")
    master = tmp_path / "stock_names.json"
    master.write_text(json.dumps({"000660": "SK하이닉스", "005930": "삼성전자"}), encoding="utf-8")
    assert load_symbols(str(cache), str(master)) == ["000660", "005930"]


def test_update_cache_uses_previous_weekday_before_market_close():
    assert safe_base_date(datetime(2026, 8, 3, 10, 55, tzinfo=KST)) == date(2026, 7, 31)
    assert safe_base_date(datetime(2026, 8, 3, 15, 40, tzinfo=KST)) == date(2026, 8, 3)
    assert safe_base_date(datetime(2026, 8, 2, 18, 0, tzinfo=KST)) == date(2026, 7, 31)


def test_audit_loader_builds_buy_and_sell_records(tmp_path: Path):
    orders = tmp_path / "orders.csv"
    with open(orders, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "ts", "action", "side", "symbol", "qty", "price", "blocked",
            "reason", "filled_qty", "avg_fill_price",
        ])
        writer.writeheader()
        writer.writerow({
            "ts": "2026-07-31T00:10:00+00:00", "action": "ORDERED", "side": "buy",
            "symbol": "005930", "qty": "2", "price": "70000", "blocked": "0",
            "reason": "entry", "filled_qty": "", "avg_fill_price": "",
        })
        writer.writerow({
            "ts": "2026-07-31T01:10:00+00:00", "action": "ORDERED", "side": "sell",
            "symbol": "005930", "qty": "2", "price": "MKT", "blocked": "0",
            "reason": "stop_loss", "filled_qty": "", "avg_fill_price": "",
        })
    fills = tmp_path / "fills.csv"
    with open(fills, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "date", "symbol", "name", "qty", "buy_price", "sell_price", "pnl_rate",
        ])
        writer.writeheader()
        writer.writerow({
            "date": "20260731", "symbol": "005930", "name": "삼성전자", "qty": "2",
            "buy_price": "70000", "sell_price": "68000", "pnl_rate": "-2.86",
        })
    records = load_audit_trade_records(
        order_audit=orders, fill_audit=fills, names={"005930": "삼성전자"},
        now=NOW, lookback_days=30,
    )
    assert [record.action for record in records] == ["BUY", "SELL"]
    assert records[0].timestamp.endswith("+00:00")
    assert records[1].pnl_pct == -2.86
    assert records[1].exit_type == "손절"


def test_scheduler_registers_exactly_one_0825_weekday_job():
    from backend.core.scheduler.premarket_briefing_jobs import register_premarket_briefing_jobs

    class Scheduler:
        calls: list[tuple] = []

        def add_job(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    scheduler = Scheduler()
    assert register_premarket_briefing_jobs(scheduler, enabled=False) == []
    assert register_premarket_briefing_jobs(scheduler, enabled=True) == [
        "premarket_telegram_briefing"
    ]
    assert len(scheduler.calls) == 1
    _, kwargs = scheduler.calls[0]
    assert kwargs["id"] == "premarket_telegram_briefing"
    assert kwargs["max_instances"] == 1
    assert "day_of_week='mon-fri'" in str(scheduler.calls[0][0][1])
    assert "hour='8'" in str(scheduler.calls[0][0][1])
    assert "minute='25'" in str(scheduler.calls[0][0][1])
