"""테마 보드 화면표시 캐시 백그라운드 갱신 잡 (default-ON — 순수 표시데이터 갱신).

배경: 프론트(테마 화면)가 자체 폴링 타이머로 매번 /api/themes/{id}/stocks 를 호출하면
그 요청이 곧바로 키움 REST 온디맨드 조회(ReadOnlyScanGateway)를 트리거했다 — "데이터
작업"이 프론트 요청에 종속되어 있었다. 이 잡은 그 작업을 백엔드 자체 주기로 분리한다:
    - 데이터 갱신(라이브 시세 조회)은 이 잡이 전담, 백엔드 스케줄에 따라만 실행.
    - GET /api/themes/{id}/stocks 는 캐시 히트 시 순수 읽기(표시)만 수행 — 어떤
      요청도 라이브 조회를 트리거하지 않는다(캐시 미스 시에만 1회 인라인 폴백,
      themes_calendar_news.get_theme_stocks 참조).

안전: 읽기 전용 시세 조회만 수행(주문/게이트웨이 쓰기 경로 무관). 잡 실행 중 예외는
잡 래퍼 내부에서 삼켜 로깅만 한다 — 스케줄러/서버/실거래 경로에 전파되지 않는다.
동시조회 상한(_THEME_QUOTE_SEMAPHORE)·종목별 시세 캐시는 themes_calendar_news /
readonly_scan_gateway 에 이미 있어 이 잡의 빈도가 높아도 mockapi 과부하로 이어지지 않는다.

배선: scripts/finance/telegram_integration/scheduler.py 의 start_scheduler().
운영에서 끄는 법: BARRO_THEME_BOARD_CACHE_ENABLED=0 (다른 테마 잡들과 달리 이 잡은
화면표시의 직접 데이터 소스라 기본 ON — 끄면 GET 요청마다 다시 인라인 폴백 계산).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time as _time

logger = logging.getLogger(__name__)

_FLAG_ENV = "BARRO_THEME_BOARD_CACHE_ENABLED"
_INTERVAL_ENV = "BARRO_THEME_BOARD_CACHE_INTERVAL_SEC"
_DEFAULT_INTERVAL_SEC = 15


def _flag_enabled() -> bool:
    """BARRO_THEME_BOARD_CACHE_ENABLED 플래그(기본 ON) 해석."""
    return os.environ.get(_FLAG_ENV, "1").strip().lower() in {"1", "true", "yes", "on"}


async def _refresh_all_themes() -> None:
    """전체 테마의 표시용 캐시를 1회 갱신. 예외는 삼켜 로깅만(스케줄러 무영향)."""
    try:
        from backend.api.routes import themes_calendar_news as tcn

        themes = await tcn.fetch_themes()
        if not themes:
            return
        results = await asyncio.gather(
            *(tcn.fetch_theme_stocks(t.id, enrich=True) for t in themes),
            return_exceptions=True,
        )
        now = _time.monotonic()
        updated = 0
        for theme, stocks in zip(themes, results):
            if isinstance(stocks, BaseException) or stocks is None:
                continue
            tcn._THEME_STOCKS_CACHE[theme.id] = (now, stocks)
            updated += 1
        logger.info("테마보드 캐시 갱신: %d/%d 테마", updated, len(themes))
    except Exception:
        logger.warning("테마보드 캐시 갱신 잡 실패", exc_info=True)


def register_theme_board_cache_jobs(scheduler, *, enabled: bool | None = None) -> list[str]:
    """AsyncIOScheduler 에 짧은 주기(기본 15s) 테마보드 캐시 갱신 잡을 등록한다.

    Args:
        scheduler: APScheduler AsyncIOScheduler 인스턴스(add_job 제공).
        enabled: 강제 on/off (테스트용). None 이면 BARRO_THEME_BOARD_CACHE_ENABLED 플래그.

    Returns:
        등록된 잡 id 리스트. 플래그 OFF 면 빈 리스트(잡 미등록).
    """
    if enabled is None:
        enabled = _flag_enabled()
    if not enabled:
        logger.debug("테마보드 캐시 갱신 잡 비활성 (%s=0) — 등록 생략", _FLAG_ENV)
        return []

    from apscheduler.triggers.interval import IntervalTrigger

    interval = int(os.environ.get(_INTERVAL_ENV, "") or _DEFAULT_INTERVAL_SEC)
    job_id = "theme_board_cache_refresh"
    scheduler.add_job(
        _refresh_all_themes,
        IntervalTrigger(seconds=interval),
        id=job_id,
        name=f"테마보드 캐시 갱신 ({interval}s)",
        replace_existing=True,
        misfire_grace_time=30,
        max_instances=1,  # 이전 사이클 미종료 시 중첩 실행 방지
    )
    logger.info("테마보드 캐시 갱신 잡 등록 완료: %s (interval=%ds)", job_id, interval)
    return [job_id]


__all__ = ["register_theme_board_cache_jobs"]
