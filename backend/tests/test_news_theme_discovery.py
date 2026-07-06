"""뉴스기반 테마 동적발굴 파이프라인 테스트.

candidate universe(랭킹 mock) · symbol 매칭(실 SQLite news_items) · 키워드추출
(순수 함수) · 오케스트레이션(repo monkeypatch) 을 각각 독립 검증.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from backend.core.themes import news_theme_discovery as ntd
from backend.db.database import get_db, init_db, reset_engine_for_test
from backend.db.repositories.theme_repo import ThemeRepository


class _FakeQuotes:
    def __init__(self, value_rows=None, gainer_rows=None):
        self._value_rows = value_rows if value_rows is not None else []
        self._gainer_rows = gainer_rows if gainer_rows is not None else []

    async def ranking(self, filter="value", stex_tp="3", mrkt_tp="000", limit=30):
        rows = self._value_rows if filter == "value" else self._gainer_rows
        return rows[:limit]


class TestBuildCandidateUniverse:
    @pytest.mark.asyncio
    async def test_union_dedup_and_filter(self):
        quotes = _FakeQuotes(
            value_rows=[
                {"symbol": "005930", "name": "삼성전자", "value_traded": 5000.0},
                {"symbol": "000660", "name": "SK하이닉스", "value_traded": 50.0},  # 임계 미달
            ],
            gainer_rows=[
                {"symbol": "005930", "name": "삼성전자(중복)", "value_traded": 1.0},  # dedup, 첫값 유지
                {"symbol": "042700", "name": "한미반도체", "value_traded": 200.0},
            ],
        )
        result = await ntd.build_candidate_universe(
            quotes, top_n=100, min_value_traded_eok=100.0
        )
        symbols = {r["symbol"] for r in result}
        assert symbols == {"005930", "042700"}
        # 첫 등장(거래대금 리스트) 값이 유지되어야 함(dedup 시 override 안 함)
        assert next(r for r in result if r["symbol"] == "005930")["name"] == "삼성전자"

    @pytest.mark.asyncio
    async def test_empty_rankings_returns_empty(self):
        quotes = _FakeQuotes()
        result = await ntd.build_candidate_universe(quotes)
        assert result == []


class TestExtractThemeGroups:
    def test_shared_keyword_forms_theme(self):
        symbol_articles = {
            "005930": ["삼성전자 반도체 파운드리 투자 확대 반도체 수출"] * 3,
            "000660": ["SK하이닉스 반도체 HBM 생산 반도체 호황"] * 3,
            "051910": ["LG화학 배터리 소재 신규 공장 배터리 투자"] * 3,
        }
        groups = ntd.extract_theme_groups(
            symbol_articles, keywords_per_symbol=5, min_symbols_per_theme=2
        )
        assert "반도체" in groups
        syms = {s for s, _ in groups["반도체"]}
        assert syms == {"005930", "000660"}
        # 배터리는 한 종목에만 등장 — 승격되지 않음
        assert "배터리" not in groups

    def test_single_symbol_never_forms_theme(self):
        groups = ntd.extract_theme_groups(
            {"005930": ["단독 종목 기사 텍스트"]}, min_symbols_per_theme=2
        )
        assert groups == {}

    def test_empty_input_returns_empty(self):
        assert ntd.extract_theme_groups({}) == {}

    def test_below_min_symbols_threshold(self):
        symbol_articles = {
            "005930": ["반도체 관련 기사"],
            "000660": ["전혀 다른 주제의 기사"],
        }
        groups = ntd.extract_theme_groups(symbol_articles, min_symbols_per_theme=3)
        assert groups == {}


@pytest.fixture
async def news_db(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    db_file = tmp_path / "news_theme_test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    reset_engine_for_test()
    await init_db(str(db_file))
    async with get_db() as db:
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS news_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(source, source_id)
                )
                """
            )
        )
    yield db_file
    reset_engine_for_test()


async def _insert_news(title: str, body: str, published_at: datetime, source_id: str):
    async with get_db() as db:
        await db.execute(
            text(
                "INSERT INTO news_items (source, source_id, title, body, url, "
                "published_at, fetched_at, tags) VALUES "
                "('rss_hankyung', :sid, :title, :body, 'https://x', :pub, :pub, '[]')"
            ),
            {"sid": source_id, "title": title, "body": body, "pub": published_at.isoformat()},
        )


class TestMatchArticlesToSymbols:
    @pytest.mark.asyncio
    async def test_matches_by_company_name_in_title_or_body(self, news_db):
        now = datetime.now(timezone.utc)
        await _insert_news("삼성전자 반도체 투자 확대", "", now, "n1")
        await _insert_news("업계 동향", "SK하이닉스 HBM 생산 호황", now, "n2")
        await _insert_news("전혀 무관한 기사", "아무 내용", now, "n3")

        candidates = [
            {"symbol": "005930", "name": "삼성전자"},
            {"symbol": "000660", "name": "SK하이닉스"},
            {"symbol": "999999", "name": "언급없는회사"},
        ]
        result = await ntd.match_articles_to_symbols(candidates, lookback_days=7)

        assert "005930" in result and len(result["005930"]) == 1
        assert "000660" in result and len(result["000660"]) == 1
        assert "999999" not in result  # 뉴스 없음 — 날조 금지, 키 자체가 없어야 함

    @pytest.mark.asyncio
    async def test_old_articles_excluded_by_lookback(self, news_db):
        old = datetime.now(timezone.utc) - timedelta(days=30)
        await _insert_news("삼성전자 오래된 기사", "", old, "old1")

        result = await ntd.match_articles_to_symbols(
            [{"symbol": "005930", "name": "삼성전자"}], lookback_days=7
        )
        assert "005930" not in result

    @pytest.mark.asyncio
    async def test_empty_candidates_returns_empty(self, news_db):
        assert await ntd.match_articles_to_symbols([]) == {}


class TestDiscoverDynamicThemes:
    @pytest.mark.asyncio
    async def test_no_key_returns_no_key_status(self, monkeypatch):
        monkeypatch.setattr(ntd, "_get_quotes", lambda: None)
        result = await ntd.discover_dynamic_themes()
        assert result["status"] == "no_key"
        assert result["themes_created"] == 0

    @pytest.mark.asyncio
    async def test_full_pipeline_writes_via_repo(self, monkeypatch):
        quotes = _FakeQuotes(
            value_rows=[
                {"symbol": "005930", "name": "삼성전자", "value_traded": 1000.0},
                {"symbol": "000660", "name": "SK하이닉스", "value_traded": 900.0},
            ]
        )

        async def fake_match(candidates, *, lookback_days=7):
            return {
                "005930": ["삼성전자 반도체 투자 반도체 확대"] * 3,
                "000660": ["SK하이닉스 반도체 생산 반도체 호황"] * 3,
            }

        monkeypatch.setattr(ntd, "match_articles_to_symbols", fake_match)

        created_themes: list[tuple[str, str]] = []
        linked: list[tuple[int, str, float]] = []
        keyworded: list[tuple[int, str]] = []

        async def fake_upsert(self, name, description=""):
            created_themes.append((name, description))
            return len(created_themes)

        async def fake_add_keyword(self, theme_id, keyword):
            keyworded.append((theme_id, keyword))
            return True

        async def fake_link(self, theme_id, symbol, score):
            linked.append((theme_id, symbol, score))
            return True

        monkeypatch.setattr(ThemeRepository, "upsert_theme", fake_upsert)
        monkeypatch.setattr(ThemeRepository, "add_keyword", fake_add_keyword)
        monkeypatch.setattr(ThemeRepository, "link_stock", fake_link)

        result = await ntd.discover_dynamic_themes(quotes=quotes, min_symbols_per_theme=2)

        assert result["status"] == "ok"
        assert result["candidates"] == 2
        assert result["symbols_with_news"] == 2
        assert result["themes_created"] >= 1
        assert any(name == "반도체" for name, _ in created_themes)
        assert all(desc == "뉴스기반 자동발굴" for _, desc in created_themes)
        assert len(linked) >= 2  # 반도체 테마에 최소 2종목 링크
