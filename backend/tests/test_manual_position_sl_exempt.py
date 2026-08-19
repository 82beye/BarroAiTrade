"""수동매매(전략 태그 없는) 보유분의 자동 손절 제외 (2026-08-20 사용자 지시).

배경: 장부(active_positions.json)에 전략이 없는 보유분은 사용자가 직접 산 종목이다.
데몬의 EOD 강제트림(`_force_close_skip` ②)과 evaluate_holdings 의 SL 평가는 이미 이
종목들을 빼는데 **장중 자동 손절 경로만 빠져 있어서**, 수동매매 종목이 시스템 기본 정책
SL 로 팔려나갈 수 있었다. 그 구멍을 `BARRO_SKIP_UNTAGGED_AUTOSELL` 로 막는다.

동시에 15:20 전량청산에는 이 종목들을 포함하라는 앞선 지시가 있으므로, evaluate_holdings
쪽 포함은 **force_mode(강제청산)에서만** 성립하도록 경계를 그었다 — 두 요구가 충돌하지
않는 유일한 지점이다.
"""
from __future__ import annotations

from dataclasses import dataclass

from scripts.evaluate_holdings import _include_untagged_in_eval
from scripts.intraday_buy_daemon import _skip_untagged_autosell, _untagged_symbols


@dataclass
class _Holding:
    symbol: str


@dataclass
class _Pos:
    strategy: str | None


# ── 미태그 심볼 판정 ────────────────────────────────────────────────────────

def test_untagged_when_absent_from_ledger():
    """장부에 항목 자체가 없으면 수동매매다."""
    holdings = [_Holding("005930"), _Holding("000660")]
    active = {"005930": _Pos("f_zone")}
    assert _untagged_symbols(holdings, active) == {"000660"}


def test_untagged_when_strategy_blank():
    """항목은 있으나 strategy 가 빈 문자열/None 이면 수동매매로 본다."""
    holdings = [_Holding("005930"), _Holding("000660"), _Holding("035420")]
    active = {"005930": _Pos(""), "000660": _Pos(None), "035420": _Pos("  ")}
    assert _untagged_symbols(holdings, active) == {"005930", "000660", "035420"}


def test_tagged_positions_not_untagged():
    """전략이 있는 보유분은 자동 손절 대상 그대로다 — 제외 집합에 들어가면 안 된다."""
    holdings = [_Holding("005930"), _Holding("000660")]
    active = {"005930": _Pos("ai_swing"), "000660": _Pos("supertrend")}
    assert _untagged_symbols(holdings, active) == set()


def test_untagged_handles_empty_inputs():
    """보유 0건·장부 None 에서도 예외 없이 빈 집합 — 라이브 경로가 이걸로 죽으면 안 된다."""
    assert _untagged_symbols([], {}) == set()
    assert _untagged_symbols(None, None) == set()
    assert _untagged_symbols([_Holding("")], {}) == set()


# ── 자동 손절 제외 토글 ─────────────────────────────────────────────────────

def test_skip_untagged_autosell_default_off(monkeypatch):
    """기본은 OFF — 미설정 환경에서 기존 동작이 바이트 동일해야 한다."""
    monkeypatch.delenv("BARRO_SKIP_UNTAGGED_AUTOSELL", raising=False)
    assert _skip_untagged_autosell() is False


def test_skip_untagged_autosell_truthy(monkeypatch):
    for raw in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("BARRO_SKIP_UNTAGGED_AUTOSELL", raw)
        assert _skip_untagged_autosell() is True, raw


def test_skip_untagged_autosell_falsy(monkeypatch):
    for raw in ("0", "false", "off", "", "아무값"):
        monkeypatch.setenv("BARRO_SKIP_UNTAGGED_AUTOSELL", raw)
        assert _skip_untagged_autosell() is False, raw


# ── evaluate_holdings: 강제청산에서만 포함 ──────────────────────────────────

_FORCE = {"tp": -100.0, "sl": 100.0}     # 15:20 크론이 쓰는 강제청산 인자
_NORMAL = {"tp": 5.0, "sl": -4.0}        # argparse 기본값 = 일반 TP/SL 평가


def test_untagged_included_only_in_force_close():
    """강제청산(force_mode) + env=1 일 때만 포함."""
    assert _include_untagged_in_eval(**_FORCE, env={"BARRO_EVAL_INCLUDE_MANUAL": "1"}) is True


def test_untagged_excluded_from_normal_sl_even_when_env_on():
    """★ 핵심 계약 — 일반 SL 평가에서는 env 가 켜져 있어도 항상 제외한다."""
    assert _include_untagged_in_eval(**_NORMAL, env={"BARRO_EVAL_INCLUDE_MANUAL": "1"}) is False


def test_untagged_excluded_when_env_off():
    """env 가 꺼져 있으면 강제청산에서도 제외(기존 기본 동작)."""
    assert _include_untagged_in_eval(**_FORCE, env={"BARRO_EVAL_INCLUDE_MANUAL": "0"}) is False
    assert _include_untagged_in_eval(**_FORCE, env={}) is False


def test_partial_force_mode_counts():
    """TP 또는 SL 중 하나만 기본값을 벗어나도 force_mode 다(본 정의와 동일)."""
    env = {"BARRO_EVAL_INCLUDE_MANUAL": "1"}
    assert _include_untagged_in_eval(tp=-100.0, sl=-4.0, env=env) is True
    assert _include_untagged_in_eval(tp=5.0, sl=100.0, env=env) is True
