"""scripts/ai_swing_daemon.py 관측 데몬 테스트 (2026-07-31 신규).

실 API 호출 0건 — 시세 조회기(`build_candle_fetcher`)와 유니버스 로더를
monkeypatch 로 갈아끼운다. 산출 파일은 `BARRO_DATA_DIR` 로 tmp_path 에 격리해
리포의 `data/` 를 건드리지 않는다.

핵심 계약 고정:
  - `BARRO_AI_SWING_ENABLED` 가 truthy 아니면 아무 파일도 만들지 않는다.
  - ok/허용 stale/명시 opt-in scan_only 만 평가하고 나머지는 사유를 남긴다.
  - 조회 실패·캔들 부족은 예외 없이 `skipped` 로 간다.
  - 실행 결과는 일자별 JSONL 에 누적되고 hard failure 는 exit 1/error 로 드러난다.
  - 텔레그램은 선정 전 종목의 점수·현재 신호 여부/사유를 보낸다.
  - ★데몬 소스에 매매 체결 심볼이 한 번도 나오지 않는다 (소스 텍스트 검사).
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))

daemon = importlib.import_module("ai_swing_daemon")

from backend.core.scanner.ai_trade_universe import AiTradeItem, AiTradeUniverse  # noqa: E402
from backend.models.market import MarketType, OHLCV  # noqa: E402

DAEMON_SRC = _REPO / "scripts" / "ai_swing_daemon.py"

UNIVERSE_KEYS = {
    "as_of", "status", "reason", "source_scan_date", "source_pred_date",
    "scan_count", "pred_count", "intersect_count", "items",
}
ITEM_KEYS = {
    "symbol", "name", "scan_score", "blue_line_status", "watermelon_signal",
    "volume_ratio", "pred_rank", "pred_score", "confidence", "consensus_level",
    "rank_combined",
}
SIGNALS_KEYS = {
    "as_of", "universe_status", "universe_reason", "evaluated",
    "signal_count", "signals", "skipped",
}
SIGNAL_ROW_KEYS = {
    "symbol", "name", "entry_price", "score", "reason",
    "sl_price", "tp1_price", "tp2_price",
}


# ─── 픽스처 ──────────────────────────────────────────────────────────────────
@pytest.fixture
def env(tmp_path, monkeypatch):
    """산출 디렉토리 격리 + 관련 env 초기화 (개발 셸 오염 차단)."""
    monkeypatch.setenv("BARRO_DATA_DIR", str(tmp_path))
    for name in (
        "BARRO_AI_SWING_ENABLED", "BARRO_AI_TRADE_DIR", "BARRO_AI_SWING_MIN_PRED_SCORE",
        "BARRO_AI_SWING_MIN_CONSENSUS", "BARRO_AI_SWING_TOP_N", "BARRO_AI_SWING_ALLOW_STALE",
        "BARRO_AI_SWING_FALLBACK",
        "KIWOOM_APP_KEY", "KIWOOM_APP_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    # 로더 자체를 대역으로 바꾸는 테스트는 원본 파일 mtime도 함께 만들지 않는다.
    monkeypatch.setattr(daemon, "validate_current_sources", lambda **_kw: (True, ""))
    return tmp_path


def _uni_path(base: Path) -> Path:
    return base / daemon.UNIVERSE_FILENAME


def _sig_path(base: Path) -> Path:
    return base / daemon.SIGNALS_FILENAME


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _history(base: Path) -> list[dict]:
    paths = list((base / daemon.HISTORY_DIRNAME).glob("*.jsonl"))
    assert len(paths) == 1
    return [json.loads(line) for line in paths[0].read_text(encoding="utf-8").splitlines()]


def _item(symbol: str, name: str, **kw) -> AiTradeItem:
    base = dict(
        scan_score=8.0, blue_line_status="BLUE", watermelon_signal=True,
        volume_ratio=2.5, pred_rank=1, pred_score=82.0, confidence=0.7,
        consensus_level="다수합의", rank_combined=1,
    )
    base.update(kw)
    return AiTradeItem(symbol=symbol, name=name, **base)


def _universe(items, status="ok", reason="", source_scan_date=None) -> AiTradeUniverse:
    source_scan_date = source_scan_date or datetime.now(daemon._KST).date().isoformat()
    return AiTradeUniverse(
        status=status, as_of="2026-07-31T09:00:00+09:00", reason=reason,
        source_scan_date=source_scan_date, source_pred_date=source_scan_date,
        scan_count=30, pred_count=20, items=tuple(items),
    )


def _bar(day: int, o, h, low, c, v) -> OHLCV:
    return OHLCV(
        symbol="TEST", timestamp=datetime(2026, 1, 1) + timedelta(days=day),
        open=float(o), high=float(h), low=float(low), close=float(c),
        volume=float(v), market_type=MarketType.STOCK,
    )


def make_signal_candles() -> list[OHLCV]:
    """AiSwingStrategy(=swing_38 진입 판정) 가 발화하도록 설계한 80 일봉.

    - 임펄스: +8% 양봉(고가 110 / 저가 98) + 거래량 평균 대비 ~4.7배
    - 되돌림: 마지막 종가 105.416 → retrace (110-105.416)/12 = 0.382 (정확히 타겟)
    - 반등: 마지막 봉 몸통 +2.0%
    - ATR%: 기본 봉 폭 6/105 ≈ 5.7% (min_atr_pct 0.03 통과)
    """
    bars = [_bar(i, 100, 103, 97, 100, 1000) for i in range(77)]
    bars.append(_bar(77, 100, 110, 98, 108, 5000))          # 임펄스
    bars.append(_bar(78, 108, 108.5, 103, 104, 1200))        # 눌림
    bars.append(_bar(79, 103.35, 105.5, 103, 105.416, 1100))  # 반등(마지막)
    return bars


class _FakeFetcher:
    """읽기 전용 일봉 조회기 대역 — 실 네트워크 호출 0건."""

    def __init__(self, mapping: dict, errors: dict | None = None) -> None:
        self.mapping = mapping
        self.errors = errors or {}
        self.calls: list[str] = []

    async def fetch_daily(self, symbol: str, **_kw) -> list[OHLCV]:
        self.calls.append(symbol)
        if symbol in self.errors:
            raise self.errors[symbol]
        return self.mapping.get(symbol, [])


# ─── 1. 마스터 플래그 OFF ────────────────────────────────────────────────────
def test_disabled_by_default_writes_nothing(env):
    """ENABLED 미설정 → 즉시 종료. 산출 파일을 만들지 않는다."""
    assert daemon.main(["--sleep", "0"]) == 0
    assert not _uni_path(env).exists()
    assert not _sig_path(env).exists()


def test_disabled_when_flag_is_zero(env, monkeypatch):
    """'0'/'off' 는 truthy 가 아니다."""
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "0")
    assert daemon.main(["--sleep", "0"]) == 0
    assert not _uni_path(env).exists()
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "off")
    assert daemon.main(["--sleep", "0"]) == 0
    assert not _uni_path(env).exists()


def test_is_truthy_contract():
    for raw in ("1", "true", "TRUE", "yes", "on", " On "):
        assert daemon.is_truthy(raw) is True
    for raw in (None, "", "0", "off", "no", "2", "enabled"):
        assert daemon.is_truthy(raw) is False


# ─── 2. 유니버스 no_data ─────────────────────────────────────────────────────
def test_no_data_universe_records_reason_and_skips_evaluation(env, monkeypatch):
    """BARRO_AI_TRADE_DIR 미설정 → 로더 no_data → 신호 평가 안 함, 사유만 기록."""
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")

    def _boom():
        raise AssertionError("no_data 인데 시세 조회기를 만들면 안 된다")

    monkeypatch.setattr(daemon, "build_candle_fetcher", _boom)

    assert daemon.main(["--sleep", "0"]) == 0

    uni = _read(_uni_path(env))
    assert set(uni) == UNIVERSE_KEYS
    assert uni["status"] == "no_data"
    assert uni["reason"] == "ai_trade_dir_unset"
    assert uni["items"] == []

    sig = _read(_sig_path(env))
    assert set(sig) == SIGNALS_KEYS
    assert sig["universe_status"] == "no_data"
    assert sig["universe_reason"] == "ai_trade_dir_unset"
    assert sig["evaluated"] == 0
    assert sig["signal_count"] == 0
    assert sig["signals"] == []
    assert sig["skipped"] == []


def test_stale_universe_blocked_unless_allowed(env, monkeypatch):
    """stale 은 ALLOW_STALE 이 truthy 일 때만 평가한다."""
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")
    monkeypatch.setattr(
        daemon, "load_ai_trade_universe",
        lambda *a, **k: _universe([_item("005930", "삼성전자")], status="stale"),
    )
    fake = _FakeFetcher({"005930": make_signal_candles()})
    monkeypatch.setattr(daemon, "build_candle_fetcher", lambda: fake)

    assert daemon.main(["--sleep", "0"]) == 0
    sig = _read(_sig_path(env))
    assert sig["universe_status"] == "stale"
    assert sig["universe_reason"].startswith("stale_not_allowed:")
    assert sig["evaluated"] == 0
    assert fake.calls == []          # 시세 조회조차 하지 않았다

    monkeypatch.setenv("BARRO_AI_SWING_ALLOW_STALE", "1")
    assert daemon.main(["--sleep", "0"]) == 0
    sig = _read(_sig_path(env))
    assert sig["evaluated"] == 1
    assert fake.calls == ["005930"]


def test_partial_scan_only_is_evaluated(env, monkeypatch):
    """★회귀 고정★ partial(스캔 단독)도 관측해야 한다.

    ai-trade 는 현재 predictions_*.json 을 만들지 않으므로 실환경 로더가 낼 수 있는
    상태는 사실상 partial 뿐이다. 이걸 거부하면 런북 §2 shadow 가 영구히 표본 0건이
    된다 — 이 테스트가 red 면 shadow 관측이 죽은 것이다.
    """
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")
    monkeypatch.setattr(
        daemon, "load_ai_trade_universe",
        lambda *a, **k: _universe(
            [_item("005930", "삼성전자")],
            status="partial", reason="predictions_missing:scan_only",
        ),
    )
    fake = _FakeFetcher({"005930": make_signal_candles()})
    monkeypatch.setattr(daemon, "build_candle_fetcher", lambda: fake)
    sent: list[str] = []

    async def _capture(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(daemon, "send_telegram", _capture)

    assert daemon.main(["--sleep", "0", "--telegram"]) == 0
    sig = _read(_sig_path(env))
    assert sig["universe_status"] == "partial"
    assert sig["evaluated"] == 1
    assert fake.calls == ["005930"]
    assert len(sent) == 1
    assert "스캔 단독 관측 (예측 미포함)" in sent[0]
    assert "교집합 shadow" not in sent[0]
    assert "1. 삼성전자 (005930)" in sent[0]
    assert "scan 8.00 / pred - (예측 없음)" in sent[0]
    assert "현재 신호: ✅ 있음" in sent[0]


def test_partial_scan_only_rejects_old_watchlist(env, monkeypatch):
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")
    monkeypatch.setattr(
        daemon, "load_ai_trade_universe",
        lambda *a, **k: _universe(
            [_item("005930", "삼성전자")],
            status="partial",
            reason="predictions_missing:scan_only",
            source_scan_date="2000-01-01",
        ),
    )
    fake = _FakeFetcher({"005930": make_signal_candles()})
    monkeypatch.setattr(daemon, "build_candle_fetcher", lambda: fake)

    assert daemon.main(["--sleep", "0"]) == 0
    sig = _read(_sig_path(env))
    assert sig["evaluated"] == 0
    assert sig["universe_reason"].startswith("stale_not_allowed:source_scan_date=2000-01-01")
    assert fake.calls == []


def test_partial_scan_only_rejects_old_same_day_file(env, monkeypatch):
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")
    monkeypatch.setattr(
        daemon, "load_ai_trade_universe",
        lambda *a, **k: _universe(
            [_item("005930", "삼성전자")],
            status="partial", reason="predictions_missing:scan_only",
        ),
    )
    monkeypatch.setattr(
        daemon, "validate_current_sources",
        lambda **kw: (
            (False, "source_age:watchlist_today.json:13.00h")
            if kw.get("require_predictions") is False else (True, "")
        ),
    )
    fake = _FakeFetcher({"005930": make_signal_candles()})
    monkeypatch.setattr(daemon, "build_candle_fetcher", lambda: fake)

    assert daemon.main(["--sleep", "0"]) == 0
    sig = _read(_sig_path(env))
    assert sig["evaluated"] == 0
    assert sig["universe_reason"].startswith("stale_not_allowed:source_age:")
    assert fake.calls == []


def test_partial_scan_only_stale_override_is_explicit(env, monkeypatch):
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")
    monkeypatch.setenv("BARRO_AI_SWING_ALLOW_STALE", "1")
    monkeypatch.setattr(
        daemon, "load_ai_trade_universe",
        lambda *a, **k: _universe(
            [_item("005930", "삼성전자")],
            status="partial", reason="predictions_missing:scan_only",
        ),
    )
    monkeypatch.setattr(
        daemon, "validate_current_sources",
        lambda **_kw: (False, "source_age:watchlist_today.json:13.00h"),
    )
    fake = _FakeFetcher({"005930": make_signal_candles()})
    monkeypatch.setattr(daemon, "build_candle_fetcher", lambda: fake)
    sent: list[str] = []

    async def _capture(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(daemon, "send_telegram", _capture)

    assert daemon.main(["--sleep", "0", "--telegram"]) == 0
    sig = _read(_sig_path(env))
    assert sig["evaluated"] == 1
    assert sig["universe_reason"].startswith("stale_allowed:source_age:")
    assert "source_scan_date=" in sig["universe_reason"]
    assert "stale 허용·예측 미포함" in sent[0]


def test_partial_without_optin_is_blocked_with_own_reason(env, monkeypatch):
    """FALLBACK 미설정(=items 빈 partial)은 거부하되, **데몬이 거부했음**이 드러나야 한다.

    로더 사유를 그대로 재사용하면 "왜 평가가 안 됐나"가 로그에서 사라진다.
    """
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")
    monkeypatch.setattr(
        daemon, "load_ai_trade_universe",
        lambda *a, **k: _universe(
            [], status="partial", reason="predictions_missing:fallback_disabled",
        ),
    )
    fake = _FakeFetcher({})
    monkeypatch.setattr(daemon, "build_candle_fetcher", lambda: fake)

    assert daemon.main(["--sleep", "0"]) == 0
    sig = _read(_sig_path(env))
    assert sig["universe_status"] == "partial"
    assert sig["universe_reason"].startswith("partial_not_allowed:")
    assert "BARRO_AI_SWING_FALLBACK" in sig["universe_reason"]
    assert sig["evaluated"] == 0
    assert fake.calls == []


# ─── 3. 정상 교집합 → 스키마 전 필드 ─────────────────────────────────────────
def test_ok_universe_full_schema_and_signal(env, monkeypatch):
    """ok 교집합 → universe/signals 계약 필드가 전부 존재하고 신호가 잡힌다."""
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")
    items = [
        _item("005930", "삼성전자", rank_combined=1),
        _item("000660", "SK하이닉스", rank_combined=2, pred_score=70.0),
    ]
    monkeypatch.setattr(daemon, "load_ai_trade_universe", lambda *a, **k: _universe(items))
    fake = _FakeFetcher({
        "005930": make_signal_candles(),
        "000660": make_signal_candles()[:10],     # 캔들 부족 → skipped
    })
    monkeypatch.setattr(daemon, "build_candle_fetcher", lambda: fake)

    assert daemon.main(["--sleep", "0"]) == 0

    uni = _read(_uni_path(env))
    assert set(uni) == UNIVERSE_KEYS
    assert uni["status"] == "ok"
    assert uni["scan_count"] == 30 and uni["pred_count"] == 20
    assert uni["intersect_count"] == 2
    assert len(uni["items"]) == 2
    for row in uni["items"]:
        assert set(row) == ITEM_KEYS
    assert uni["items"][0]["symbol"] == "005930"
    assert uni["items"][0]["watermelon_signal"] is True

    sig = _read(_sig_path(env))
    assert set(sig) == SIGNALS_KEYS
    assert sig["universe_status"] == "ok"
    assert sig["evaluated"] == 1          # 캔들 부족 종목은 판정 대상이 아니다
    assert sig["signal_count"] == 1
    assert len(sig["signals"]) == 1

    row = sig["signals"][0]
    assert set(row) == SIGNAL_ROW_KEYS
    assert row["symbol"] == "005930"
    assert row["name"] == "삼성전자"
    assert row["entry_price"] == pytest.approx(105.416)
    assert row["score"] > 0
    # SL < 진입가 < TP1 < TP2 (build_exit_plan 단일 원천 기준)
    assert row["sl_price"] < row["entry_price"] < row["tp1_price"] < row["tp2_price"]

    # 캔들 부족 종목은 예외 없이 skipped 로 간다
    assert [s["symbol"] for s in sig["skipped"]] == ["000660"]
    assert sig["skipped"][0]["reason"].startswith("insufficient_candles:10<")


def test_ok_universe_rejects_old_source_mtime(env, monkeypatch):
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")
    monkeypatch.setattr(
        daemon, "load_ai_trade_universe",
        lambda *a, **k: _universe([_item("005930", "삼성전자")]),
    )
    monkeypatch.setattr(
        daemon, "validate_current_sources",
        lambda **_kw: (False, "source_age:watchlist_2026-07-31.json:13.00h"),
    )
    fake = _FakeFetcher({"005930": make_signal_candles()})
    monkeypatch.setattr(daemon, "build_candle_fetcher", lambda: fake)

    assert daemon.main(["--sleep", "0"]) == 0
    sig = _read(_sig_path(env))
    assert sig["evaluated"] == 0
    assert sig["universe_reason"].startswith("source_age:")
    assert fake.calls == []


def test_no_partial_files_left_behind(env, monkeypatch):
    """원자적 저장 — tmp 잔여물이 남지 않는다."""
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")
    monkeypatch.setattr(
        daemon, "load_ai_trade_universe",
        lambda *a, **k: _universe([_item("005930", "삼성전자")]),
    )
    monkeypatch.setattr(
        daemon, "build_candle_fetcher",
        lambda: _FakeFetcher({"005930": make_signal_candles()}),
    )
    assert daemon.main(["--sleep", "0"]) == 0
    names = sorted(p.name for p in env.iterdir())
    assert names == [daemon.HISTORY_DIRNAME, daemon.SIGNALS_FILENAME, daemon.UNIVERSE_FILENAME]
    assert not list(env.rglob("*.tmp"))


# ─── 4. 실패 흡수 ────────────────────────────────────────────────────────────
def test_fetch_error_goes_to_skipped_without_raising(env, monkeypatch):
    """조회 실패 종목은 사유와 함께 skipped — 예외가 전파되지 않는다."""
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")
    items = [_item("005930", "삼성전자", rank_combined=1),
             _item("000660", "SK하이닉스", rank_combined=2)]
    monkeypatch.setattr(daemon, "load_ai_trade_universe", lambda *a, **k: _universe(items))
    monkeypatch.setattr(
        daemon, "build_candle_fetcher",
        lambda: _FakeFetcher(
            {"000660": make_signal_candles()},
            errors={"005930": RuntimeError("kiwoom-native error: rc=3")},
        ),
    )

    assert daemon.main(["--sleep", "0"]) == 0
    sig = _read(_sig_path(env))
    assert sig["evaluated"] == 1
    assert sig["signal_count"] == 1
    reasons = {s["symbol"]: s["reason"] for s in sig["skipped"]}
    assert reasons == {"005930": "fetch_error:RuntimeError"}


def test_missing_kiwoom_keys_records_reason_per_symbol(env, monkeypatch):
    """키 미설정 → 조회기 None → 판정 0건 + 종목별 사유(계약 필드만 사용)."""
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")
    monkeypatch.setattr(
        daemon, "load_ai_trade_universe",
        lambda *a, **k: _universe([_item("005930", "삼성전자")]),
    )
    assert daemon.build_candle_fetcher() is None   # 키가 없으면 만들지 않는다

    assert daemon.main(["--sleep", "0"]) == 0
    sig = _read(_sig_path(env))
    assert sig["evaluated"] == 0
    assert sig["signal_count"] == 0
    assert sig["skipped"] == [
        {"symbol": "005930", "reason": "fetcher_unavailable:kiwoom_keys_unset"}
    ]


def test_unexpected_exception_exits_one_and_records_error(env, monkeypatch):
    """hard failure 는 exit 1 + latest/error 이력으로 모니터링할 수 있다."""
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")

    def _explode(*_a, **_k):
        raise ValueError("boom")

    monkeypatch.setattr(daemon, "load_ai_trade_universe", _explode)
    assert daemon.main(["--sleep", "0"]) == 1
    sig = _read(_sig_path(env))
    assert set(sig) == SIGNALS_KEYS
    assert sig["universe_status"] == "error"
    assert sig["universe_reason"].startswith("daemon_error:ValueError")
    events = _history(env)
    assert len(events) == 1
    assert events[0]["run_status"] == "error"
    assert events[0]["exit_code"] == 1
    assert events[0]["signals"] == sig


# ─── 5. 관측 대상 필터 (순수 함수) ───────────────────────────────────────────
def _row(symbol, pred_score=50.0, consensus="다수합의"):
    return {"symbol": symbol, "pred_score": pred_score, "consensus_level": consensus}


def test_select_items_no_filter_keeps_order():
    rows = [_row("A"), _row("B"), _row("C")]
    out, stats = daemon.select_items(rows)
    assert [r["symbol"] for r in out] == ["A", "B", "C"]
    assert stats["selected"] == 3
    assert stats["input"] == 3


def test_select_items_min_pred_score():
    rows = [_row("A", 80.0), _row("B", 40.0)]
    out, stats = daemon.select_items(rows, min_pred_score=50.0)
    assert [r["symbol"] for r in out] == ["A"]
    assert stats["dropped_pred_score"] == 1


def test_select_items_min_consensus_rank():
    rows = [_row("A", consensus="만장일치"), _row("B", consensus="소수합의"), _row("C", consensus="")]
    out, stats = daemon.select_items(rows, min_consensus="다수합의")
    assert [r["symbol"] for r in out] == ["A"]
    assert stats["dropped_consensus"] == 2


def test_select_items_unknown_consensus_label_is_fail_open():
    """미지 라벨 임계는 필터를 끈다 — 전량 탈락 사고 방지."""
    rows = [_row("A"), _row("B")]
    out, stats = daemon.select_items(rows, min_consensus="STRONG")
    assert len(out) == 2
    assert stats["consensus_filter"].startswith("unknown_label:STRONG")


def test_select_items_top_n():
    rows = [_row(s) for s in ("A", "B", "C", "D")]
    out, stats = daemon.select_items(rows, top_n=2)
    assert [r["symbol"] for r in out] == ["A", "B"]
    assert stats["dropped_top_n"] == 2


def test_top_env_and_cli_limit_observation(env, monkeypatch):
    """--top / BARRO_AI_SWING_TOP_N 이 관측 종목 수를 제한한다 (CLI 우선)."""
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")
    monkeypatch.setenv("BARRO_AI_SWING_TOP_N", "2")
    items = [_item(f"00000{i}", f"종목{i}", rank_combined=i) for i in range(1, 4)]
    monkeypatch.setattr(daemon, "load_ai_trade_universe", lambda *a, **k: _universe(items))
    fake = _FakeFetcher({})       # 전부 캔들 0 → skipped (조회 횟수만 본다)
    monkeypatch.setattr(daemon, "build_candle_fetcher", lambda: fake)

    assert daemon.main(["--sleep", "0"]) == 0
    assert fake.calls == ["000001", "000002"]          # env TOP_N=2
    uni = _read(_uni_path(env))
    assert uni["intersect_count"] == 3                 # 유니버스 파일은 교집합 전량

    fake.calls.clear()
    assert daemon.main(["--sleep", "0", "--top", "1"]) == 0
    assert fake.calls == ["000001"]                    # CLI 가 env 를 이긴다


# ─── 6. 실행 이력 + 텔레그램 선정 목록 ──────────────────────────────────────
def test_history_accumulates_each_run(env, monkeypatch):
    """latest JSON 은 갱신하되 일자별 JSONL 은 실행별로 계속 누적한다."""
    monkeypatch.setenv("BARRO_AI_SWING_ENABLED", "1")
    monkeypatch.setattr(
        daemon, "load_ai_trade_universe",
        lambda *a, **k: _universe([_item("005930", "삼성전자")]),
    )
    monkeypatch.setattr(
        daemon, "build_candle_fetcher",
        lambda: _FakeFetcher({"005930": make_signal_candles()}),
    )

    assert daemon.main(["--sleep", "0"]) == 0
    assert daemon.main(["--sleep", "0"]) == 0

    events = _history(env)
    assert len(events) == 2
    assert [event["run_status"] for event in events] == ["ok", "ok"]
    assert [event["exit_code"] for event in events] == [0, 0]
    assert all(event["selected"][0]["symbol"] == "005930" for event in events)
    assert all(event["signals"]["signal_count"] == 1 for event in events)


def test_summary_lists_every_selected_item_with_signal_state_and_reason():
    """신호 있음/없음/판정 제외가 점수·사유와 함께 번호 목록으로 보인다."""
    items = [
        _item("005930", "삼성전자", scan_score=9.1, pred_score=88.2),
        _item("000660", "SK하이닉스", scan_score=8.3, pred_score=77.4),
        _item("035720", "카카오", scan_score=7.2, pred_score=66.5),
    ]
    uni = daemon.universe_payload(_universe(items))
    selected = uni["items"]
    sig = daemon.signals_payload(
        universe_status="ok",
        universe_reason="",
        evaluated=2,
        signals=[{
            "symbol": "005930",
            "name": "삼성전자",
            "entry_price": 70100,
            "score": 6.25,
            "reason": "Fib < 0.4 반등",
            "sl_price": 66595,
            "tp1_price": 84120,
            "tp2_price": 105150,
        }],
        skipped=[{"symbol": "035720", "reason": "insufficient_candles:10<60"}],
    )

    text = daemon.summary_text(uni, sig, selected)

    assert "단테 교집합 shadow" in text
    assert "1. 삼성전자 (005930)" in text
    assert "scan 9.10 / pred 88.20" in text
    assert "현재 신호: ✅ 있음 · 전략점수 6.25 · Fib &lt; 0.4 반등" in text
    assert "2. SK하이닉스 (000660)" in text
    assert "현재 신호: ⭕ 없음 · 현재 진입 조건 미충족" in text
    assert "3. 카카오 (035720)" in text
    assert "현재 신호: ⚠️ 판정 제외 · insufficient_candles:10&lt;60" in text


def test_send_telegram_uses_html_chunks(monkeypatch):
    """긴 선정 목록도 잘리지 않도록 기존 notifier 의 chunk 전송을 재사용한다."""
    from backend.core.notify.telegram import TelegramNotifier

    calls: dict = {}

    class _Notifier:
        async def send_chunks(self, text: str) -> None:
            calls["text"] = text

    def _from_env(_cls, **kwargs):
        calls["kwargs"] = kwargs
        return _Notifier()

    monkeypatch.setattr(TelegramNotifier, "from_env", classmethod(_from_env))
    asyncio.run(daemon.send_telegram("선정 목록"))

    assert calls == {"kwargs": {"parse_mode": "HTML"}, "text": "선정 목록"}


# ─── 7. ★주문 심볼 부재 (소스 텍스트 검사) ──────────────────────────────────
def test_source_has_no_execution_symbols():
    """데몬 소스에 매매 체결 관련 심볼이 한 번도 나오면 안 된다 (하드 계약)."""
    src = DAEMON_SRC.read_text(encoding="utf-8")
    forbidden = [
        "OrderExecutor",
        "LiveOrderGate",
        "place_" + "buy",
        "place_" + "sell",
        "kiwoom_native_orders",
        "backend.core.execution",
    ]
    found = [tok for tok in forbidden if tok in src]
    assert found == [], f"금지 심볼이 데몬 소스에 있다: {found}"


def test_source_declares_observation_only():
    """모듈 docstring 은 '관측 전용' 이라고만 쓴다 (문구 계약)."""
    src = DAEMON_SRC.read_text(encoding="utf-8")
    assert "관측 전용" in src
    assert "주문 미배선" not in src
