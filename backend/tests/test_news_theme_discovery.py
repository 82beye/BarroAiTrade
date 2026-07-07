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


class TestClassifyThemeGroups:
    def test_rejects_boilerplate_keywords(self):
        for keyword in ["펀드", "연합뉴스", "서울", "김태종", "돌파"]:
            theme_name, reason = ntd.classify_theme_keyword(keyword)
            assert theme_name is None
            assert reason in {"stopword", "unclassified"}

    def test_maps_aliases_to_canonical_theme(self):
        assert ntd.classify_theme_keyword("파운드리") == ("반도체", "taxonomy")
        assert ntd.classify_theme_keyword("배터리") == ("2차전지", "taxonomy")
        assert ntd.classify_theme_keyword("SMR") == ("원전", "taxonomy")

    def test_classified_groups_merge_aliases_and_reject_noise(self):
        groups = {
            "반도체": [("005930", 0.4)],
            "파운드리": [("005930", 0.7), ("000660", 0.3)],
            "펀드": [("005930", 0.9), ("000660", 0.8)],
        }
        classified, keywords, rejected = ntd.classify_theme_groups(groups)

        assert set(classified) == {"반도체"}
        assert classified["반도체"] == [("005930", 0.7), ("000660", 0.3)]
        assert keywords["반도체"] == ["반도체", "파운드리"]
        assert rejected == {"펀드": "stopword"}

    @pytest.mark.asyncio
    async def test_rules_analyst_accepts_dynamic_theme_keyword(self):
        groups = {
            "초전도체": [("111111", 0.6), ("222222", 0.5)],
            "연합뉴스": [("111111", 0.9), ("222222", 0.8)],
        }
        classified, keywords, rejected, decisions, backend = (
            await ntd.classify_theme_groups_with_analyst(
                groups,
                candidates=[
                    {"symbol": "111111", "name": "A테크"},
                    {"symbol": "222222", "name": "B소재"},
                ],
                symbol_articles={},
                analyst_backend="rules",
            )
        )

        assert backend == "rules"
        assert classified["초전도체"] == [("111111", 0.6), ("222222", 0.5)]
        assert keywords["초전도체"] == ["초전도체"]
        assert rejected == {"연합뉴스": "stopword"}
        assert any(d["action"] == "accept" and d["theme"] == "초전도체" for d in decisions)

    @pytest.mark.asyncio
    async def test_claude_analyst_groups_keywords_into_new_theme(self):
        groups = {
            "변압기": [("010120", 0.7), ("006340", 0.4)],
            "전력망": [("010120", 0.5), ("006340", 0.6)],
            "펀드": [("010120", 0.9), ("006340", 0.8)],
        }

        def fake_llm(prompt):
            assert "한국 주식 장중 테마" in prompt
            return {
                "themes": [
                    {
                        "theme": "전력망 증설",
                        "keywords": ["변압기", "전력망"],
                        "symbols": ["010120", "006340"],
                        "confidence": 0.86,
                        "reason": "전력 인프라 뉴스와 거래대금이 함께 붙음",
                    }
                ],
                "rejected_keywords": [{"keyword": "펀드", "reason": "금융 일반어"}],
            }

        classified, keywords, rejected, decisions, backend = (
            await ntd.classify_theme_groups_with_analyst(
                groups,
                candidates=[
                    {"symbol": "010120", "name": "LS ELECTRIC"},
                    {"symbol": "006340", "name": "대원전선"},
                ],
                symbol_articles={
                    "010120": ["LS ELECTRIC 변압기 수출 확대"],
                    "006340": ["대원전선 전력망 투자 기대"],
                },
                analyst_backend="claude-cli",
                llm_fn=fake_llm,
            )
        )

        assert backend == "claude-cli"
        assert set(classified) == {"전력망 증설"}
        assert classified["전력망 증설"] == [("010120", 0.7), ("006340", 0.6)]
        assert keywords["전력망 증설"] == ["변압기", "전력망"]
        assert rejected == {"펀드": "금융 일반어"}
        assert decisions[0]["action"] == "accept"


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
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS theme_stocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    theme_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    score REAL NOT NULL,
                    UNIQUE(theme_id, symbol)
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


class TestFilterUnthemedSymbols:
    @pytest.mark.asyncio
    async def test_already_themed_symbol_excluded(self, news_db):
        async with get_db() as db:
            await db.execute(
                text("INSERT INTO theme_stocks (theme_id, symbol, score) VALUES (1, '005930', 5.0)")
            )
        candidates = [
            {"symbol": "005930", "name": "삼성전자"},   # 이미 테마 있음 — 제외
            {"symbol": "999999", "name": "무명회사"},   # 테마 없음 — 유지
        ]
        result = await ntd.filter_unthemed_symbols(candidates)
        assert [c["symbol"] for c in result] == ["999999"]

    @pytest.mark.asyncio
    async def test_no_themed_symbols_keeps_all(self, news_db):
        candidates = [{"symbol": "005930", "name": "삼성전자"}]
        result = await ntd.filter_unthemed_symbols(candidates)
        assert result == candidates

    @pytest.mark.asyncio
    async def test_empty_candidates_returns_empty(self, news_db):
        assert await ntd.filter_unthemed_symbols([]) == []


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
    async def test_candidate_discovery_can_include_already_themed_symbols(
        self, monkeypatch, news_db
    ):
        async with get_db() as db:
            await db.execute(
                text("INSERT INTO theme_stocks (theme_id, symbol, score) VALUES (1, '005930', 5.0)")
            )
        quotes = _FakeQuotes(
            value_rows=[
                {"symbol": "005930", "name": "삼성전자", "value_traded": 1000.0},
                {"symbol": "999999", "name": "무명회사", "value_traded": 900.0},
            ]
        )
        seen: list[list[str]] = []

        async def fake_match(candidates, *, lookback_days=7):
            seen.append([c["symbol"] for c in candidates])
            return {}

        monkeypatch.setattr(ntd, "match_articles_to_symbols", fake_match)

        await ntd.discover_dynamic_theme_candidates(
            quotes=quotes, exclude_already_themed=True, analyst_backend="rules"
        )
        await ntd.discover_dynamic_theme_candidates(
            quotes=quotes, exclude_already_themed=False, analyst_backend="rules"
        )

        assert seen[0] == ["999999"]
        assert seen[1] == ["005930", "999999"]

    @pytest.mark.asyncio
    async def test_full_pipeline_writes_via_repo(self, monkeypatch, news_db):
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

        result = await ntd.discover_dynamic_themes(
            quotes=quotes, min_symbols_per_theme=2, analyst_backend="rules"
        )

        assert result["status"] == "ok"
        assert result["candidates"] == 2
        assert result["symbols_with_news"] == 2
        assert result["themes_created"] >= 1
        assert any(name == "반도체" for name, _ in created_themes)
        assert all(desc == "뉴스기반 자동발굴" for _, desc in created_themes)
        assert len(linked) >= 2  # 반도체 테마에 최소 2종목 링크

    @pytest.mark.asyncio
    async def test_persist_skips_single_symbol_groups(self, monkeypatch):
        created_themes: list[str] = []
        linked: list[str] = []

        async def fake_upsert(self, name, description=""):
            created_themes.append(name)
            return len(created_themes)

        async def fake_add_keyword(self, theme_id, keyword):
            return True

        async def fake_link(self, theme_id, symbol, score):
            linked.append(symbol)
            return True

        monkeypatch.setattr(ThemeRepository, "upsert_theme", fake_upsert)
        monkeypatch.setattr(ThemeRepository, "add_keyword", fake_add_keyword)
        monkeypatch.setattr(ThemeRepository, "link_stock", fake_link)

        result = await ntd.persist_theme_groups(
            {
                "개별급등": [{"symbol": "111111", "score": 0.9}],
                "반도체": [
                    {"symbol": "005930", "score": 0.8},
                    {"symbol": "000660", "score": 0.7},
                ],
            },
            theme_keywords={"개별급등": ["개별급등"], "반도체": ["반도체"]},
        )

        assert result["themes_created"] == 1
        assert result["individual_groups"] == 1
        assert created_themes == ["반도체"]
        assert linked == ["005930", "000660"]
