"""EOD 강제청산(carry-limit) 전략 면제 테스트 (2026-06-22).

배경: 종베(closing_bet) 보유분은 _closing_bet_held() 로 강제청산 제외돼 있었으나,
다일보유가 설계인 swing_38 은 명시적 제외 리스트에 없어 이월총액 한도(20%) 초과 트림 시
청산될 수 있었다. 또 전략 태그가 없는 수동/외부 매매 보유분은 자동 매매 전략의 소유가
아니므로 강제청산 대상에서 제외한다. _FORCE_CLOSE_EXEMPT_STRATEGIES + _force_close_skip()
로 명시 보존한다.

주의: 이 면제는 'EOD 강제 트림(carry-limit)' 에만 한정된다. 장중 보유평가의 swing_38
자체 손절/시간청산은 그대로 적용된다(여기서 제외하지 않음).
"""
from __future__ import annotations

from scripts.intraday_buy_daemon import (
    DEFAULT_ZONE_STRATEGIES,
    _FORCE_CLOSE_EXEMPT_STRATEGIES,
    _force_close_skip,
)


def test_swing38_in_force_close_exempt_set():
    assert "swing_38" in _FORCE_CLOSE_EXEMPT_STRATEGIES


def test_swing38_skipped_by_strategy():
    """swing_38 보유분은 EOD 강제청산에서 제외(True)."""
    assert _force_close_skip("005930", "swing_38", cb_skip=set()) is True


def test_closing_bet_symbol_skipped():
    """종베 보유분(심볼 기준)은 strategy 와 무관하게 제외."""
    assert _force_close_skip("000660", strategy=None, cb_skip={"000660"}) is True
    # 종베 심볼이면 strategy 가 f_zone 이어도 제외
    assert _force_close_skip("000660", strategy="f_zone", cb_skip={"000660"}) is True


def test_non_exempt_strategies_not_skipped():
    """단타/추세 전략(f/sf/gold/supertrend)은 강제청산 대상(False)."""
    for strat in ("f_zone", "sf_zone", "gold_zone", "supertrend"):
        assert _force_close_skip("005930", strat, cb_skip=set()) is False


def test_blank_strategy_skipped_when_not_closing_bet():
    """strategy 미상/공백 + 종베 아님 → 전략없는 수동매매로 보고 강제청산 제외."""
    assert _force_close_skip("005930", strategy=None, cb_skip=set()) is True
    assert _force_close_skip("005930", strategy="", cb_skip=set()) is True
    assert _force_close_skip("005930", strategy="   ", cb_skip=set()) is True


def test_swing38_in_default_zone_strategies():
    """데몬 기본 전략 집합에 swing_38 포함 (BAR-OPS-33 라이브 활성)."""
    assert "swing_38" in DEFAULT_ZONE_STRATEGIES
