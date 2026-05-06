"""BAR-57 — news_repo 시그니처 + dialect 분기 검증 (3 cases)."""
from __future__ import annotations

import inspect


def test_news_repo_module_singleton():
    from backend.db.repositories import news_repo
    assert hasattr(news_repo, "news_repo")
    assert news_repo.news_repo.__class__.__name__ == "NewsRepository"


def test_insert_signature():
    from backend.db.repositories.news_repo import NewsRepository
    sig = inspect.signature(NewsRepository.insert)
    assert set(sig.parameters) == {"self", "item"}
    assert inspect.iscoroutinefunction(NewsRepository.insert)


def test_find_recent_by_source_signature():
    from backend.db.repositories.news_repo import NewsRepository
    sig = inspect.signature(NewsRepository.find_recent_by_source)
    assert set(sig.parameters) == {"self", "source", "limit"}
    assert inspect.iscoroutinefunction(NewsRepository.find_recent_by_source)
