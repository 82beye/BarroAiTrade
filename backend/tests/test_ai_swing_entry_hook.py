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
from decimal import Decimal

import pytest

from backend.core.gateway.kiwoom_native_account import HoldingPosition
from backend.core.gateway.kiwoom_native_rank import LeaderCandidate
from scripts.intraday_buy_daemon import (
    DEFAULT_ZONE_STRATEGIES,
    _AI_SWING_SID,
    _ai_swing_cap_filter,
    _ai_swing_caps,
    _ai_swing_entry_enabled,
    _ai_swing_extra_candidates,
    _ai_swing_universe_symbols,
)

_AI_ENV = (
    "BARRO_AI_SWING_ENTRY_ENABLED",
    "BARRO_AI_SWING_BUDGET_RATIO",
    "BARRO_AI_SWING_MAX_POSITIONS",
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
    def __init__(self, strategy: str):
        self.strategy = strategy


class _Deposit:
    def __init__(self, orderable_cash=Decimal("0"), cash=Decimal("0")):
        self.orderable_cash = orderable_cash
        self.cash = cash


class _Balance:
    def __init__(self, total_eval=Decimal("0"), holdings=None):
        self.total_eval = total_eval
        self.holdings = holdings or []


def _holding(symbol: str, eval_amount: float) -> HoldingPosition:
    return HoldingPosition(
        symbol=symbol, name=symbol, qty=1,
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


def test_caps_parse_failure_is_fail_closed(monkeypatch):
    """숫자가 아닌 값이 들어오면 통과가 아니라 차단이어야 한다."""
    monkeypatch.setenv("BARRO_AI_SWING_BUDGET_RATIO", "무효")
    monkeypatch.setenv("BARRO_AI_SWING_MAX_POSITIONS", "무효")
    assert _ai_swing_caps() == (0.0, 0)


def test_negative_ratio_clamped_to_zero(monkeypatch):
    monkeypatch.setenv("BARRO_AI_SWING_BUDGET_RATIO", "-0.5")
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
