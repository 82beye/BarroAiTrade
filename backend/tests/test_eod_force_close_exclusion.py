"""EOD 강제청산 제외 목록 — 다일보유 전략(ai_swing·swing_38) 보호 회귀 방지.

force_mode(--tp/--sl 지정)에서는 PositionContext 가 만들어지지 않아 전략 override
(SL −8%)·min_hold 가 무시되고 `--tp -100` 이 전량 청산을 유발한다. 따라서 제외 목록이
유일한 보호 수단이다. 이 파일은 그 목록이 조용히 되돌아가는 것을 막는다.
"""
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
MULTIDAY = ("ai_swing", "swing_38")


def _wrapper_exclusions() -> set[str]:
    s = (_REPO / "scripts" / "eod_force_close.sh").read_text(encoding="utf-8")
    # 주석(#로 시작하는 줄)은 제외하고 실제 플래그만 본다
    body = "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("#"))
    m = re.search(r"--exclude-strategy\s+([A-Za-z0-9_,]+)", body)
    assert m, "래퍼에서 --exclude-strategy 플래그를 찾지 못했다"
    return {x for x in m.group(1).split(",") if x}


def _argparse_default() -> set[str]:
    s = (_REPO / "scripts" / "evaluate_holdings.py").read_text(encoding="utf-8")
    m = re.search(r'"--exclude-strategy",\s*default="([^"]*)"', s)
    assert m, "evaluate_holdings.py 에서 --exclude-strategy default 를 찾지 못했다"
    return {x for x in m.group(1).split(",") if x}


@pytest.mark.parametrize("strategy", MULTIDAY)
def test_wrapper_excludes_multiday_strategies(strategy):
    excl = _wrapper_exclusions()
    assert strategy in excl, (
        f"{strategy} 가 EOD 강제청산 제외 목록에 없다 — force_mode 는 min_hold·SL 을 "
        f"무시하므로 당일 전량 청산된다. 현재 목록: {sorted(excl)}"
    )


@pytest.mark.parametrize("strategy", MULTIDAY)
def test_argparse_default_excludes_multiday_strategies(strategy):
    assert strategy in _argparse_default()


def test_signal_only_strategies_still_excluded():
    """기존 제외 대상(supertrend·limit_up_chase)이 유지되는지."""
    excl = _wrapper_exclusions()
    assert "supertrend" in excl and "limit_up_chase" in excl


def test_wrapper_and_default_agree():
    assert _wrapper_exclusions() == _argparse_default()


def test_daemon_exempt_set_matches_multiday():
    """데몬 내부 트림 면제 집합과 크론 제외 목록이 다일보유 전략에서 일치해야 한다."""
    s = (_REPO / "scripts" / "intraday_buy_daemon.py").read_text(encoding="utf-8")
    m = re.search(r"_FORCE_CLOSE_EXEMPT_STRATEGIES\s*=\s*\{([^}]*)\}", s)
    assert m, "_FORCE_CLOSE_EXEMPT_STRATEGIES 를 찾지 못했다"
    exempt = {x.strip().strip('"').strip("'") for x in m.group(1).split(",") if x.strip()}
    assert exempt >= set(MULTIDAY), f"데몬 면제 집합이 다일보유 전략을 빠뜨렸다: {exempt}"
    assert exempt <= _wrapper_exclusions(), (
        f"데몬은 면제하는데 크론은 청산하는 전략이 있다: {exempt - _wrapper_exclusions()}"
    )
