"""ai_swing 개장 진입 — 기본 OFF · 창 판정 · 유니버스 선택 · fail-closed · 추천 리더."""
import json
from datetime import time

import pytest

from backend.core.strategy.ai_swing_open_entry import (
    DEFAULT_UNIVERSE,
    ENV_ENABLED,
    ENV_MIN_PRICE,
    ENV_NEAR20,
    ENV_TURNOVER,
    ENV_UNIVERSE,
    ENV_UNTIL,
    OpenEntryConfig,
    compute_factors,
    evaluate_open_entry,
    in_open_entry_window,
    read_reco_codes,
    select_universe,
)

BUY_START = time(9, 5)


class Bar:
    def __init__(self, high, close, volume):
        self.high, self.close, self.volume = high, close, volume


def bars(n=20, high=100.0, close=100.0, volume=1_000_000.0):
    return [Bar(high, close, volume) for _ in range(n)]


# ── 기본 OFF ──────────────────────────────────────────────────────────
def test_disabled_by_default(monkeypatch):
    for k in (ENV_ENABLED, ENV_UNIVERSE, ENV_UNTIL, ENV_NEAR20, ENV_TURNOVER, ENV_MIN_PRICE):
        monkeypatch.delenv(k, raising=False)
    cfg = OpenEntryConfig.from_env()
    assert cfg.enabled is False
    assert cfg.universe == DEFAULT_UNIVERSE == "scan"
    assert in_open_entry_window(time(9, 6), cfg, BUY_START) is False
    ok, why = evaluate_open_entry(bars(high=100.0, close=99.0, volume=50_000_000.0), cfg)
    assert ok is False and "비활성" in why


def test_env_activation_and_overrides(monkeypatch):
    monkeypatch.setenv(ENV_ENABLED, "1")
    monkeypatch.setenv(ENV_UNIVERSE, "UNION")
    monkeypatch.setenv(ENV_UNTIL, "0915")
    monkeypatch.setenv(ENV_NEAR20, "-3.0")
    monkeypatch.setenv(ENV_TURNOVER, "5e9")
    monkeypatch.setenv(ENV_MIN_PRICE, "10000")
    c = OpenEntryConfig.from_env()
    assert c.enabled and c.universe == "union" and c.until_hhmm == "0915"
    assert c.near20_pct == pytest.approx(-3.0)
    assert c.min_turnover == pytest.approx(5e9)
    assert c.min_price == pytest.approx(10000.0)


@pytest.mark.parametrize("bad", ["", "  ", "scan_only", "교집합", "xyz"])
def test_bad_universe_falls_back(monkeypatch, bad):
    monkeypatch.setenv(ENV_ENABLED, "1")
    monkeypatch.setenv(ENV_UNIVERSE, bad)
    assert OpenEntryConfig.from_env().universe == "scan"


@pytest.mark.parametrize("bad", ["", "9:05", "905", "09050", "abcd"])
def test_bad_until_falls_back(monkeypatch, bad):
    monkeypatch.setenv(ENV_ENABLED, "1")
    monkeypatch.setenv(ENV_UNTIL, bad)
    assert OpenEntryConfig.from_env().until_hhmm == "0910"


# ── 진입 창 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("t,expect", [
    (time(9, 0), False),    # BUY_START 이전
    (time(9, 4), False),
    (time(9, 5), True),     # 경계 포함
    (time(9, 9), True),
    (time(9, 10), False),   # until 배타
    (time(9, 30), False),
    (time(14, 0), False),
])
def test_window_boundaries(t, expect):
    cfg = OpenEntryConfig(enabled=True)
    assert in_open_entry_window(t, cfg, BUY_START) is expect


def test_window_accepts_datetime():
    from datetime import datetime
    cfg = OpenEntryConfig(enabled=True)
    assert in_open_entry_window(datetime(2026, 10, 7, 9, 6), cfg, BUY_START) is True


def test_window_bad_input_is_false():
    cfg = OpenEntryConfig(enabled=True)
    assert in_open_entry_window(None, cfg, BUY_START) is False


# ── 유니버스 선택 ─────────────────────────────────────────────────────
@pytest.mark.parametrize("uni,expect", [
    ("scan", ["A", "B"]),
    ("pred", ["B", "C"]),
    ("union", ["A", "B", "C"]),
    ("inter", ["B"]),
])
def test_select_universe(uni, expect):
    cfg = OpenEntryConfig(enabled=True, universe=uni)
    assert select_universe(cfg, ["A", "B"], ["B", "C"]) == expect


def test_select_universe_dedupes_and_sorts():
    cfg = OpenEntryConfig(enabled=True, universe="union")
    assert select_universe(cfg, ["B", "A", "A"], ["C", "B"]) == ["A", "B", "C"]


def test_select_universe_handles_none():
    cfg = OpenEntryConfig(enabled=True, universe="union")
    assert select_universe(cfg, None, None) == []


# ── fail-closed (일반 게이트와 반대 방향) ──────────────────────────────
@pytest.mark.parametrize("candles", [None, [], bars(n=19), [object()] * 20])
def test_insufficient_data_is_blocked(candles):
    """팩터 게이트가 진입 권위라 자료 부족은 **차단**이어야 한다."""
    cfg = OpenEntryConfig(enabled=True)
    ok, why = evaluate_open_entry(candles, cfg)
    assert ok is False
    if candles:
        assert "자료부족" in why or "fail-closed" in why


def test_zero_price_blocked():
    cfg = OpenEntryConfig(enabled=True)
    ok, _ = evaluate_open_entry(bars(high=0.0, close=0.0), cfg)
    assert ok is False


# ── 임계 판정 ─────────────────────────────────────────────────────────
def test_passes_all_conditions():
    # 근접 −2% · 거래대금 98원×5천만주 = 49억 · 주가 9.8만원
    b = bars(high=100_000.0, close=98_000.0, volume=50_000.0)
    ok, why = evaluate_open_entry(b, OpenEntryConfig(enabled=True))
    assert ok is True and why == ""


def test_blocks_far_from_high():
    b = bars(high=100_000.0, close=90_000.0, volume=50_000.0)
    ok, why = evaluate_open_entry(b, OpenEntryConfig(enabled=True))
    assert ok is False and "20일고가" in why


def test_blocks_low_turnover():
    b = bars(high=100_000.0, close=99_000.0, volume=10.0)
    ok, why = evaluate_open_entry(b, OpenEntryConfig(enabled=True))
    assert ok is False and "거래대금" in why


def test_blocks_low_price():
    b = bars(high=4_900.0, close=4_800.0, volume=10_000_000.0)
    ok, why = evaluate_open_entry(b, OpenEntryConfig(enabled=True))
    assert ok is False and "주가" in why


def test_reason_lists_all_failures():
    b = bars(high=10_000.0, close=4_000.0, volume=10.0)
    ok, why = evaluate_open_entry(b, OpenEntryConfig(enabled=True))
    assert ok is False
    assert "20일고가" in why and "거래대금" in why and "주가" in why


def test_compute_factors_values():
    b = bars(high=100.0, close=100.0, volume=1000.0)
    b[-1] = Bar(high=100.0, close=96.0, volume=1000.0)
    near, turn, price = compute_factors(b)
    assert near == pytest.approx(-4.0)
    assert price == pytest.approx(96.0)
    assert turn == pytest.approx((19 * 100 * 1000 + 96 * 1000) / 20)


# ── 추천 파일 리더 ────────────────────────────────────────────────────
def test_read_reco_codes(tmp_path, monkeypatch):
    (tmp_path / "watchlist_2026-10-07.json").write_text(
        json.dumps({"stocks": [{"code": "000001"}, {"code": "000002"}]}), encoding="utf-8")
    (tmp_path / "predictions_2026-10-07.json").write_text(
        json.dumps({"stocks": [{"code": "000002"}, {"code": "000003"}]}), encoding="utf-8")
    monkeypatch.setenv("BARRO_AI_TRADE_DIR", str(tmp_path))
    scan, pred, why = read_reco_codes("2026-10-07")
    assert scan == ["000001", "000002"]
    assert pred == ["000002", "000003"]
    assert why == ""


def test_read_reco_codes_missing_dir(monkeypatch):
    monkeypatch.delenv("BARRO_AI_TRADE_DIR", raising=False)
    assert read_reco_codes("2026-10-07") == ([], [], "ai_trade_dir_unset")
    monkeypatch.setenv("BARRO_AI_TRADE_DIR", "/nonexistent/xyz")
    assert read_reco_codes("2026-10-07") == ([], [], "ai_trade_dir_missing")


def test_read_reco_codes_missing_files(tmp_path, monkeypatch):
    monkeypatch.setenv("BARRO_AI_TRADE_DIR", str(tmp_path))
    assert read_reco_codes("2026-10-07") == ([], [], "files_missing")


def test_read_reco_codes_malformed(tmp_path, monkeypatch):
    (tmp_path / "watchlist_2026-10-07.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "predictions_2026-10-07.json").write_text(
        json.dumps({"stocks": [{"code": "000003"}]}), encoding="utf-8")
    monkeypatch.setenv("BARRO_AI_TRADE_DIR", str(tmp_path))
    scan, pred, why = read_reco_codes("2026-10-07")
    assert scan == [] and pred == ["000003"] and why == "watchlist_missing"


def test_read_reco_codes_skips_blank_codes(tmp_path, monkeypatch):
    (tmp_path / "watchlist_2026-10-07.json").write_text(
        json.dumps({"stocks": [{"code": " 000001 "}, {"code": ""}, {}, None]}), encoding="utf-8")
    (tmp_path / "predictions_2026-10-07.json").write_text(
        json.dumps({"stocks": []}), encoding="utf-8")
    monkeypatch.setenv("BARRO_AI_TRADE_DIR", str(tmp_path))
    scan, _, _ = read_reco_codes("2026-10-07")
    assert scan == ["000001"]


# ── 데몬 통합: 기본 OFF 시 기존 OPEN_HOLD 거동이 보존되는가 ─────────────
def test_daemon_defaults_open_entry_off():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "scripts" / "intraday_buy_daemon.py"
    spec = importlib.util.spec_from_file_location("_d_oe", str(p))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m._AI_SWING_OPEN_ENTRY.enabled is False, "기본값은 OFF 여야 한다"
    assert m._AI_SWING_OPEN_ENTRY.universe == "scan"
    # BUY_START 와 모듈 윈도우 기준이 어긋나지 않는지
    assert m.BUY_START == time(9, 5)
    assert in_open_entry_window(time(9, 6), m._AI_SWING_OPEN_ENTRY, m.BUY_START) is False


def test_daemon_open_hold_still_guards_zone_strategies():
    """개장 진입은 ai_swing 전용 — 존 3전략 보류 로그·면제 집합이 보존돼야 한다."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "scripts" / "intraday_buy_daemon.py").read_text()
    assert "[OPEN-HOLD] 개장러시 보류" in src, "존 3전략 보류 로그가 사라졌다"
    assert "zone_strategies = [_AI_SWING_SID]" in src, "개장 창에서 ai_swing 단독 제한이 없다"
    assert "[OPEN-ENTRY]" in src and "[OPEN-ENTRY-ERR]" in src, "개장 진입 로그·오류격리 누락"
