"""뉴스기반 테마 동적 발굴 스케줄 잡 (default-OFF).

news_theme_discovery.discover_dynamic_themes() 를 주기적으로 실행 — 큐레이션
시드(theme_live_refresh_jobs)와 별개 잡이며, news_collector_jobs 가 채운
news_items 에 의존한다(둘 다 꺼져 있으면 조용히 빈 결과, 에러 아님).

이 잡은 새 테마 그룹을 DB 에 누적 생성하는 실험적 기능이라 다른 표시전용 잡
(theme_board_cache_jobs)과 달리 기본 OFF — 운영에서 결과를 검증한 뒤 켠다.

배선: scripts/finance/telegram_integration/scheduler.py 의 start_scheduler().
운영에서 켜는 법: BARRO_THEME_DISCOVERY_ENABLED=1
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_FLAG_ENV = "BARRO_THEME_DISCOVERY_ENABLED"
_INTERVAL_ENV = "BARRO_THEME_DISCOVERY_INTERVAL_SEC"
_DEFAULT_INTERVAL_SEC = 1800  # 30분 — 뉴스 갱신 대비 과도한 재계산 방지


def _flag_enabled() -> bool:
    return os.environ.get(_FLAG_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


async def _run_theme_discovery_job() -> None:
    """1 사이클 발굴. 예외는 삼켜 로깅만(스케줄러 무영향)."""
    try:
        from backend.core.themes.news_theme_discovery import discover_dynamic_themes

        result = await discover_dynamic_themes()
        logger.info("테마 뉴스발굴 잡 완료: %s", result)
    except Exception:
        logger.warning("테마 뉴스발굴 잡 실패", exc_info=True)


def register_theme_discovery_jobs(scheduler, *, enabled: bool | None = None) -> list[str]:
    """AsyncIOScheduler 에 테마 뉴스발굴 잡을 등록한다(기본 30분 주기).

    Args:
        scheduler: APScheduler AsyncIOScheduler 인스턴스(add_job 제공).
        enabled: 강제 on/off (테스트용). None 이면 BARRO_THEME_DISCOVERY_ENABLED 플래그.

    Returns:
        등록된 잡 id 리스트. 플래그 OFF 면 빈 리스트(잡 미등록).
    """
    if enabled is None:
        enabled = _flag_enabled()
    if not enabled:
        logger.debug("테마 뉴스발굴 잡 비활성 (%s=0) — 등록 생략", _FLAG_ENV)
        return []

    from apscheduler.triggers.interval import IntervalTrigger

    interval = int(os.environ.get(_INTERVAL_ENV, "") or _DEFAULT_INTERVAL_SEC)
    job_id = "theme_discovery"
    scheduler.add_job(
        _run_theme_discovery_job,
        IntervalTrigger(seconds=interval),
        id=job_id,
        name=f"테마 뉴스발굴 ({interval}s)",
        replace_existing=True,
        misfire_grace_time=120,
        max_instances=1,
    )
    logger.info("테마 뉴스발굴 잡 등록 완료: %s (interval=%ds)", job_id, interval)
    return [job_id]


__all__ = ["register_theme_discovery_jobs"]
