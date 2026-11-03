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


def _wrapper_exclusions(multiday: str = "0") -> set[str]:
    """래퍼가 실제로 넘기는 --exclude-strategy 값.

    [2026-11-03] 목록이 리터럴에서 `$EXCLUDE` 변수로 바뀌어 정규식 파싱이 불가능해졌다.
    스크립트의 EXCLUDE 산출 블록을 bash 로 실제 실행해 결과를 읽는다 — 리터럴을 읽는
    것보다 실동작에 가깝다.
    """
    import subprocess

    src = (_REPO / "scripts" / "eod_force_close.sh").read_text(encoding="utf-8")
    assert '--exclude-strategy "$EXCLUDE"' in src, "래퍼가 $EXCLUDE 를 넘기지 않는다"
    lines = [
        l for l in src.splitlines()
        if l.startswith("EXCLUDE=") or l.startswith("case ") or "EXCLUDE=\"$EXCLUDE" in l
        or l.strip() == "esac"
    ]
    assert lines, "EXCLUDE 산출 블록을 찾지 못했다"
    script = "\n".join(lines) + '\nprintf %s "$EXCLUDE"'
    out = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "BARRO_ZONE_MULTIDAY_ENABLED": multiday},
    ).stdout
    return {x for x in out.split(",") if x}


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


def test_wrapper_includes_zones_when_multiday_on():
    """[2026-11-03] 존3 다일보유 ON 이면 래퍼도 존3를 제외한다."""
    excl = _wrapper_exclusions("1")
    for z in ("f_zone", "sf_zone", "gold_zone"):
        assert z in excl, z


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
