"""theme_refresher 단위 테스트 + POST /api/themes/refresh 라우트 테스트.

리포지토리(upsert_theme/link_stock)와 시세(cache_quotes.get_quote)를 monkeypatch 로
스파이/스텁 → 실제 DB·게이트웨이 없이 계약(호출 인자·스코어·에러 격리)을 검증한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core.market_data import cache_quotes
from backend.core.themes import theme_refresher
from backend.db.repositories.theme_repo import ThemeRepository


def _write_seed(path: Path, mapping: dict) -> None:
    path.write_text(
        json.dumps({"version": "1.0", "map": mapping}, ensure_ascii=False),
        encoding="utf-8",
    )


@pytest.fixture
def spy_repo(monkeypatch):
    """ThemeRepository.upsert_theme/link_stock 를 스파이로 대체(실 DB 미사용)."""
    calls: dict = {"upsert": [], "link": []}
    _theme_ids: dict = {}
    _counter = {"n": 0}

    async def fake_upsert(self, name, description=""):
        calls["upsert"].append({"name": name, "description": description})
        if name not in _theme_ids:
            _counter["n"] += 1
            _theme_ids[name] = _counter["n"]
        return _theme_ids[name]

    async def fake_link(self, theme_id, symbol, score):
        calls["link"].append({"theme_id": theme_id, "symbol": symbol, "score": score})
        return True

    monkeypatch.setattr(ThemeRepository, "upsert_theme", fake_upsert)
    monkeypatch.setattr(ThemeRepository, "link_stock", fake_link)
    return calls


# ── 1. 시드 없음 → no_seed (예외 없음) ────────────────────────────────────────


async def test_missing_seed_returns_no_seed(tmp_path, spy_repo):
    missing = tmp_path / "does_not_exist.json"
    result = await theme_refresher.refresh_themes_from_seed(missing)

    assert result["status"] == "no_seed"
    assert result["theme_count"] == 0
    assert result["symbol_count"] == 0
    assert "refreshed_at" in result and result["refreshed_at"]
    # 시드 없음 → 리포지토리 무접촉
    assert spy_repo["upsert"] == []
    assert spy_repo["link"] == []


async def test_empty_map_returns_no_seed(tmp_path, spy_repo):
    seed = tmp_path / "theme_map.json"
    _write_seed(seed, {})  # 빈 매핑
    result = await theme_refresher.refresh_themes_from_seed(seed)
    assert result["status"] == "no_seed"
    assert result["theme_count"] == 0
    assert result["symbol_count"] == 0


# ── 2. 정상 시드 → upsert_theme/link_stock 올바른 인자로 호출 ─────────────────


async def test_normal_seed_calls_upsert_and_link(tmp_path, spy_repo, monkeypatch):
    seed = tmp_path / "theme_map.json"
    _write_seed(seed, {"005930": ["반도체"], "000660": ["반도체", "HBM"]})
    # 등락률 2.5% → score 2.5 (실제 관측값을 그대로 스코어로)
    monkeypatch.setattr(
        cache_quotes, "get_quote", lambda sym, base_dir=None: {"change_pct": 2.5}
    )

    result = await theme_refresher.refresh_themes_from_seed(seed)

    assert result["status"] == "ok"
    # 테마 2개(반도체, HBM), 고유 종목 2개(005930, 000660)
    assert result["theme_count"] == 2
    assert result["symbol_count"] == 2

    # upsert_theme: 테마명 + description 규약
    upsert_names = {c["name"] for c in spy_repo["upsert"]}
    assert upsert_names == {"반도체", "HBM"}
    for c in spy_repo["upsert"]:
        assert c["description"] == f"{c['name']} 테마 (큐레이션 시드 기반)"

    # link_stock: 스코어 = 등락률
    assert spy_repo["link"], "링크가 하나도 없음"
    for c in spy_repo["link"]:
        assert c["score"] == 2.5

    # 000660 은 반도체 + HBM 두 테마에 각각 링크(다대다)
    links_660 = [c for c in spy_repo["link"] if c["symbol"] == "000660"]
    assert len(links_660) == 2


# ── 3. get_quote 실패/None → score 0.0 (날조 금지, 링크는 유지) ────────────────


async def test_quote_none_links_with_score_zero(tmp_path, spy_repo, monkeypatch):
    seed = tmp_path / "theme_map.json"
    _write_seed(seed, {"005930": ["반도체"]})
    monkeypatch.setattr(cache_quotes, "get_quote", lambda sym, base_dir=None: None)

    result = await theme_refresher.refresh_themes_from_seed(seed)

    assert result["status"] == "ok"
    assert len(spy_repo["link"]) == 1
    # 시세를 못 구해도 링크는 하되 스코어는 정직하게 0.0
    assert spy_repo["link"][0]["symbol"] == "005930"
    assert spy_repo["link"][0]["score"] == 0.0


# ── 4. 한 종목 예외 → 나머지는 계속 처리 ──────────────────────────────────────


async def test_one_symbol_error_others_continue(tmp_path, spy_repo, monkeypatch):
    seed = tmp_path / "theme_map.json"
    _write_seed(seed, {"005930": ["반도체"], "000660": ["반도체"]})

    def flaky_quote(sym, base_dir=None):
        if sym == "005930":
            raise RuntimeError("시세 조회 폭발")
        return {"change_pct": 1.0}

    monkeypatch.setattr(cache_quotes, "get_quote", flaky_quote)

    result = await theme_refresher.refresh_themes_from_seed(seed)

    # 한 종목 실패해도 전체가 막히지 않고 status="ok"
    assert result["status"] == "ok"
    linked = {c["symbol"] for c in spy_repo["link"]}
    assert "000660" in linked  # 정상 종목은 계속 링크됨
    # 폭발한 005930 은 링크되지 않음(스킵)
    assert "005930" not in linked
    assert result["symbol_count"] == 1


# ── 5. POST /api/themes/refresh 라우트 (refresh_themes_from_seed 스텁) ─────────


def test_refresh_route_returns_result(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes.themes_calendar_news import router

    async def fake_refresh(seed_path=None):
        return {
            "theme_count": 3,
            "symbol_count": 5,
            "status": "ok",
            "refreshed_at": "2026-07-06T00:00:00+00:00",
        }

    # 라우트 핸들러는 함수 내부에서 theme_refresher 로부터 지연 import → 모듈 속성 교체.
    monkeypatch.setattr(theme_refresher, "refresh_themes_from_seed", fake_refresh)

    app = FastAPI()
    app.include_router(router)  # 라우트 경로에 이미 /api/ 포함 → prefix 없음
    client = TestClient(app)

    resp = client.post("/api/themes/refresh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["theme_count"] == 3
    assert body["symbol_count"] == 5
