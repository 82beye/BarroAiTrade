"""ai_trade_universe 로더 단위 테스트 — tmp_path 에 JSON 픽스처를 써서 검증.

실 운영 파일(운영 머신의 ai-trade 산출물)은 사용하지 않는다.
env 는 monkeypatch 로 매 테스트 격리한다(테스트 간 오염 금지).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from backend.core.scanner.ai_trade_universe import (
    ENV_ALLOW_STALE,
    ENV_DIR,
    ENV_FALLBACK,
    ENV_MAX_AGE_H,
    load_ai_trade_universe,
    validate_current_sources,
)

TODAY = date(2026, 7, 30)
TODAY_ISO = "2026-07-30"
YESTERDAY_ISO = "2026-07-29"


# ─── 픽스처 ──────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """ai-swing 관련 env 전부 제거 — 기본은 기능 OFF 상태."""
    for key in (ENV_DIR, ENV_FALLBACK, ENV_MAX_AGE_H, ENV_ALLOW_STALE):
        monkeypatch.delenv(key, raising=False)


def _scan_stock(code: str, *, name: str = "", score: float = 0.0, **extra) -> dict:
    """daily_screener asdict(IndicatorResult) 11필드 형태."""
    row = {
        "code": code,
        "name": name or f"종목{code}",
        "close": 10000.0,
        "blue_line": 9800.0,
        "blue_line_status": "above",
        "ma224": 9500.0,
        "ma112": 9700.0,
        "watermelon_signal": True,
        "watermelon_price": None,
        "volume_ratio": 1.8,
        "score": score,
    }
    row.update(extra)
    return row


def _pred_stock(code: str, *, rank: int = 1, total_score: float = 0.0, **extra) -> dict:
    row = {
        "rank": rank,
        "code": code,
        "name": f"종목{code}",
        "total_score": total_score,
        "confidence": 0.72,
        "agent_scores": {"momentum": 0.0, "volume": 0.0,
                         "technical": 0.0, "breakout": 0.0, "timing": 0.0},
        "consensus_level": "만장일치",
        "top_reasons": ["테스트"],
    }
    row.update(extra)
    return row


def _write_scan(dirpath: Path, date_iso: str, stocks: list[dict]) -> Path:
    path = dirpath / f"watchlist_{date_iso}.json"
    path.write_text(json.dumps({
        "date": date_iso,
        "generated_at": f"{date_iso}T08:31:12.123456",  # naive KST
        "count": len(stocks),
        "stocks": stocks,
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _write_pred(dirpath: Path, date_iso: str, stocks: list[dict]) -> Path:
    path = dirpath / f"predictions_{date_iso}.json"
    path.write_text(json.dumps({
        "date": date_iso,
        "generated_at": f"{date_iso}T08:35:00.000000",
        "universe_size": 412,
        "top_n": 50,
        "count": len(stocks),
        "weights": {"momentum": 0.25, "volume": 0.20, "technical": 0.20,
                    "breakout": 0.15, "timing": 0.20},
        "stocks": stocks,
    }, ensure_ascii=False), encoding="utf-8")
    return path


# ─── 테스트 ──────────────────────────────────────────────────────────────────
def test_ok_intersection_sorted_and_ranked(tmp_path, monkeypatch):
    """두 파일 당일 → status=ok, 교집합만, pred_score DESC 정렬 + rank_combined 1부터."""
    _write_scan(tmp_path, TODAY_ISO, [
        _scan_stock("005930", score=70.0),
        _scan_stock("000660", score=60.0),
        _scan_stock("035720", score=90.0),   # 예측에 없음 → 탈락
    ])
    _write_pred(tmp_path, TODAY_ISO, [
        _pred_stock("000660", rank=1, total_score=91.0),
        _pred_stock("005930", rank=2, total_score=87.4),
        _pred_stock("068270", rank=3, total_score=80.0),  # 스캔에 없음 → 탈락
    ])
    monkeypatch.setenv(ENV_DIR, str(tmp_path))

    uni = load_ai_trade_universe(today=TODAY)

    assert uni.status == "ok"
    assert uni.reason == ""
    assert (uni.source_scan_date, uni.source_pred_date) == (TODAY_ISO, TODAY_ISO)
    assert (uni.scan_count, uni.pred_count) == (3, 3)
    assert uni.intersect_count == 2
    assert [i.symbol for i in uni.items] == ["000660", "005930"]
    assert [i.rank_combined for i in uni.items] == [1, 2]
    first = uni.items[0]
    assert first.pred_score == 91.0 and first.pred_rank == 1
    assert first.scan_score == 60.0 and first.blue_line_status == "above"
    assert first.watermelon_signal is True and first.volume_ratio == 1.8
    assert first.confidence == 0.72 and first.consensus_level == "만장일치"
    assert uni.as_of.endswith("+09:00")


def test_tie_on_pred_score_falls_back_to_scan_score(tmp_path, monkeypatch):
    """pred_score 동점 → scan_score DESC 로 순서 결정."""
    _write_scan(tmp_path, TODAY_ISO, [
        _scan_stock("000660", score=10.0),
        _scan_stock("005930", score=80.0),
    ])
    _write_pred(tmp_path, TODAY_ISO, [
        _pred_stock("000660", rank=1, total_score=50.0),
        _pred_stock("005930", rank=2, total_score=50.0),
    ])
    monkeypatch.setenv(ENV_DIR, str(tmp_path))

    uni = load_ai_trade_universe(today=TODAY)

    assert uni.status == "ok"
    assert [i.symbol for i in uni.items] == ["005930", "000660"]


def test_today_scan_ignores_yesterday_prediction(tmp_path, monkeypatch):
    """당일 예측 실패 시 과거 예측을 섞지 않고 partial로 강등한다."""
    _write_scan(tmp_path, TODAY_ISO, [_scan_stock("005930", score=70.0)])
    _write_pred(tmp_path, YESTERDAY_ISO, [_pred_stock("005930", total_score=88.0)])
    monkeypatch.setenv(ENV_DIR, str(tmp_path))

    uni = load_ai_trade_universe(today=TODAY)

    assert uni.status == "partial"
    assert uni.reason == "predictions_missing:fallback_disabled"
    assert uni.source_scan_date == TODAY_ISO
    assert uni.source_pred_date == ""
    assert uni.items == ()


def test_scan_only_uses_today_scan_when_yesterday_prediction_exists(tmp_path, monkeypatch):
    _write_scan(tmp_path, TODAY_ISO, [
        _scan_stock("005930", score=70.0), _scan_stock("000660", score=90.0),
    ])
    _write_pred(tmp_path, YESTERDAY_ISO, [_pred_stock("005930", total_score=99.0)])
    monkeypatch.setenv(ENV_DIR, str(tmp_path))
    monkeypatch.setenv(ENV_FALLBACK, "scan_only")

    uni = load_ai_trade_universe(today=TODAY)

    assert uni.status == "partial"
    assert uni.reason == "predictions_missing:scan_only"
    assert [i.symbol for i in uni.items] == ["000660", "005930"]
    assert all(i.pred_score == 0.0 for i in uni.items)


def test_scan_only_freshness_requires_only_watchlist(tmp_path, monkeypatch):
    scan = _write_scan(tmp_path, TODAY_ISO, [_scan_stock("005930", score=70.0)])
    now = datetime(2026, 7, 30, 3, 0, tzinfo=timezone.utc)
    os.utime(scan, (now.timestamp(), now.timestamp()))
    monkeypatch.setenv(ENV_MAX_AGE_H, "12")

    assert validate_current_sources(
        str(tmp_path), today=TODAY, now=now, require_predictions=False,
    ) == (True, "")
    fresh, reason = validate_current_sources(str(tmp_path), today=TODAY, now=now)
    assert fresh is False
    assert reason == f"source_missing:predictions_{TODAY_ISO}.json"


def test_stale_when_both_sources_are_yesterday(tmp_path, monkeypatch):
    _write_scan(tmp_path, YESTERDAY_ISO, [_scan_stock("005930", score=70.0)])
    _write_pred(tmp_path, YESTERDAY_ISO, [_pred_stock("005930", total_score=88.0)])
    monkeypatch.setenv(ENV_DIR, str(tmp_path))

    uni = load_ai_trade_universe(today=TODAY)

    assert uni.status == "stale"
    assert (uni.source_scan_date, uni.source_pred_date) == (YESTERDAY_ISO, YESTERDAY_ISO)
    assert [i.symbol for i in uni.items] == ["005930"]


def test_partial_scan_only_file_has_empty_items_by_default(tmp_path, monkeypatch):
    """watchlist 만 존재 + 기본 env → status=partial 이지만 items 는 비어 있다."""
    _write_scan(tmp_path, TODAY_ISO, [
        _scan_stock("005930", score=70.0), _scan_stock("000660", score=60.0),
    ])
    monkeypatch.setenv(ENV_DIR, str(tmp_path))

    uni = load_ai_trade_universe(today=TODAY)

    assert uni.status == "partial"
    assert uni.items == ()
    assert uni.intersect_count == 0
    assert uni.scan_count == 2 and uni.pred_count == 0
    assert uni.source_scan_date == TODAY_ISO and uni.source_pred_date == ""
    assert uni.reason == "predictions_missing:fallback_disabled"


def test_partial_with_scan_only_fallback_fills_items(tmp_path, monkeypatch):
    """BARRO_AI_SWING_FALLBACK=scan_only → 스캔 단독 items, pred_* 는 0/""."""
    _write_scan(tmp_path, TODAY_ISO, [
        _scan_stock("005930", score=70.0), _scan_stock("000660", score=95.0),
    ])
    monkeypatch.setenv(ENV_DIR, str(tmp_path))
    monkeypatch.setenv(ENV_FALLBACK, "scan_only")

    uni = load_ai_trade_universe(today=TODAY)

    assert uni.status == "partial"
    assert uni.reason == "predictions_missing:scan_only"
    assert [i.symbol for i in uni.items] == ["000660", "005930"]   # scan_score DESC
    assert [i.rank_combined for i in uni.items] == [1, 2]
    assert all(i.pred_rank == 0 and i.pred_score == 0.0 for i in uni.items)
    assert all(i.confidence == 0.0 and i.consensus_level == "" for i in uni.items)


def test_no_data_when_env_unset(tmp_path):
    """BARRO_AI_TRADE_DIR 미설정 → no_data (기능 OFF)."""
    uni = load_ai_trade_universe(today=TODAY)

    assert uni.status == "no_data"
    assert uni.reason == "ai_trade_dir_unset"
    assert uni.items == ()
    assert uni.to_dict()["intersect_count"] == 0


def test_no_data_when_env_is_blank_string(monkeypatch):
    """빈 문자열도 미설정과 동일 취급."""
    monkeypatch.setenv(ENV_DIR, "   ")

    uni = load_ai_trade_universe(today=TODAY)

    assert (uni.status, uni.reason) == ("no_data", "ai_trade_dir_unset")


def test_no_data_when_directory_missing(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_DIR, str(tmp_path / "없는디렉토리"))

    uni = load_ai_trade_universe(today=TODAY)

    assert (uni.status, uni.reason) == ("no_data", "ai_trade_dir_missing")


def test_no_data_when_directory_has_no_files(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_DIR, str(tmp_path))

    uni = load_ai_trade_universe(today=TODAY)

    assert uni.status == "no_data"
    assert uni.reason == "files_missing"
    assert uni.items == ()


def test_no_data_when_only_predictions_exist(tmp_path, monkeypatch):
    """예측만 있고 스캔이 없으면 교집합 기반이 없다 → 날조 대신 no_data."""
    _write_pred(tmp_path, TODAY_ISO, [_pred_stock("005930", total_score=88.0)])
    monkeypatch.setenv(ENV_DIR, str(tmp_path))

    uni = load_ai_trade_universe(today=TODAY)

    assert (uni.status, uni.reason) == ("no_data", "watchlist_missing")


def test_no_data_on_broken_json(tmp_path, monkeypatch):
    """깨진 JSON → 예외 흡수, reason 이 parse_error 로 시작."""
    (tmp_path / f"watchlist_{TODAY_ISO}.json").write_text("{ this is not json", encoding="utf-8")
    _write_pred(tmp_path, TODAY_ISO, [_pred_stock("005930", total_score=88.0)])
    monkeypatch.setenv(ENV_DIR, str(tmp_path))

    uni = load_ai_trade_universe(today=TODAY)

    assert uni.status == "no_data"
    assert uni.reason.startswith("parse_error")
    assert uni.items == ()


def test_no_data_when_payload_is_not_dict(tmp_path, monkeypatch):
    """최상위가 list 인 스키마 불일치도 parse_error 로 강등."""
    (tmp_path / f"watchlist_{TODAY_ISO}.json").write_text("[1, 2, 3]", encoding="utf-8")
    _write_pred(tmp_path, TODAY_ISO, [_pred_stock("005930", total_score=88.0)])
    monkeypatch.setenv(ENV_DIR, str(tmp_path))

    uni = load_ai_trade_universe(today=TODAY)

    assert uni.status == "no_data"
    assert uni.reason == "parse_error:TypeError"


def test_intersection_empty_keeps_status_with_reason(tmp_path, monkeypatch):
    """교집합 0종목 → status 유지(ok), items 비고 reason=intersection_empty."""
    _write_scan(tmp_path, TODAY_ISO, [_scan_stock("005930", score=70.0)])
    _write_pred(tmp_path, TODAY_ISO, [_pred_stock("068270", total_score=88.0)])
    monkeypatch.setenv(ENV_DIR, str(tmp_path))

    uni = load_ai_trade_universe(today=TODAY)

    assert uni.status == "ok"
    assert uni.items == ()
    assert uni.reason == "intersection_empty"
    assert (uni.scan_count, uni.pred_count) == (1, 1)


def test_symbol_normalization_intersects(tmp_path, monkeypatch):
    """'005930_AL' 과 '005930' 은 같은 종목으로 교집합된다."""
    _write_scan(tmp_path, TODAY_ISO, [_scan_stock("005930_AL", score=70.0)])
    _write_pred(tmp_path, TODAY_ISO, [_pred_stock("005930", total_score=88.0)])
    monkeypatch.setenv(ENV_DIR, str(tmp_path))

    uni = load_ai_trade_universe(today=TODAY)

    assert uni.status == "ok"
    assert [i.symbol for i in uni.items] == ["005930"]


def test_unknown_fields_are_ignored(tmp_path, monkeypatch):
    """계약에 없는 미지 필드가 와도 무시하고 정상 파싱(lenient)."""
    _write_scan(tmp_path, TODAY_ISO, [
        _scan_stock("005930", score=70.0, future_metric=1.23, tags=["a", "b"]),
        "문자열_원소_무시",  # dict 아닌 원소도 흡수
    ])
    _write_pred(tmp_path, TODAY_ISO, [
        _pred_stock("005930", total_score=88.0, new_agent_scores={"x": 1}, note=None),
    ])
    monkeypatch.setenv(ENV_DIR, str(tmp_path))

    uni = load_ai_trade_universe(today=TODAY)

    assert uni.status == "ok"
    assert uni.scan_count == 1
    assert [i.symbol for i in uni.items] == ["005930"]
    assert uni.items[0].scan_score == 70.0 and uni.items[0].pred_score == 88.0


def test_base_dir_argument_overrides_env(tmp_path, monkeypatch):
    """base_dir 인자가 env 보다 우선한다."""
    _write_scan(tmp_path, TODAY_ISO, [_scan_stock("005930", score=70.0)])
    _write_pred(tmp_path, TODAY_ISO, [_pred_stock("005930", total_score=88.0)])
    monkeypatch.setenv(ENV_DIR, str(tmp_path / "없는경로"))

    uni = load_ai_trade_universe(base_dir=str(tmp_path), today=TODAY)

    assert uni.status == "ok"
    assert [i.symbol for i in uni.items] == ["005930"]


def test_freshness_env_does_not_change_status(tmp_path, monkeypatch):
    """MAX_AGE_H·ALLOW_STALE 는 로더 status 판정에 영향 없음(소비자 몫)."""
    _write_scan(tmp_path, YESTERDAY_ISO, [_scan_stock("005930", score=70.0)])
    _write_pred(tmp_path, YESTERDAY_ISO, [_pred_stock("005930", total_score=88.0)])
    monkeypatch.setenv(ENV_DIR, str(tmp_path))
    monkeypatch.setenv(ENV_MAX_AGE_H, "999")
    monkeypatch.setenv(ENV_ALLOW_STALE, "1")

    uni = load_ai_trade_universe(today=TODAY)

    assert uni.status == "stale"   # allow_stale=1 이어도 ok 로 승격하지 않는다


def test_to_dict_contract_keys(tmp_path, monkeypatch):
    """to_dict() 키 계약 + items 원소 키 = AiTradeItem 필드명."""
    _write_scan(tmp_path, TODAY_ISO, [_scan_stock("005930", score=70.0)])
    _write_pred(tmp_path, TODAY_ISO, [_pred_stock("005930", total_score=88.0)])
    monkeypatch.setenv(ENV_DIR, str(tmp_path))

    payload = load_ai_trade_universe(today=TODAY).to_dict()

    assert set(payload) == {
        "as_of", "source_scan_date", "source_pred_date", "status", "reason",
        "scan_count", "pred_count", "intersect_count", "items",
    }
    assert set(payload["items"][0]) == {
        "symbol", "name", "scan_score", "blue_line_status", "watermelon_signal",
        "volume_ratio", "pred_rank", "pred_score", "confidence", "consensus_level",
        "rank_combined",
    }
    # data/ai_swing_universe.json 직렬화 가능해야 한다
    assert json.loads(json.dumps(payload, ensure_ascii=False))["status"] == "ok"
