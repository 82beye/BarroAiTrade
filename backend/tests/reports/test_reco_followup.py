"""추천 종목 2주 추적 리포트 — 추적·집계 로직."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location(
        "_reco_followup", _REPO / "scripts" / "recommendation_followup_report.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["_reco_followup"] = m
    spec.loader.exec_module(m)
    return m


def _mk(mod, code, rows):
    mod._candle_cache[code] = rows


def _bar(d, o, h, l, c):
    return {"date": d, "open": o, "high": h, "low": l, "close": c, "volume": 1}


# ── track() 기본 ─────────────────────────────────────────────────────
def test_track_uses_reco_day_open_as_base(mod):
    _mk(mod, "T001", [
        _bar("20260901", 100, 110, 95, 105),   # 추천일 — 기준가 100
        _bar("20260902", 105, 130, 100, 120),  # 최고 130
        _bar("20260903", 120, 125, 80, 90),    # 최저 80
    ])
    t = mod.track("T001", "2026-09-01", window=3)
    assert t["base"] == 100
    assert t["max_up"] == pytest.approx(30.0)    # 130/100-1
    assert t["max_dn"] == pytest.approx(-20.0)   # 80/100-1
    assert t["ret"] == pytest.approx(-10.0)      # 90/100-1
    assert t["days"] == 3 and t["complete"] is True


def test_track_includes_reco_day_in_window(mod):
    """추천일이 창에 포함된다 — 추천일 장중 극값도 집계된다."""
    _mk(mod, "T002", [_bar("20260901", 100, 150, 90, 100)])
    t = mod.track("T002", "2026-09-01", window=10)
    assert t["max_up"] == pytest.approx(50.0)
    assert t["max_dn"] == pytest.approx(-10.0)
    assert t["days"] == 1 and t["complete"] is False   # 창 미완성


def test_track_marks_incomplete_window(mod):
    _mk(mod, "T003", [_bar("20260901", 100, 105, 99, 102),
                      _bar("20260902", 102, 108, 101, 107)])
    t = mod.track("T003", "2026-09-01", window=10)
    assert t["days"] == 2 and t["complete"] is False


def test_track_stops_at_window_edge(mod):
    bars = [_bar(f"202609{d:02d}", 100, 100 + d, 100 - d, 100) for d in range(1, 21)]
    _mk(mod, "T004", bars)
    t = mod.track("T004", "2026-09-01", window=5)
    assert t["days"] == 5
    assert t["max_up"] == pytest.approx(5.0)     # 창 안 최대 high=105
    assert t["last_date"] == "20260905"


# ── track() 경계 ─────────────────────────────────────────────────────
def test_track_returns_none_when_reco_day_missing(mod):
    """추천일 캔들 부재(휴장·미수록) → None."""
    _mk(mod, "T005", [_bar("20260902", 100, 101, 99, 100)])
    assert mod.track("T005", "2026-09-01", window=10) is None


def test_track_returns_none_on_empty_candles(mod):
    _mk(mod, "T006", [])
    assert mod.track("T006", "2026-09-01", window=10) is None


def test_track_returns_none_on_zero_base(mod):
    _mk(mod, "T007", [_bar("20260901", 0, 10, 0, 5)])
    assert mod.track("T007", "2026-09-01", window=10) is None


# ── summarize() ──────────────────────────────────────────────────────
def _item(ret, up, dn, code="X", complete=True):
    return {"code": code, "name": code, "ret": ret, "max_up": up,
            "max_dn": dn, "complete": complete, "reco": "09-01"}


def test_summarize_counts_and_averages(mod):
    s = mod.summarize([_item(10, 20, -5, "A"), _item(-4, 3, -10, "B"),
                       _item(0, 1, -1, "C")])
    assert s["n"] == 3
    assert s["up"] == 1                      # ret>0 만 상승
    assert s["down"] == 2
    assert s["win"] == pytest.approx(100 / 3)
    assert s["ret_avg"] == pytest.approx(2.0)
    assert s["max_up_avg"] == pytest.approx(8.0)
    assert s["max_dn_avg"] == pytest.approx(-16 / 3)
    assert s["best"]["code"] == "A"
    assert s["worst"]["code"] == "B"


def test_summarize_empty(mod):
    assert mod.summarize([]) == {}


def test_summarize_counts_complete_only(mod):
    s = mod.summarize([_item(1, 1, -1, "A", True), _item(1, 1, -1, "B", False)])
    assert s["n"] == 2 and s["complete"] == 1


# ── Top N 종목 중복 제거 ─────────────────────────────────────────────
def test_block_dedupes_symbol_in_top_list(mod):
    """같은 종목이 여러 날 추천되면 Top N 을 독점하지 않는다."""
    items = [_item(5, 50, -1, "DUP"), _item(4, 45, -2, "DUP"),
             _item(3, 40, -3, "DUP"), _item(2, 30, -4, "OTHER")]
    lines = mod._block("T", items, top=3)
    # ▲/▼ 두 Top 섹션 각각에서 같은 종목이 1행을 넘지 않아야 한다.
    section, per_section = None, {}
    for ln in lines:
        if "Top" in ln:
            section = ln.strip()[0]
            per_section[section] = []
        elif section and ln.startswith("    "):
            per_section[section].append(ln.split()[0])
    assert per_section, f"Top 섹션을 찾지 못했다: {lines}"
    for sec, codes in per_section.items():
        assert codes.count("DUP") == 1, f"{sec} 섹션에서 중복 독점: {codes}"
        assert "OTHER" in codes, f"{sec} 섹션에 다른 종목이 없다: {codes}"


# ── 캐시 경로 검증 (조용한 0건 방지) ─────────────────────────────────
def test_require_cache_raises_when_missing(mod, tmp_path, monkeypatch):
    monkeypatch.setenv("BARRO_OHLCV_CACHE_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as e:
        mod._require_cache()
    assert "일봉 캐시를 찾을 수 없다" in str(e.value)


def test_require_cache_accepts_populated_dir(mod, tmp_path, monkeypatch):
    for i in range(120):
        (tmp_path / f"{i:06d}.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("BARRO_OHLCV_CACHE_DIR", str(tmp_path))
    assert mod._require_cache() == tmp_path


def test_cache_dir_falls_back_to_premarket_env(mod, tmp_path, monkeypatch):
    monkeypatch.delenv("BARRO_OHLCV_CACHE_DIR", raising=False)
    monkeypatch.setenv("BARRO_PREMARKET_CACHE_DIR", str(tmp_path))
    assert mod._cache_dir() == tmp_path


# ── 추천 파일 파싱 ───────────────────────────────────────────────────
def test_load_reco_normalizes_code_field(mod, tmp_path, monkeypatch):
    monkeypatch.setenv("BARRO_AI_TRADE_DIR", str(tmp_path))
    (tmp_path / "watchlist_2026-09-01.json").write_text(json.dumps({
        "stocks": [{"code": "A005930", "name": "삼성전자"},
                   {"symbol": "660", "name": "짧은코드"},
                   {"code": "000000", "name": "무효"},
                   {"name": "코드없음"}]
    }), encoding="utf-8")
    rows = mod._load_reco("watchlist", "2026-09-01")
    codes = [r["code"] for r in rows]
    assert "005930" in codes          # A 접두 제거
    assert "000660" in codes          # zfill
    assert "000000" not in codes      # 무효 제외
    assert len(rows) == 2


def test_load_reco_missing_file_returns_empty(mod, tmp_path, monkeypatch):
    monkeypatch.setenv("BARRO_AI_TRADE_DIR", str(tmp_path))
    assert mod._load_reco("watchlist", "1999-01-01") == []
