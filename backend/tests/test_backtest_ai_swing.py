"""scripts/backtest_ai_swing.py 순수 로직 테스트 (2026-07-30 신규).

API 호출·시뮬 실행 없이 그리드 파싱·판정 강등·유니버스 파일 로딩만 검증한다.
(실제 백테스트는 일봉 캐시가 필요해 운영·개발 머신에서 수동 실행한다.)
"""
from __future__ import annotations

import importlib
import sys
from decimal import Decimal
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))
bt = importlib.import_module("backtest_ai_swing")


# ─── parse_grid ───────────────────────────────────────────────────────────
def test_parse_grid_empty_yields_single_default():
    """빈 스펙 → 기본 파라미터 1회 실행."""
    assert bt.parse_grid("") == [{}]
    assert bt.parse_grid("   ") == [{}]


def test_parse_grid_percent_to_fraction():
    """percent 입력이 fraction 으로 변환된다 (-15 → -0.15)."""
    combos = bt.parse_grid("sl=-15")
    assert combos == [{"sl_pct": Decimal("-0.15")}]


def test_parse_grid_multi_value_axis():
    """한 축 다중값 → 값마다 조합 1개."""
    combos = bt.parse_grid("sl=-8,-15,-20")
    assert [c["sl_pct"] for c in combos] == [
        Decimal("-0.08"), Decimal("-0.15"), Decimal("-0.20"),
    ]


def test_parse_grid_cartesian_product():
    """다축 → 데카르트 곱."""
    combos = bt.parse_grid("sl=-10,-15 max_hold=10,20")
    assert len(combos) == 4
    assert {"sl_pct": Decimal("-0.10"), "max_hold_days": 10} in combos
    assert {"sl_pct": Decimal("-0.15"), "max_hold_days": 20} in combos


def test_parse_grid_raw_field_types():
    """정수/실수 필드는 percent 변환 없이 캐스팅된다."""
    combos = bt.parse_grid("max_hold=8 min_score=4.5")
    assert combos[0]["max_hold_days"] == 8
    assert isinstance(combos[0]["max_hold_days"], int)
    assert combos[0]["min_score"] == 4.5


def test_parse_grid_unknown_key_raises():
    """알 수 없는 키는 조용히 무시하지 않는다 — 스윕 미실행을 놓치면 안 된다."""
    with pytest.raises(ValueError, match="알 수 없는 그리드 키"):
        bt.parse_grid("nonexistent=1")


def test_parse_grid_malformed_token_raises():
    with pytest.raises(ValueError, match="'=' 없음"):
        bt.parse_grid("sl-15")


def test_parse_grid_empty_value_raises():
    with pytest.raises(ValueError, match="그리드 값 없음"):
        bt.parse_grid("sl=")


# ─── combo_label ──────────────────────────────────────────────────────────
def test_combo_label_default():
    assert bt.combo_label({}) == "default"


def test_combo_label_formats_percent_and_raw():
    label = bt.combo_label({"sl_pct": Decimal("-0.15"), "max_hold_days": 20})
    assert "sl=-15%" in label
    assert "max_hold=20" in label


# ─── classify (INSUFFICIENT 강등) ─────────────────────────────────────────
def test_classify_insufficient_on_low_active():
    """표본 부족은 FAIL 이 아니라 INSUFFICIENT — 성과 판정과 구별한다 (§8)."""
    v, fails = bt.classify(active=5, trades=100, avg_ret=2.0, drop1_ok=True, holdout_avg=1.0)
    assert v == "INSUFFICIENT"
    assert any("active" in f for f in fails)


def test_classify_insufficient_on_low_trades():
    v, _ = bt.classify(active=20, trades=10, avg_ret=2.0, drop1_ok=True, holdout_avg=1.0)
    assert v == "INSUFFICIENT"


def test_classify_pass_when_all_gates_met():
    v, fails = bt.classify(active=20, trades=50, avg_ret=1.5, drop1_ok=True, holdout_avg=0.8)
    assert v == "PASS"
    assert fails == []


def test_classify_fail_on_negative_return_with_enough_sample():
    """표본이 충분한데 성과가 음수면 FAIL (INSUFFICIENT 로 숨기지 않는다)."""
    v, fails = bt.classify(active=20, trades=50, avg_ret=-1.5, drop1_ok=True, holdout_avg=0.5)
    assert v == "FAIL"
    assert any("avg_ret" in f for f in fails)


def test_classify_fail_on_drop1_instability():
    v, fails = bt.classify(active=20, trades=50, avg_ret=1.0, drop1_ok=False, holdout_avg=0.5)
    assert v == "FAIL"
    assert any("drop1" in f for f in fails)


# ─── load_universe_file ───────────────────────────────────────────────────
def test_load_universe_file_newline_and_csv(tmp_path):
    p = tmp_path / "uni.txt"
    p.write_text("005930\n000660,035720\n\n  042660  \n", encoding="utf-8")
    assert bt.load_universe_file(str(p)) == ["005930", "000660", "035720", "042660"]


def test_load_universe_file_dedups_and_filters(tmp_path):
    """중복 제거 + 6자리 숫자 아닌 토큰 무시 (순서 보존)."""
    p = tmp_path / "uni.txt"
    p.write_text("005930\n005930\nAAPL\n12345\n1234567\n000660\n", encoding="utf-8")
    assert bt.load_universe_file(str(p)) == ["005930", "000660"]


# ─── cache_as_of ──────────────────────────────────────────────────────────
def test_cache_as_of_returns_unknown_when_missing(tmp_path, monkeypatch):
    """meta.json 부재 시 예외 대신 'unknown' — 리포트가 죽지 않는다."""
    monkeypatch.setattr(bt.oos, "DAILY_CACHE", tmp_path)
    assert bt.cache_as_of() == "unknown"


def test_cache_as_of_reads_updated(tmp_path, monkeypatch):
    (tmp_path / "meta.json").write_text('{"updated": "2026-06-18", "count": 2953}',
                                        encoding="utf-8")
    monkeypatch.setattr(bt.oos, "DAILY_CACHE", tmp_path)
    assert bt.cache_as_of() == "2026-06-18"


# ─── 재사용 계약 (기계를 재구현하지 않았음을 고정) ────────────────────────
def test_reuses_oos_machinery():
    """_oos_validation 의 로더·판정·집계를 그대로 참조한다."""
    for fn in ("load_daily", "select_random_universe", "backtest_universe",
               "summarize", "drop1_sign_stable", "verdict"):
        assert hasattr(bt.oos, fn), f"_oos_validation.{fn} 참조 불가"
    assert bt.oos.MIN_ACTIVE_SYMBOLS == 15
    assert bt.oos.MIN_TRADES == 30


def test_sid_is_ai_swing():
    assert bt.SID == "ai_swing"


# ─── min_atr 축 (2026-09-02 추가) ──────────────────────────────────────────
def test_parse_grid_min_atr_is_float_not_decimal():
    """★ `min_atr_pct` 는 float 필드다 — Decimal 을 넣으면 진입 판정의
    `atr < p.min_atr_pct` 비교에서 TypeError 가 난다. percent → float 소수여야 한다.
    """
    combos = bt.parse_grid("min_atr=3.0,3.5")
    assert combos == [{"min_atr_pct": 0.03}, {"min_atr_pct": 0.035}]
    for c in combos:
        assert isinstance(c["min_atr_pct"], float)


def test_parse_grid_min_atr_feeds_params():
    """파싱 결과가 AiSwingParams 에 그대로 들어가고 비교가 성립해야 한다."""
    from backend.core.strategy.ai_swing import AiSwingParams

    (combo,) = bt.parse_grid("min_atr=3.5")
    p = AiSwingParams(**combo)
    assert p.min_atr_pct == 0.035
    assert (0.04 < p.min_atr_pct) is False   # TypeError 없이 비교되어야 한다
