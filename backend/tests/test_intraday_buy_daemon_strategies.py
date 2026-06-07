"""intraday_buy_daemon 전략 토글 + 슈퍼트렌드 이중가동 가드 테스트 (BAR-OPS-10, 2026-06-03).

6/2 'supertrend만' 의도였는데 데몬이 f_zone/sf_zone/gold_zone 를 자동매매한 문제 정정:
  - --strategies 로 일반 전략 on/off (빈 값 → 비활성)
  - SUPERTREND_AUTO_ENABLED 감지 시 데몬 --supertrend 양보(이중 주문 방지)
"""
from __future__ import annotations

from scripts.intraday_buy_daemon import (
    DEFAULT_ZONE_STRATEGIES,
    _parse_strategies,
    _supertrend_yield_to_bot,
)


# ── --strategies 파싱 ────────────────────────────────────────────────────────
def test_parse_strategies_default():
    assert _parse_strategies("f_zone,sf_zone,gold_zone") == ["f_zone", "sf_zone", "gold_zone"]
    # BAR-OPS-33: swing_38 라이브 활성화 — 안정성 1위 전략을 기본 zone 에 포함.
    assert DEFAULT_ZONE_STRATEGIES == ["swing_38", "f_zone", "sf_zone", "gold_zone"]


def test_parse_strategies_empty_disables():
    # 빈 값/none/off → 일반 전략 비활성(슈퍼트렌드 단독)
    assert _parse_strategies("") == []
    assert _parse_strategies("   ") == []
    assert _parse_strategies("none") == []
    assert _parse_strategies("OFF") == []
    assert _parse_strategies(None) == []


def test_parse_strategies_subset_and_strip():
    assert _parse_strategies("f_zone") == ["f_zone"]
    assert _parse_strategies(" f_zone , gold_zone ") == ["f_zone", "gold_zone"]
    assert _parse_strategies("supertrend") == ["supertrend"]


# ── 슈퍼트렌드 이중가동 가드 ─────────────────────────────────────────────────
def test_supertrend_yield_when_bot_enabled():
    # run_telegram_bot 가 슈퍼트렌드 담당 → 데몬은 양보(False)
    for v in ("1", "true", "TRUE", "yes", "on"):
        assert _supertrend_yield_to_bot(True, {"SUPERTREND_AUTO_ENABLED": v}) is False


def test_supertrend_keeps_when_bot_disabled():
    assert _supertrend_yield_to_bot(True, {}) is True
    assert _supertrend_yield_to_bot(True, {"SUPERTREND_AUTO_ENABLED": "0"}) is True
    assert _supertrend_yield_to_bot(True, {"SUPERTREND_AUTO_ENABLED": "false"}) is True


def test_supertrend_off_stays_off():
    # 애초에 --supertrend 미사용이면 env 무관하게 False
    assert _supertrend_yield_to_bot(False, {"SUPERTREND_AUTO_ENABLED": "1"}) is False
    assert _supertrend_yield_to_bot(False, {}) is False


# ── BAR-OPS-33: swing_38 자체분할 → 데몬 DCA 비활성 ──────────────────────────
def test_no_dca_strategies_contains_swing_38():
    from scripts.intraday_buy_daemon import _NO_DCA_STRATEGIES
    assert "swing_38" in _NO_DCA_STRATEGIES
    # gold_zone 은 _MEANREV(조건부)이지 _NO_DCA(무조건)는 아님
    assert "gold_zone" not in _NO_DCA_STRATEGIES
