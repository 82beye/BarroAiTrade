"""뉴스/공시 수집기(NewsCollector) 가동 스케줄 잡 (default-OFF).

RSS(한경·매경·연합·이데일리) + DART(공시, dart_api_key 설정 시만) 를 주기적으로
수집해 news_items 에 적재한다(BAR-57 NewsCollector 그대로 재사용, 이 잡은 그
기동/배선만 담당). 테마 뉴스기반 발굴 파이프라인(news_theme_discovery)의
데이터 소스 — 이 잡이 꺼져 있으면 news_items 가 채워지지 않아 발굴 결과가 빈다.

안전: 읽기 전용 수집(주문/게이트웨이 무관). source 격리 fetch — 한 피드 실패가
다른 피드/서버를 막지 않는다(NewsCollector.tick 자체 보장).

배선: scripts/finance/telegram_integration/scheduler.py 의 start_scheduler().
운영에서 켜는 법: BARRO_NEWS_COLLECTOR_ENABLED=1 (기본 OFF — 외부 RSS 호스트에
상시 아웃바운드 요청을 내므로 명시적 옵트인).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_FLAG_ENV = "BARRO_NEWS_COLLECTOR_ENABLED"

# 실측 확인(2026-07-06, curl 200 + RSS 파싱 가능) 피드 URL. RSSSource.HOST_ALLOWLIST
# 와 호스트가 일치해야 한다(sources.py 참조).
_RSS_FEEDS: tuple[tuple[str, str], ...] = (
    ("RSS_HANKYUNG", "https://www.hankyung.com/feed/economy"),
    ("RSS_MAEKYUNG", "https://www.mk.co.kr/rss/40300001/"),
    ("RSS_YONHAP", "https://www.yna.co.kr/rss/economy.xml"),
    ("RSS_EDAILY", "https://rss.edaily.co.kr/stock_news.xml"),
)

_collector = None  # 프로세스 lazy 싱글턴 — start_scheduler 재호출 대비


def _flag_enabled() -> bool:
    return os.environ.get(_FLAG_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


def _build_collector():
    """NewsCollector 인스턴스 조립(RSS 4종 + DART 선택적). 실패 시 None."""
    import httpx
    from pydantic import SecretStr

    from backend.config.settings import get_settings
    from backend.core.news.collector import NewsCollector
    from backend.core.news.dedup import InMemoryDeduplicator
    from backend.core.news.publisher import InMemoryStreamPublisher
    from backend.core.news.sources import DARTSource, RSSSource
    from backend.db.repositories.news_repo import news_repo
    from backend.models.news import NewsSource

    settings = get_settings()
    http_client = httpx.AsyncClient(timeout=settings.news_fetch_timeout_seconds)

    sources = []
    for name_str, url in _RSS_FEEDS:
        try:
            sources.append(RSSSource(NewsSource[name_str], url, http_client))
        except Exception as exc:
            logger.warning("RSSSource 구성 실패 %s: %s", name_str, exc)

    if settings.dart_api_key is not None:
        try:
            sources.append(DARTSource(SecretStr(settings.dart_api_key.get_secret_value()), http_client))
        except Exception as exc:
            logger.warning("DARTSource 구성 실패(dart_api_key 설정됨에도): %s", exc)
    else:
        logger.info("dart_api_key 미설정 — DART 공시 source 제외(RSS 4종만 가동)")

    return NewsCollector(
        sources=sources,
        repo=news_repo,
        publisher=InMemoryStreamPublisher(maxsize=settings.news_inmemory_queue_max),
        dedup=InMemoryDeduplicator(ttl_hours=settings.news_dedup_ttl_hours),
        http_client=http_client,
        fetch_timeout=settings.news_fetch_timeout_seconds,
    )


async def _run_news_collector_tick() -> None:
    """1 사이클 수집. 예외는 삼켜 로깅만(스케줄러 무영향)."""
    global _collector
    try:
        if _collector is None:
            _collector = _build_collector()
        await _collector.tick()
        logger.info(
            "뉴스 수집 사이클 완료: published=%d errors=%d",
            _collector.published,
            _collector.errors,
        )
    except Exception:
        logger.warning("뉴스 수집 잡 실패", exc_info=True)


def register_news_collector_jobs(scheduler, *, enabled: bool | None = None) -> list[str]:
    """AsyncIOScheduler 에 뉴스 수집 잡을 등록한다(기본 60s, news_polling_interval_sec).

    Args:
        scheduler: APScheduler AsyncIOScheduler 인스턴스(add_job 제공).
        enabled: 강제 on/off (테스트용). None 이면 BARRO_NEWS_COLLECTOR_ENABLED 플래그.

    Returns:
        등록된 잡 id 리스트. 플래그 OFF 면 빈 리스트(잡 미등록).
    """
    if enabled is None:
        enabled = _flag_enabled()
    if not enabled:
        logger.debug("뉴스 수집 잡 비활성 (%s=0) — 등록 생략", _FLAG_ENV)
        return []

    from apscheduler.triggers.interval import IntervalTrigger

    from backend.config.settings import get_settings

    interval = get_settings().news_polling_interval_sec
    job_id = "news_collector_tick"
    scheduler.add_job(
        _run_news_collector_tick,
        IntervalTrigger(seconds=interval),
        id=job_id,
        name=f"뉴스/공시 수집 ({interval}s)",
        replace_existing=True,
        misfire_grace_time=60,
        max_instances=1,
    )
    logger.info("뉴스 수집 잡 등록 완료: %s (interval=%ds)", job_id, interval)
    return [job_id]


__all__ = ["register_news_collector_jobs"]
