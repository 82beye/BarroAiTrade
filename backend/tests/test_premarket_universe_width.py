"""유니버스 폭 확대 — 산출물(JSON)과 텔레그램 메시지의 분리 (2026-09-03).

배경: ai_swing 진입 후보는 `watchlist ∩ predictions` 인데 양쪽이 top20 이라 교집합이
일평균 1.08종목(0 인 날이 다수)까지 떨어져 **진입 기회 자체가 병목**이었다.
실측(2026-09-03): 스캔 top20 × 예측 top20 → 0종목 / 각 top50 → 9종목.

폭을 넓히려면 파일에 실리는 예측 수를 늘려야 하는데 `format_prediction_message` 에는
표시 상한이 없어 그대로 늘리면 텔레그램 메시지가 그 길이만큼 늘어난다. 그래서
**데이터는 넓히고 메시지는 유지**하도록 분리했다 — 이 파일이 그 계약을 고정한다.
"""
from __future__ import annotations

import os

from backend.core.premarket_briefing import format_prediction_message


def _rows(n: int) -> list[dict]:
    return [
        {
            "rank": i,
            "code": f"{i:06d}",
            "name": f"종목{i}",
            "total_score": 90.0 - i,
            "confidence": 1.0,
            "agent_scores": {"momentum": 80.0, "volume": 10.0},
            "consensus_level": "만장일치",
            "top_reasons": ["[momentum] 3일 연속 상승"],
        }
        for i in range(1, n + 1)
    ]


# ── 기존 동작 보존 (20종목 = 지금까지의 호출) ───────────────────────────────

def test_twenty_rows_render_all_and_no_tail():
    """20종목이면 전량 표시되고 '외 N종목' 꼬리가 붙지 않는다 — 기존 출력과 동일."""
    msg = format_prediction_message(_rows(20))
    assert "... 외" not in msg
    assert "예측 종목: 20개" in msg
    for i in (1, 20):
        assert f"[{i:06d}]" in msg


# ── 넓힌 산출물 + 유지된 메시지 ─────────────────────────────────────────────

def test_fifty_rows_message_stays_at_twenty():
    """★ 핵심 계약 — 예측이 50종목이어도 메시지에는 20종목만 싣는다."""
    msg = format_prediction_message(_rows(50))
    assert "[000020]" in msg, "20위까지는 표시"
    assert "[000021]" not in msg, "21위부터는 메시지에서 잘린다"
    assert "... 외 30종목" in msg


def test_header_count_reflects_full_artifact_not_display():
    """헤더의 '예측 종목: N개' 는 **전량** 기준이어야 한다 — 산출물 규모를 숨기지 않는다."""
    assert "예측 종목: 50개" in format_prediction_message(_rows(50))


def test_consensus_counts_use_full_set():
    """합의수준 집계도 전량 기준(표시분만 세면 분포가 왜곡된다)."""
    rows = _rows(50)
    for r in rows[20:]:
        r["consensus_level"] = "다수결"
    msg = format_prediction_message(rows)
    assert "만장일치:20" in msg
    assert "다수결:30" in msg


def test_display_limit_zero_means_no_truncation():
    msg = format_prediction_message(_rows(50), display_limit=0)
    assert "[000050]" in msg
    assert "... 외" not in msg


def test_display_limit_above_row_count_adds_no_tail():
    msg = format_prediction_message(_rows(5), display_limit=20)
    assert "... 외" not in msg


# ── 스캔 유니버스 크기 env 제어 ─────────────────────────────────────────────

def _load_config_with(env_value: str | None):
    from pathlib import Path

    from scripts.premarket_telegram_briefing import _load_config

    key = "BARRO_PREMARKET_MAX_WATCHLIST"
    prev = os.environ.get(key)
    if env_value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = env_value
    try:
        return _load_config(Path("data/ohlcv_cache"))
    finally:
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev


def test_max_watchlist_default_is_settings_yaml():
    """env 미설정이면 settings.yaml 값(20) 그대로 — 기존 동작 보존."""
    assert _load_config_with(None)["scanner"]["max_watchlist"] == 20


def test_max_watchlist_env_override():
    assert _load_config_with("50")["scanner"]["max_watchlist"] == 50


def test_max_watchlist_invalid_env_falls_back():
    """오타·0·음수는 무시하고 기본값을 쓴다 — env 오타로 스캔이 비면 안 된다."""
    for raw in ("", "  ", "abc", "0", "-5"):
        assert _load_config_with(raw)["scanner"]["max_watchlist"] == 20, raw


# ── 브리핑 subprocess 가 .env.local 을 매 실행 다시 읽는다 ──────────────────
# 백엔드는 기동 시 env 를 1회만 소싱한다. 그래서 .env.local 을 고쳐도 브리핑 subprocess 는
# 기동 시점의 stale 값을 물려받았다 — 유니버스 폭 설정이 재기동 전까지 안 먹는 원인이었다.

def test_child_env_prefers_env_local_over_inherited(tmp_path, monkeypatch):
    from backend.core.scheduler import premarket_briefing_jobs as jobs

    env_file = tmp_path / ".env.local"
    env_file.write_text("BARRO_PREMARKET_MAX_WATCHLIST=50\n", encoding="utf-8")
    monkeypatch.setattr(jobs, "_REPO_ROOT", tmp_path)
    monkeypatch.setenv("BARRO_PREMARKET_MAX_WATCHLIST", "20")  # 백엔드 기동 시점의 stale 값

    assert jobs._child_env()["BARRO_PREMARKET_MAX_WATCHLIST"] == "50"


def test_child_env_does_not_mutate_parent(tmp_path, monkeypatch):
    """부모 프로세스(백엔드) 환경을 오염시키면 안 된다 — 자식 env 만 만든다."""
    from backend.core.scheduler import premarket_briefing_jobs as jobs

    (tmp_path / ".env.local").write_text("BARRO_TEST_ONLY_KEY=1\n", encoding="utf-8")
    monkeypatch.setattr(jobs, "_REPO_ROOT", tmp_path)
    monkeypatch.delenv("BARRO_TEST_ONLY_KEY", raising=False)

    assert jobs._child_env()["BARRO_TEST_ONLY_KEY"] == "1"
    assert "BARRO_TEST_ONLY_KEY" not in os.environ


def test_child_env_falls_back_when_file_missing(tmp_path, monkeypatch):
    """.env.local 이 없으면 상속 env 를 그대로 쓴다(= 기존 동작)."""
    from backend.core.scheduler import premarket_briefing_jobs as jobs

    monkeypatch.setattr(jobs, "_REPO_ROOT", tmp_path)   # 파일 없음
    monkeypatch.setenv("BARRO_PREMARKET_TOP_N", "20")

    assert jobs._child_env()["BARRO_PREMARKET_TOP_N"] == "20"


def test_child_env_absorbs_unreadable_file(tmp_path, monkeypatch):
    """읽기 실패해도 예외를 던지지 않는다 — 이 함수 때문에 브리핑이 죽으면 안 된다."""
    from backend.core.scheduler import premarket_briefing_jobs as jobs

    monkeypatch.setattr(jobs, "_REPO_ROOT", tmp_path)
    (tmp_path / ".env.local").mkdir()   # 파일이 아니라 디렉토리 → 읽기 실패 유도
    monkeypatch.setenv("BARRO_PREMARKET_TOP_N", "20")

    assert jobs._child_env()["BARRO_PREMARKET_TOP_N"] == "20"
