"""ai_swing 진입 훅 — 후보 주입·종목 격리·슬롯/예산 캡 (2026-07-31 신규).

왜 별도 훅이 필요한가
--------------------
다른 zone 전략(f_zone/sf_zone/gold_zone)은 후보를 **당일 주도주 랭킹**
(`KiwoomNativeLeaderPicker`)에서 받는다. ai_swing 은 **ai-trade(단테) 산출물의
스캔∩예측 교집합**에서 받는다. 두 집합은 성격상 거의 겹치지 않으므로
(랭킹 = 당일 급등 상위 N / 교집합 = 되돌림 대기 종목) 후보를 별도로 합성해
주입하지 않으면 ai_swing 은 **영구히 진입 0** 이다.

이 파일이 고정하는 것
--------------------
1. **기본은 완전 OFF** — 플래그 미설정 시 로더조차 호출되지 않는다(§2 S3).
2. **종목 격리** — 랭킹 후보가 우연히 ai_swing 시뮬 최고점을 받아도 진입하지 않는다.
3. **캡은 fail-closed** — 예산/슬롯 계산에 실패하면 통과가 아니라 차단이다.
   다일보유(min_hold 3·max_hold 20) 포지션을 "모르는 예산"으로 늘리지 않는다.
"""
from __future__ import annotations

import asyncio
import csv
import inspect
import json
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import scripts.intraday_buy_daemon as daemon_module

from backend.core.gateway.kiwoom_native_account import HoldingPosition
from backend.core.gateway.kiwoom_native_rank import LeaderCandidate
from backend.core.strategy.ai_swing import AiSwingStrategy
from backend.models.market import MarketType, OHLCV
from scripts.intraday_buy_daemon import (
    DEFAULT_ZONE_STRATEGIES,
    _AI_SWING_SID,
    _ai_swing_budget_left,
    _ai_swing_cap_filter,
    _ai_swing_caps,
    _ai_swing_current_signal,
    _ai_swing_entry_enabled,
    _ai_swing_extra_candidates,
    _ai_swing_order_qty,
    _ai_swing_universe_symbols,
    _append_confirmed_fill_audit,
    _append_confirmed_sell_audits,
    _append_sell_intent_audit,
    _append_unfilled_audit,
    _cancel_sell_intent_audit,
    _evaluate_and_sell,
    _hard_sl_bypasses_cooldown,
    _recent_unresolved_ai_sell_symbols,
    _scan_and_buy,
)
from backend.core.risk.holding_evaluator import SellSignal

_AI_ENV = (
    "BARRO_AI_SWING_ENABLED",
    "BARRO_AI_SWING_ENTRY_ENABLED",
    "BARRO_AI_SWING_BUDGET_RATIO",
    "BARRO_AI_SWING_MAX_POSITIONS",
    "BARRO_AI_SWING_MAX_AGE_H",
    "BARRO_AI_SWING_FALLBACK",
    "BARRO_AI_TRADE_DIR",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """각 테스트를 기본값(전부 미설정) 상태에서 시작한다."""
    for k in _AI_ENV:
        monkeypatch.delenv(k, raising=False)


# ── 테스트 더블 ────────────────────────────────────────────────────────────
class _FakeCandle:
    def __init__(self, close: float):
        self.close = close


class _FakeFetcher:
    """fetch_daily 만 흉내낸다. 실 API 호출 0건."""

    def __init__(self, data: dict, fail: set[str] | None = None):
        self._data = data
        self._fail = fail or set()
        self.calls: list[str] = []

    async def fetch_daily(self, symbol: str):
        self.calls.append(symbol)
        if symbol in self._fail:
            raise RuntimeError("boom")
        return [_FakeCandle(c) for c in self._data.get(symbol, [])]


class _Item:
    def __init__(self, symbol: str, name: str = "", pred_score: float = 0.0):
        self.symbol = symbol
        self.name = name or symbol
        self.pred_score = pred_score


class _FakeStore:
    def __init__(self, positions: dict | None = None, raise_on_load: bool = False):
        self._p = positions or {}
        self._raise = raise_on_load

    def load_all(self):
        if self._raise:
            raise RuntimeError("ledger unreadable")
        return self._p


class _Pos:
    def __init__(self, strategy: str, entry_price: float = 0.0, qty: int = 0):
        self.strategy = strategy
        self.entry_price = entry_price
        self.total_recommended_qty = qty


class _Deposit:
    def __init__(self, orderable_cash=Decimal("0"), cash=Decimal("0")):
        self.orderable_cash = orderable_cash
        self.cash = cash


class _Balance:
    def __init__(self, total_eval=Decimal("0"), holdings=None):
        self.total_eval = total_eval
        self.holdings = holdings or []


def _holding(symbol: str, eval_amount: float, qty: int = 1) -> HoldingPosition:
    return HoldingPosition(
        symbol=symbol, name=symbol, qty=qty,
        avg_buy_price=Decimal(str(eval_amount)),
        cur_price=Decimal(str(eval_amount)),
        eval_amount=Decimal(str(eval_amount)),
        pnl=Decimal("0"), pnl_rate=Decimal("0"),
    )


def _cand(symbol: str, price: float = 10_000.0) -> LeaderCandidate:
    return LeaderCandidate(
        symbol=symbol, name=symbol, cur_price=price, flu_rate=1.0,
        rank_trade_value=None, rank_flu_rate=None, rank_volume=None, score=1.0,
    )


def _sig(symbol: str, strategy: str, price: float = 10_000.0):
    return (_cand(symbol, price), strategy, 1000.0)


# ─── 1. 기본 OFF (§2 S3) ──────────────────────────────────────────────────
def test_entry_disabled_by_default():
    """플래그 미설정 = 후보 주입 안 함. 이게 라이브 무영향의 1차 근거다."""
    assert _ai_swing_entry_enabled() is False


def test_entry_requires_master_and_entry_flags(monkeypatch):
    monkeypatch.setenv("BARRO_AI_SWING_ENTRY_ENABLED", "1")
    assert _ai_swing_entry_enabled() is False
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")
    assert _ai_swing_entry_enabled() is True


def test_budget_ratio_zero_by_default():
    """예산 0 → 신호가 나와도 진입 0. ENTRY_ENABLED 를 켜도 이게 2차로 막는다."""
    ratio, slots = _ai_swing_caps()
    assert ratio == 0.0
    assert slots == 3


def test_ai_swing_not_in_default_zone_strategies():
    """기본 전략 목록에 없어야 한다 — 명시 지정(--strategies/env)해야만 스캔된다."""
    assert _AI_SWING_SID not in DEFAULT_ZONE_STRATEGIES


def test_universe_no_data_when_dir_unset():
    """BARRO_AI_TRADE_DIR 미설정 → no_data. 예외가 새어나오지 않는다."""
    items, status, reason = _ai_swing_universe_symbols()
    assert items == []
    assert status == "no_data"
    assert reason  # 사유가 비어 있으면 안 된다(§8)


def _write_today_sources(path, *, age_h: float = 0.0) -> None:
    today = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    scan = {"date": today, "stocks": [{"code": "005930", "name": "삼성전자", "score": 80}]}
    pred = {"date": today, "stocks": [{"code": "005930", "name": "삼성전자", "rank": 1, "total_score": 90}]}
    for prefix, payload in (("watchlist", scan), ("predictions", pred)):
        target = path / f"{prefix}_{today}.json"
        target.write_text(json.dumps(payload), encoding="utf-8")
        ts = time.time() - age_h * 3600
        os.utime(target, (ts, ts))


def test_live_universe_requires_fresh_complete_sources(monkeypatch, tmp_path):
    _write_today_sources(tmp_path)
    monkeypatch.setenv("BARRO_AI_TRADE_DIR", str(tmp_path))
    monkeypatch.setenv("BARRO_AI_SWING_MAX_AGE_H", "12")
    items, status, _ = _ai_swing_universe_symbols()
    assert status == "ok"
    assert [item.symbol for item in items] == ["005930"]


def test_live_universe_rejects_old_same_day_files(monkeypatch, tmp_path):
    _write_today_sources(tmp_path, age_h=13)
    monkeypatch.setenv("BARRO_AI_TRADE_DIR", str(tmp_path))
    monkeypatch.setenv("BARRO_AI_SWING_MAX_AGE_H", "12")
    items, status, reason = _ai_swing_universe_symbols()
    assert items == []
    assert status == "stale"
    assert reason.startswith("source_age:")


@pytest.mark.parametrize("raw", ["nan", "inf", "-1", "0"])
def test_live_universe_rejects_invalid_max_age(monkeypatch, tmp_path, raw):
    _write_today_sources(tmp_path)
    monkeypatch.setenv("BARRO_AI_TRADE_DIR", str(tmp_path))
    monkeypatch.setenv("BARRO_AI_SWING_MAX_AGE_H", raw)
    items, status, reason = _ai_swing_universe_symbols()
    assert items == []
    assert status == "no_data"
    assert reason == "invalid_max_age_h"


def test_current_signal_calls_ai_swing_analyze(monkeypatch):
    expected = object()
    captured = {}

    def _analyze(_self, ctx):
        captured["ctx"] = ctx
        return expected

    monkeypatch.setattr(AiSwingStrategy, "analyze", _analyze)
    candle = OHLCV(
        symbol="005930", timestamp=datetime(2026, 7, 31, tzinfo=timezone.utc),
        open=100, high=110, low=90, close=105, volume=1000,
        market_type=MarketType.STOCK,
    )
    signal, reason = _ai_swing_current_signal(_cand("005930"), [candle])
    assert signal is expected and reason == ""
    assert captured["ctx"].symbol == "005930"


def test_caps_parse_failure_is_fail_closed(monkeypatch):
    """숫자가 아닌 값이 들어오면 통과가 아니라 차단이어야 한다."""
    monkeypatch.setenv("BARRO_AI_SWING_BUDGET_RATIO", "무효")
    monkeypatch.setenv("BARRO_AI_SWING_MAX_POSITIONS", "무효")
    assert _ai_swing_caps() == (0.0, 0)


def test_negative_ratio_clamped_to_zero(monkeypatch):
    monkeypatch.setenv("BARRO_AI_SWING_BUDGET_RATIO", "-0.5")
    ratio, _ = _ai_swing_caps()
    assert ratio == 0.0


def test_ratio_over_total_assets_is_fail_closed(monkeypatch):
    monkeypatch.setenv("BARRO_AI_SWING_BUDGET_RATIO", "1.1")
    ratio, _ = _ai_swing_caps()
    assert ratio == 0.0


# ─── 2. 후보 합성 ─────────────────────────────────────────────────────────
def test_extra_candidates_synthesizes_from_daily_candles():
    """일봉 마지막 2봉으로 cur_price/flu_rate 를 만든다 — 별도 시세 TR 을 쓰지 않는다."""
    f = _FakeFetcher({"005930": [9_000.0, 10_000.0]})
    out = asyncio.run(_ai_swing_extra_candidates(f, [_Item("005930", "삼성전자", 81.2)], set()))
    assert len(out) == 1
    c = out[0]
    assert c.symbol == "005930"
    assert c.name == "삼성전자"
    assert c.cur_price == pytest.approx(10_000.0)
    assert c.flu_rate == pytest.approx((10_000 - 9_000) / 9_000 * 100)
    assert c.score == pytest.approx(81.2)   # 예측점수를 그대로 싣는다
    assert c.rank_trade_value is None       # 랭킹 산출물이 아니다


def test_extra_candidates_skips_excluded():
    """보유·당일매도 등 excluded 종목은 조회조차 하지 않는다(API 절약)."""
    f = _FakeFetcher({"005930": [9_000.0, 10_000.0]})
    out = asyncio.run(_ai_swing_extra_candidates(f, [_Item("005930")], {"005930"}))
    assert out == []
    assert f.calls == []


def test_extra_candidates_absorbs_fetch_failure():
    """한 종목 조회 실패가 나머지를 깨뜨리면 안 된다."""
    f = _FakeFetcher({"000660": [9_000.0, 10_000.0]}, fail={"005930"})
    out = asyncio.run(_ai_swing_extra_candidates(f, [_Item("005930"), _Item("000660")], set()))
    assert [c.symbol for c in out] == ["000660"]


def test_extra_candidates_skips_insufficient_candles():
    """봉이 2개 미만이면 등락률을 만들 수 없다 → 조용히 건너뛴다."""
    f = _FakeFetcher({"005930": [10_000.0]})
    assert asyncio.run(_ai_swing_extra_candidates(f, [_Item("005930")], set())) == []


def test_extra_candidates_skips_zero_prev_close():
    """전일종가 0(데이터 결함) 시 0나눗셈 없이 flu_rate=0 으로 살린다."""
    f = _FakeFetcher({"005930": [0.0, 10_000.0]})
    out = asyncio.run(_ai_swing_extra_candidates(f, [_Item("005930")], set()))
    assert len(out) == 1 and out[0].flu_rate == 0.0


# ─── 3. 슬롯·예산 캡 ──────────────────────────────────────────────────────
def test_cap_filter_noop_without_ai_swing_signals():
    """ai_swing 신호가 없으면 목록을 그대로 돌려준다(다른 전략 무영향)."""
    sigs = [_sig("005930", "f_zone"), _sig("000660", "swing_38")]
    out, skipped = _ai_swing_cap_filter(sigs, _FakeStore(), _Balance(), _Deposit())
    assert out == sigs
    assert skipped == []


def test_cap_filter_blocks_all_when_budget_zero():
    """BUDGET_RATIO=0(기본) → ai_swing 전량 차단, 타 전략은 보존."""
    sigs = [_sig("005930", _AI_SWING_SID), _sig("000660", "f_zone")]
    out, skipped = _ai_swing_cap_filter(sigs, _FakeStore(), _Balance(), _Deposit())
    assert [s[1] for s in out] == ["f_zone"]
    assert [s[0] for s in skipped] == ["005930"]


def test_cap_filter_passes_within_caps(monkeypatch):
    monkeypatch.setenv("BARRO_AI_SWING_BUDGET_RATIO", "0.10")
    monkeypatch.setenv("BARRO_AI_SWING_MAX_POSITIONS", "3")
    sigs = [_sig("005930", _AI_SWING_SID, 10_000.0)]
    out, skipped = _ai_swing_cap_filter(
        sigs, _FakeStore(), _Balance(total_eval=Decimal("0")),
        _Deposit(orderable_cash=Decimal("10000000")),
    )
    assert len(out) == 1 and skipped == []


def test_cap_filter_slot_exhaustion(monkeypatch):
    """이미 슬롯을 다 쓴 상태면 신규 진입을 막는다. `_v1` 접미사도 같은 전략으로 센다."""
    monkeypatch.setenv("BARRO_AI_SWING_BUDGET_RATIO", "0.50")
    monkeypatch.setenv("BARRO_AI_SWING_MAX_POSITIONS", "1")
    store = _FakeStore({"000660": _Pos("ai_swing_v1")})
    out, skipped = _ai_swing_cap_filter(
        [_sig("005930", _AI_SWING_SID)], store,
        _Balance(total_eval=Decimal("100000"), holdings=[_holding("000660", 100_000)]),
        _Deposit(orderable_cash=Decimal("10000000")),
    )
    assert out == []
    assert "슬롯" in skipped[0][1]


def test_cap_filter_budget_exhaustion(monkeypatch):
    """이미 보유한 ai_swing 평가액이 예산을 채웠으면 차단한다."""
    monkeypatch.setenv("BARRO_AI_SWING_BUDGET_RATIO", "0.10")
    monkeypatch.setenv("BARRO_AI_SWING_MAX_POSITIONS", "5")
    store = _FakeStore({"000660": _Pos("ai_swing")})
    # 기준자산 = 900,000(현금) + 100,000(평가) = 1,000,000 → 예산 100,000. 이미 100,000 사용.
    out, skipped = _ai_swing_cap_filter(
        [_sig("005930", _AI_SWING_SID)], store,
        _Balance(total_eval=Decimal("100000"), holdings=[_holding("000660", 100_000)]),
        _Deposit(orderable_cash=Decimal("900000")),
    )
    assert out == []
    assert "예산" in skipped[0][1]


def test_pending_ai_order_reserves_budget_before_broker_balance(monkeypatch):
    monkeypatch.setenv("BARRO_AI_SWING_BUDGET_RATIO", "0.10")
    store = _FakeStore({"000660": _Pos("ai_swing", entry_price=10_000, qty=10)})
    total, left = _ai_swing_budget_left(
        store, _Balance(), _Deposit(orderable_cash=Decimal("1000000")),
    )
    assert total == 1_000_000
    assert left == 0


def test_cap_filter_fail_closed_on_ledger_error(monkeypatch):
    """★장부를 못 읽으면 통과가 아니라 차단이다 — 모르는 상태로 포지션을 늘리지 않는다."""
    monkeypatch.setenv("BARRO_AI_SWING_BUDGET_RATIO", "0.50")
    out, skipped = _ai_swing_cap_filter(
        [_sig("005930", _AI_SWING_SID)], _FakeStore(raise_on_load=True),
        _Balance(), _Deposit(orderable_cash=Decimal("10000000")),
    )
    assert out == []
    assert "fail-closed" in skipped[0][1]


def test_cap_filter_fail_closed_on_zero_assets(monkeypatch):
    """예탁자산 조회가 0 이면(조회 실패 포함) 차단한다."""
    monkeypatch.setenv("BARRO_AI_SWING_BUDGET_RATIO", "0.50")
    out, skipped = _ai_swing_cap_filter(
        [_sig("005930", _AI_SWING_SID)], _FakeStore(), _Balance(), _Deposit(),
    )
    assert out == []
    assert "fail-closed" in skipped[0][1]


def test_cap_filter_preserves_other_strategies_on_failure(monkeypatch):
    """ai_swing 캡 실패가 다른 전략 신호를 삼키면 안 된다."""
    monkeypatch.setenv("BARRO_AI_SWING_BUDGET_RATIO", "0.50")
    sigs = [_sig("005930", _AI_SWING_SID), _sig("000660", "gold_zone")]
    out, _ = _ai_swing_cap_filter(sigs, _FakeStore(raise_on_load=True), _Balance(), _Deposit())
    assert [s[1] for s in out] == ["gold_zone"]


def test_cap_filter_multiple_signals_respect_slot_count(monkeypatch):
    """빈 슬롯 수만큼만 통과시킨다."""
    monkeypatch.setenv("BARRO_AI_SWING_BUDGET_RATIO", "0.90")
    monkeypatch.setenv("BARRO_AI_SWING_MAX_POSITIONS", "2")
    sigs = [_sig(s, _AI_SWING_SID, 1_000.0) for s in ("005930", "000660", "035720")]
    out, skipped = _ai_swing_cap_filter(
        sigs, _FakeStore(), _Balance(), _Deposit(orderable_cash=Decimal("10000000")),
    )
    assert len(out) == 2
    assert len(skipped) == 1


def test_order_qty_is_clamped_by_quote_notional():
    assert _ai_swing_order_qty(50, 10_000.0, 100_000.0) == 10
    assert _ai_swing_order_qty(50, 10_000.0, 9_999.0) == 0


def test_dry_run_does_not_persist_active_position():
    """DRY_RUN 감사행은 남겨도 실보유 장부는 만들면 안 된다."""
    source = inspect.getsource(_scan_and_buy)
    provisional_guard = source.index("if is_ai_swing and not args.dry_run:")
    first_persist = source.index("pos_store.create_from_order(")
    order_call = source.index("result = await gate.place_buy")
    post_guard = source.index("if not result.dry_run:")
    last_persist = source.rindex("pos_store.create_from_order(")
    assert provisional_guard < first_persist < order_call
    assert post_guard < last_persist
    # 실주문 접수 후 장부 저장이 실패해도 세션 예약이 먼저라 중복 주문하지 않는다.
    assert source.index("session_bought.add(r.symbol)") < last_persist
    assert source.index("executed += 1") < last_persist


def test_ai_swing_hard_sl_bypasses_entry_cooldown():
    assert _hard_sl_bypasses_cooldown(SellSignal.STOP_LOSS, Decimal("-3.1"), "ai_swing_v1")
    assert _hard_sl_bypasses_cooldown(SellSignal.STOP_LOSS, Decimal("-5.1"), "f_zone")
    assert not _hard_sl_bypasses_cooldown(SellSignal.STOP_LOSS, Decimal("-3.1"), "f_zone")


def test_confirmed_fill_audit_is_appended_once(tmp_path):
    audit = tmp_path / "order_audit.csv"
    header = [
        "ts", "action", "side", "symbol", "qty", "price", "order_no",
        "return_code", "blocked", "reason", "strategy_id", "filled_qty",
        "avg_fill_price",
    ]
    with audit.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerow([
            "2026-07-31T01:00:00+00:00", "ORDERED", "buy", "005930", "1",
            "MKT", "A1", "0", "0", "", "ai_swing", "", "",
        ])
        writer.writerow([
            "2000-01-01T01:00:00+00:00", "FILLED", "buy", "005930", "1",
            "MKT", "A1", "", "0", "과거 주문번호 재사용", "ai_swing", "1", "9000",
        ])
    pos = SimpleNamespace(
        symbol="005930", strategy="ai_swing",
        entry_time="2001-01-02T01:00:00+00:00",
        tranches=[SimpleNamespace(status="filled", order_no="A1")],
    )
    holding = _holding("005930", 10_250)

    assert _append_confirmed_fill_audit(audit, pos, holding) is True
    assert _append_confirmed_fill_audit(audit, pos, holding) is False
    rows = list(csv.DictReader(audit.open(encoding="utf-8", newline="")))
    filled = [row for row in rows if row["reason"] == "브로커 잔고 반영·미체결 없음으로 확정"]
    assert len(filled) == 1
    assert filled[0]["ts"] == "2001-01-02T01:00:00+00:00"
    assert filled[0]["filled_qty"] == "1"
    assert filled[0]["avg_fill_price"] == "10250"


def test_provisional_fill_links_real_order_number(tmp_path):
    audit = tmp_path / "order_audit.csv"
    with audit.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ts", "action", "side", "symbol", "qty", "price", "order_no",
            "return_code", "blocked", "reason", "strategy_id", "filled_qty",
            "avg_fill_price",
        ])
        writer.writerow([
            "2026-07-31T01:00:01+00:00", "ORDERED", "buy", "005930", "3",
            "MKT", "REAL-42", "0", "0", "", "ai_swing", "", "",
        ])
    pos = SimpleNamespace(
        symbol="005930", strategy="ai_swing",
        entry_time="2026-07-31T01:00:00+00:00",
        tranches=[SimpleNamespace(status="filled", order_no="PENDING:005930")],
    )

    assert _append_confirmed_fill_audit(audit, pos, _holding("005930", 10_250, qty=3))
    rows = list(csv.DictReader(audit.open(encoding="utf-8", newline="")))
    assert rows[-1]["order_no"] == "REAL-42"


def test_provisional_fill_does_not_reuse_old_cancelled_order(tmp_path):
    audit = tmp_path / "order_audit.csv"
    with audit.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ts", "action", "side", "symbol", "qty", "price", "order_no",
            "return_code", "blocked", "reason", "strategy_id", "filled_qty",
            "avg_fill_price",
        ])
        writer.writerow([
            "2026-07-31T00:30:00+00:00", "ORDERED", "buy", "005930", "3",
            "MKT", "OLD-1", "0", "0", "", "ai_swing", "", "",
        ])
        writer.writerow([
            "2026-07-31T00:40:00+00:00", "UNFILLED", "buy", "005930", "3",
            "MKT", "OLD-1", "", "0", "", "ai_swing", "0", "",
        ])
    pos = SimpleNamespace(
        symbol="005930", strategy="ai_swing",
        entry_time="2026-07-31T01:00:00+00:00",
        tranches=[SimpleNamespace(
            status="filled", order_no="PENDING:005930", qty=3,
        )],
    )

    assert _append_confirmed_fill_audit(audit, pos, _holding("005930", 10_250, qty=3))
    rows = list(csv.DictReader(audit.open(encoding="utf-8", newline="")))
    assert rows[-1]["order_no"] == "PENDING:005930"


def test_provisional_unfilled_cancels_real_order_number(tmp_path):
    audit = tmp_path / "order_audit.csv"
    entry_time = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with audit.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ts", "action", "side", "symbol", "qty", "price", "order_no",
            "return_code", "blocked", "reason", "strategy_id", "filled_qty",
            "avg_fill_price",
        ])
        writer.writerow([
            entry_time, "ORDERED", "buy", "005930", "3", "MKT", "REAL-43",
            "0", "0", "", "ai_swing", "", "",
        ])
    pos = SimpleNamespace(
        symbol="005930", name="삼성전자", strategy="ai_swing",
        entry_time=entry_time,
        tranches=[SimpleNamespace(status="filled", order_no="PENDING:005930", qty=3)],
    )

    _append_unfilled_audit(audit, pos)
    rows = list(csv.DictReader(audit.open(encoding="utf-8", newline="")))
    assert rows[-1]["action"] == "UNFILLED"
    assert rows[-1]["order_no"] == "REAL-43"


def test_confirmed_sell_does_not_mark_original_buy_unfilled(tmp_path):
    audit = tmp_path / "order_audit.csv"
    entry = datetime.now(timezone.utc)
    entry_time = entry.isoformat(timespec="seconds")
    sell_time = (entry + timedelta(seconds=1)).isoformat(timespec="seconds")
    with audit.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ts", "action", "side", "symbol", "qty", "price", "order_no",
            "return_code", "blocked", "reason", "strategy_id", "filled_qty",
            "avg_fill_price",
        ])
        writer.writerow([
            entry_time, "FILLED", "buy", "005930", "3", "MKT", "BUY-1",
            "", "0", "", "ai_swing", "3", "10000",
        ])
        writer.writerow([
            sell_time, "FILLED", "sell", "005930", "3", "MKT", "SELL-1",
            "", "0", "", "ai_swing", "3", "",
        ])
    pos = SimpleNamespace(
        symbol="005930", name="삼성전자", strategy="ai_swing", entry_time=entry_time,
        tranches=[SimpleNamespace(status="filled", order_no="BUY-1", qty=3)],
    )

    _append_unfilled_audit(audit, pos)
    rows = list(csv.DictReader(audit.open(encoding="utf-8", newline="")))
    assert not [r for r in rows if r["action"] == "UNFILLED"]


def test_confirmed_partial_sell_is_appended_once_and_offsets_recovery(tmp_path):
    from scripts.ai_swing_recover import parse_audit_buys

    audit = tmp_path / "order_audit.csv"
    with audit.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ts", "action", "side", "symbol", "qty", "price", "order_no",
            "return_code", "blocked", "reason", "strategy_id", "filled_qty",
            "avg_fill_price",
        ])
        writer.writerow([
            "2026-07-30T01:00:00+00:00", "FILLED", "buy", "005930", "10",
            "MKT", "BUY-1", "", "0", "", "ai_swing", "10", "10000",
        ])
        writer.writerow([
            "2026-07-31T01:00:00+00:00", "ORDERED", "sell", "005930", "4",
            "MKT", "SELL-1", "0", "0", "", "ai_swing", "", "",
        ])
    pos = SimpleNamespace(
        strategy="ai_swing", entry_time="2026-07-30T01:00:00+00:00",
        partial_tp_done=False, filled_qty=lambda: 10,
    )
    holdings = {"005930": _holding("005930", 10_000, qty=6)}

    assert _append_confirmed_sell_audits(
        audit, holdings, {"005930": pos}, set(),
    ) == {"005930"}
    pos.partial_tp_done = True
    assert _append_confirmed_sell_audits(audit, holdings, {"005930": pos}, set()) == set()
    rows = list(csv.DictReader(audit.open(encoding="utf-8", newline="")))
    assert parse_audit_buys(rows)["005930"]["filled_qty"] == "6"


def test_confirmed_full_sell_never_exceeds_book_qty(tmp_path):
    audit = tmp_path / "order_audit.csv"
    with audit.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ts", "action", "side", "symbol", "qty", "price", "order_no",
            "return_code", "blocked", "reason", "strategy_id", "filled_qty",
            "avg_fill_price",
        ])
        writer.writerow([
            "2026-07-30T01:00:00+00:00", "FILLED", "buy", "005930", "10",
            "MKT", "BUY-1", "", "0", "", "ai_swing", "10", "10000",
        ])
        for idx in (1, 2):
            writer.writerow([
                f"2026-07-31T01:00:0{idx}+00:00", "ORDERED", "sell", "005930", "10",
                "MKT", f"SELL-{idx}", "0", "0", "", "ai_swing", "", "",
            ])
    pos = SimpleNamespace(
        strategy="ai_swing", entry_time="2026-07-30T01:00:00+00:00",
        partial_tp_done=False, filled_qty=lambda: 10,
    )

    assert _append_confirmed_sell_audits(audit, {}, {"005930": pos}, set()) == set()
    rows = list(csv.DictReader(audit.open(encoding="utf-8", newline="")))
    fills = [r for r in rows if r["action"] == "FILLED" and r["side"] == "sell"]
    assert sum(int(r["filled_qty"]) for r in fills) == 10


def test_confirmed_sell_is_crash_idempotent_before_ledger_reconcile(tmp_path):
    audit = tmp_path / "order_audit.csv"
    with audit.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ts", "action", "side", "symbol", "qty", "price", "order_no",
            "return_code", "blocked", "reason", "strategy_id", "filled_qty",
            "avg_fill_price",
        ])
        writer.writerow([
            "2026-07-30T01:00:00+00:00", "FILLED", "buy", "005930", "10",
            "MKT", "BUY-1", "", "0", "", "ai_swing", "10", "10000",
        ])
        for idx in (1, 2):
            writer.writerow([
                f"2026-07-31T01:00:0{idx}+00:00", "ORDERED", "sell", "005930", "4",
                "MKT", f"SELL-{idx}", "0", "0", "", "ai_swing", "", "",
            ])
    pos = SimpleNamespace(
        strategy="ai_swing", entry_time="2026-07-30T01:00:00+00:00",
        partial_tp_done=False, filled_qty=lambda: 10,
    )
    holdings = {"005930": _holding("005930", 10_000, qty=6)}

    assert _append_confirmed_sell_audits(
        audit, holdings, {"005930": pos}, set(),
    ) == {"005930"}
    # audit append 후 pos qty reconcile 전 crash를 가정해 같은 pos10으로 재실행.
    assert _append_confirmed_sell_audits(
        audit, holdings, {"005930": pos}, set(),
    ) == {"005930"}
    rows = list(csv.DictReader(audit.open(encoding="utf-8", newline="")))
    fills = [r for r in rows if r["action"] == "FILLED" and r["side"] == "sell"]
    assert [(r["order_no"], r["filled_qty"]) for r in fills] == [("SELL-2", "4")]


def test_confirmed_sell_prefers_latest_order_over_old_cancelled_intent(tmp_path):
    audit = tmp_path / "order_audit.csv"
    with audit.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ts", "action", "side", "symbol", "qty", "price", "order_no",
            "return_code", "blocked", "reason", "strategy_id", "filled_qty",
            "avg_fill_price",
        ])
        writer.writerow([
            "2026-07-30T01:00:00+00:00", "FILLED", "buy", "005930", "10",
            "MKT", "BUY-1", "", "0", "", "ai_swing", "10", "10000",
        ])
        writer.writerow([
            "2026-07-31T01:00:00+00:00", "ORDERED", "sell", "005930", "10",
            "MKT", "CANCELLED-FULL", "0", "0", "", "ai_swing", "", "",
        ])
        writer.writerow([
            "2026-07-31T02:00:00+00:00", "ORDERED", "sell", "005930", "4",
            "MKT", "ACTUAL-TP", "0", "0", "", "ai_swing", "", "",
        ])
    pos = SimpleNamespace(
        strategy="ai_swing", entry_time="2026-07-30T01:00:00+00:00",
        partial_tp_done=False, filled_qty=lambda: 10,
    )

    assert _append_confirmed_sell_audits(
        audit, {"005930": _holding("005930", 10_000, qty=6)},
        {"005930": pos}, set(),
    ) == {"005930"}
    rows = list(csv.DictReader(audit.open(encoding="utf-8", newline="")))
    fills = [r for r in rows if r["action"] == "FILLED" and r["side"] == "sell"]
    assert [(r["order_no"], r["filled_qty"]) for r in fills] == [("ACTUAL-TP", "4")]
    assert "PARTIAL_TP" in fills[0]["reason"]


def test_sell_intent_recovers_fill_when_ordered_audit_is_missing(tmp_path):
    audit = tmp_path / "order_audit.csv"
    with audit.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ts", "action", "side", "symbol", "qty", "price", "order_no",
            "return_code", "blocked", "reason", "strategy_id", "filled_qty",
            "avg_fill_price",
        ])
        writer.writerow([
            "2026-07-30T01:00:00+00:00", "FILLED", "buy", "005930", "10",
            "MKT", "BUY-1", "", "0", "", "ai_swing", "10", "10000",
        ])
    intent_no = _append_sell_intent_audit(
        audit, symbol="005930", qty=4, strategy="ai_swing",
        signal_name="partial_tp",
    )
    pos = SimpleNamespace(
        strategy="ai_swing", entry_time="2026-07-30T01:00:00+00:00",
        partial_tp_done=False, filled_qty=lambda: 10,
    )

    assert _append_confirmed_sell_audits(
        audit, {"005930": _holding("005930", 10_000, qty=6)},
        {"005930": pos}, set(),
    ) == {"005930"}
    rows = list(csv.DictReader(audit.open(encoding="utf-8", newline="")))
    fills = [r for r in rows if r["action"] == "FILLED" and r["side"] == "sell"]
    assert [(r["order_no"], r["filled_qty"]) for r in fills] == [(intent_no, "4")]
    assert "PARTIAL_TP" in fills[0]["reason"]


def test_terminal_ordered_sell_releases_linked_intent_grace(tmp_path):
    audit = tmp_path / "order_audit.csv"
    with audit.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ts", "action", "side", "symbol", "qty", "price", "order_no",
            "return_code", "blocked", "reason", "strategy_id", "filled_qty",
            "avg_fill_price",
        ])
        writer.writerow([
            "2026-07-30T01:00:00+00:00", "FILLED", "buy", "005930", "10",
            "MKT", "BUY-1", "", "0", "", "ai_swing", "10", "10000",
        ])
    _append_sell_intent_audit(
        audit, symbol="005930", qty=10, strategy="ai_swing",
        signal_name="stop_loss",
    )
    with audit.open("a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow([
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ORDERED", "sell", "005930", "10", "MKT", "SELL-1",
            "0", "0", "", "ai_swing", "", "",
        ])
    pos = SimpleNamespace(
        strategy="ai_swing", entry_time="2026-07-30T01:00:00+00:00",
        partial_tp_done=False, filled_qty=lambda: 10,
    )

    assert _recent_unresolved_ai_sell_symbols(audit) == {"005930"}
    assert _append_confirmed_sell_audits(
        audit, {"005930": _holding("005930", 10_000, qty=6)},
        {"005930": pos}, set(),
    ) == set()
    rows = list(csv.DictReader(audit.open(encoding="utf-8", newline="")))
    assert [
        row["filled_qty"] for row in rows
        if row["action"] == "FILLED" and row["side"] == "sell"
    ] == ["4"]
    assert _recent_unresolved_ai_sell_symbols(audit) == set()


def test_cancelled_sell_intent_is_not_used_as_fill_source(tmp_path):
    audit = tmp_path / "order_audit.csv"
    with audit.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ts", "action", "side", "symbol", "qty", "price", "order_no",
            "return_code", "blocked", "reason", "strategy_id", "filled_qty",
            "avg_fill_price",
        ])
        writer.writerow([
            "2026-07-30T01:00:00+00:00", "FILLED", "buy", "005930", "10",
            "MKT", "BUY-1", "", "0", "", "ai_swing", "10", "10000",
        ])
    intent_no = _append_sell_intent_audit(
        audit, symbol="005930", qty=4, strategy="ai_swing",
        signal_name="partial_tp",
    )
    assert _recent_unresolved_ai_sell_symbols(audit) == {"005930"}
    _cancel_sell_intent_audit(
        audit, symbol="005930", qty=4, strategy="ai_swing",
        intent_no=intent_no, reason="TradingDisabled",
    )
    assert _recent_unresolved_ai_sell_symbols(audit) == set()
    pos = SimpleNamespace(
        strategy="ai_swing", entry_time="2026-07-30T01:00:00+00:00",
        partial_tp_done=False, filled_qty=lambda: 10,
    )

    assert _append_confirmed_sell_audits(
        audit, {"005930": _holding("005930", 10_000, qty=6)},
        {"005930": pos}, set(),
    ) == set()
    rows = list(csv.DictReader(audit.open(encoding="utf-8", newline="")))
    assert not [r for r in rows if r["action"] == "FILLED" and r["side"] == "sell"]


def test_sell_skips_symbols_with_open_orders():
    source = inspect.getsource(_evaluate_and_sell)
    assert source.index("fetch_open_orders") < source.index("fetch_balance")
    assert source.index("_append_sell_intent_audit(") < source.index("r = await gate.place_sell")
    assert "d.symbol in pending_sell_symbols" in source
    assert "strategy_key == _AI_SWING_SID and d.symbol in pending_symbols" in source
    assert "d.symbol in recent_ai_sell_symbols" in source
    assert "and h.symbol not in pending_symbols" in source
    assert "or (open_orders_known and sell_intents_known)" in source
    assert "not open_orders_known or not sell_intents_known" in source
    assert source.index("confirmed_partial_symbols is not None") < source.index(
        "await _sync_positions("
    )
    assert "for sym in confirmed_partial_symbols:" in source
    assert "pos.partial_tp_done = True" in source


def test_sell_audit_read_failure_blocks_position_sync(monkeypatch, tmp_path):
    audit = tmp_path / "order_audit.csv"
    audit.write_bytes(b"\xff")
    position = SimpleNamespace(strategy="ai_swing")

    class _Store:
        def __init__(self):
            self.positions = {"005930": position}

        def load_all(self):
            return dict(self.positions)

    store = _Store()

    class _Account:
        def __init__(self, **_kwargs):
            pass

        async def fetch_open_orders(self):
            return []

        async def fetch_balance(self):
            return _Balance(holdings=[])

    async def _forbidden_sync(*_args, **_kwargs):
        raise AssertionError("감사 원장 실패 후 position sync가 실행됨")

    monkeypatch.setattr(daemon_module, "KiwoomNativeAccountFetcher", _Account)
    monkeypatch.setattr(daemon_module, "ActivePositionStore", lambda _path: store)
    monkeypatch.setattr(daemon_module, "_sync_positions", _forbidden_sync)
    monkeypatch.setattr(
        daemon_module.PolicyConfigStore, "load", lambda _self: SimpleNamespace(),
    )
    args = SimpleNamespace(
        dry_run=False, pos_log=str(tmp_path / "positions.json"), audit_log=str(audit),
    )

    assert asyncio.run(_evaluate_and_sell(args, object(), object())) == 0
    assert store.positions == {"005930": position}


@pytest.mark.parametrize("contents", [
    None,
    b"",
    b"garbage\n",
    (
        b"ts,action,side,symbol,qty,price,order_no,return_code,blocked,reason,"
        b"strategy_id,filled_qty,avg_fill_price\n"
    ),
])
def test_active_ai_position_requires_valid_sell_audit(contents, tmp_path):
    audit = tmp_path / "order_audit.csv"
    if contents is not None:
        audit.write_bytes(contents)
    pos = SimpleNamespace(
        strategy="ai_swing", entry_time="2026-07-30T01:00:00+00:00",
        tranches=[SimpleNamespace(status="filled", order_no="BUY-1", qty=10)],
        filled_qty=lambda: 10,
    )

    assert _append_confirmed_sell_audits(
        audit, {"005930": _holding("005930", 10_000, qty=6)},
        {"005930": pos}, set(),
    ) is None


def test_cancelled_zero_fill_real_buy_can_reach_position_sync(tmp_path):
    audit = tmp_path / "order_audit.csv"
    with audit.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ts", "action", "side", "symbol", "qty", "price", "order_no",
            "return_code", "blocked", "reason", "strategy_id", "filled_qty",
            "avg_fill_price",
        ])
        writer.writerow([
            "2026-07-30T01:00:00+00:00", "ORDERED", "buy", "005930", "10",
            "MKT", "REAL-BUY-1", "0", "0", "", "ai_swing", "", "",
        ])
    pos = SimpleNamespace(
        strategy="ai_swing", entry_time="2026-07-30T01:00:00+00:00",
        tranches=[SimpleNamespace(status="filled", order_no="REAL-BUY-1", qty=10)],
        filled_qty=lambda: 10,
    )

    assert _append_confirmed_sell_audits(
        audit, {}, {"005930": pos}, set(),
    ) == set()


def test_pending_partial_sell_is_audited_from_original_book_qty(tmp_path):
    audit = tmp_path / "order_audit.csv"
    with audit.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ts", "action", "side", "symbol", "qty", "price", "order_no",
            "return_code", "blocked", "reason", "strategy_id", "filled_qty",
            "avg_fill_price",
        ])
        writer.writerow([
            "2026-07-30T01:00:00+00:00", "FILLED", "buy", "005930", "10",
            "MKT", "BUY-1", "", "0", "", "ai_swing", "10", "10000",
        ])
        writer.writerow([
            "2026-07-31T01:00:00+00:00", "ORDERED", "sell", "005930", "4",
            "MKT", "SELL-1", "0", "0", "", "ai_swing", "", "",
        ])
    pos = SimpleNamespace(
        strategy="ai_swing", entry_time="2026-07-30T01:00:00+00:00",
        partial_tp_done=False, filled_qty=lambda: 10,
    )

    assert _append_confirmed_sell_audits(
        audit, {"005930": _holding("005930", 10_000, qty=8)},
        {"005930": pos}, {"005930"},
    ) == set()
    assert _append_confirmed_sell_audits(
        audit, {"005930": _holding("005930", 10_000, qty=6)},
        {"005930": pos}, set(),
    ) == {"005930"}
    rows = list(csv.DictReader(audit.open(encoding="utf-8", newline="")))
    fills = [r for r in rows if r["action"] == "FILLED" and r["side"] == "sell"]
    assert [r["filled_qty"] for r in fills] == ["4"]


def test_uncertain_ai_buy_preserves_provisional_for_reconciliation():
    source = inspect.getsource(_scan_and_buy)
    assert "[ORDER-UNCERTAIN]" in source
    assert "preserve_provisional = True" in source
    assert "not accepted and not preserve_provisional" in source
    assert "if cleanup_failed:" in source


def test_entry_only_once_never_runs_sell_or_eod(monkeypatch):
    calls: list[str] = []

    async def _scan(*_args):
        calls.append("scan")
        return 2

    async def _forbidden(*_args):
        raise AssertionError("entry-only path reached position mutation flow")

    monkeypatch.setattr(daemon_module, "_build_oauth", lambda: object())
    monkeypatch.setattr(daemon_module, "_scan_and_buy", _scan)
    monkeypatch.setattr(daemon_module, "_evaluate_and_sell", _forbidden)
    monkeypatch.setattr(daemon_module, "_eod_carry_limit", _forbidden)
    monkeypatch.setattr(daemon_module, "_save_balance_snapshot", _forbidden)

    asyncio.run(daemon_module._daemon(SimpleNamespace(
        interval=1, top=5, telegram=False, entry_only_once=True, dry_run=True,
    )))
    assert calls == ["scan"]


def test_entry_only_once_isolates_scan_writes_to_temp_audit_dir():
    source = inspect.getsource(_scan_and_buy)
    guard = source.index('if not getattr(args, "entry_only_once", False):')
    assert guard < source.index("_save_refined_signals(signals, regime)")
    assert guard < source.index("_record_alert_events(signals)")
    assert guard < source.index("_save_market_snapshot(leaders, balance, regime)")
    assert 'Path(args.audit_log).with_name("daily_gate_state.json")' in source
