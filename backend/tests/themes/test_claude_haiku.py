"""BAR-59 — ClaudeHaikuClassifier lazy stub (2 cases)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import SecretStr

from backend.core.themes.classifier import ClaudeHaikuClassifier
from backend.models.news import NewsItem, NewsSource


def _news() -> NewsItem:
    return NewsItem(
        source=NewsSource.DART,
        source_id="x-1",
        title="t",
        url="https://x",
        published_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc),
    )


class TestClaudeHaiku:
    def test_init_does_not_raise(self):
        # __init__ 정상 (council 합의 — lazy stub)
        cls = ClaudeHaikuClassifier(api_key=SecretStr("test"))
        assert cls.backend_id == "claude_haiku_v1"

    @pytest.mark.asyncio
    async def test_classify_raises_not_implemented(self):
        cls = ClaudeHaikuClassifier()
        with pytest.raises(NotImplementedError, match="BAR-59b"):
            await cls.classify(_news())
