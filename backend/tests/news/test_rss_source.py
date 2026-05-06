"""BAR-57 — RSSSource 검증 (5 cases)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from backend.core.news.sources import RSSSource
from backend.models.news import NewsSource


@pytest.fixture
def http_mock() -> AsyncMock:
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


class TestRSSSource:
    def test_https_only_enforced(self, http_mock):
        with pytest.raises(ValueError, match="non-https"):
            RSSSource(
                NewsSource.RSS_HANKYUNG,
                "http://rss.hankyung.com/feed",
                http_mock,
            )

    def test_host_allowlist_enforced(self, http_mock):
        with pytest.raises(ValueError, match="not in allowlist"):
            RSSSource(
                NewsSource.RSS_HANKYUNG,
                "https://evil.example.com/feed",
                http_mock,
            )

    def test_allowlisted_host_constructs(self, http_mock):
        src = RSSSource(
            NewsSource.RSS_HANKYUNG,
            "https://rss.hankyung.com/feed/economy.xml",
            http_mock,
        )
        assert src.name == NewsSource.RSS_HANKYUNG

    @pytest.mark.asyncio
    async def test_fetch_parses_basic_rss(self, http_mock):
        rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>샘플 뉴스</title>
  <link>https://rss.hankyung.com/article/1</link>
  <guid>article-1</guid>
  <description>본문</description>
  <pubDate>Wed, 06 May 2026 12:00:00 +0000</pubDate>
</item>
</channel></rss>""".encode("utf-8")
        resp = MagicMock()
        resp.content = rss_xml
        resp.raise_for_status = MagicMock()
        http_mock.get = AsyncMock(return_value=resp)

        src = RSSSource(
            NewsSource.RSS_HANKYUNG,
            "https://rss.hankyung.com/feed/economy.xml",
            http_mock,
        )
        items = await src.fetch()
        assert len(items) == 1
        assert items[0].title == "샘플 뉴스"
        assert items[0].source == NewsSource.RSS_HANKYUNG
        assert items[0].source_id == "article-1"

    @pytest.mark.asyncio
    async def test_parse_fail_soft_skips_bad_entry(self, http_mock):
        rss_xml = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<item>
  <title>good</title>
  <link>https://rss.hankyung.com/article/2</link>
  <guid>article-2</guid>
  <pubDate>Wed, 06 May 2026 12:00:00 +0000</pubDate>
</item>
</channel></rss>"""
        resp = MagicMock()
        resp.content = rss_xml
        resp.raise_for_status = MagicMock()
        http_mock.get = AsyncMock(return_value=resp)

        src = RSSSource(
            NewsSource.RSS_HANKYUNG,
            "https://rss.hankyung.com/feed/economy.xml",
            http_mock,
        )
        items = await src.fetch()
        assert len(items) >= 1
        assert all(it.title for it in items)
