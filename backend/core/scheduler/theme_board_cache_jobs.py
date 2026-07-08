"""테마 보드 화면표시 캐시 백그라운드 갱신 잡 (default-ON — 순수 표시데이터 갱신).

배경: 프론트(테마 화면)가 자체 폴링 타이머로 매번 /api/themes/{id}/stocks 를 호출하면
그 요청이 곧바로 키움 REST 온디맨드 조회(ReadOnlyScanGateway)를 트리거했다 — "데이터
작업"이 프론트 요청에 종속되어 있었다. 이 잡은 그 작업을 백엔드 자체 주기로 분리한다:
    - 기본값(BARRO_THEME_BOARD_CACHE_ENRICH=1)은 라이브 시세까지 캐시에 채운다 —
      GET /api/themes/{id}/stocks 는 이 캐시를 읽기만 하므로 요청 자체는 여전히
      순수 읽기(표시)다. =0 이면 DB/스냅숏 score 만 캐시에 적재(라이브 부하 완전 차단).
    - GET /api/themes/{id}/stocks 는 언제나 순수 읽기(표시)만 수행 — 데이터 신선도는
      이 배경잡의 enrich 설정에 달려 있다.

안전: 읽기 전용 시세 조회만 수행(주문/게이트웨이 쓰기 경로 무관). 잡 실행 중 예외는
잡 래퍼 내부에서 삼켜 로깅만 한다 — 스케줄러/서버/실거래 경로에 전파되지 않는다.
라이브 보강을 켠 경우에는 동시조회 상한(_THEME_QUOTE_SEMAPHORE)·종목별 시세 캐시를
themes_calendar_news / readonly_scan_gateway 에서 적용한다.

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
_ENRICH_ENV = "BARRO_THEME_BOARD_CACHE_ENRICH"
# 실측: 21테마 전체갱신 1사이클=18~28s(동시조회 상한5+429백오프 영향) — 15s는
# 매번 겹쳐 max_instances=1 로 스킵됨(무해하나 로그 노이즈). 20s로 스킵 최소화.
_DEFAULT_INTERVAL_SEC = 20


def _flag_enabled() -> bool:
    """BARRO_THEME_BOARD_CACHE_ENABLED 플래그(기본 ON) 해석."""
    return os.environ.get(_FLAG_ENV, "1").strip().lower() in {"1", "true", "yes", "on"}


def _live_enrich_enabled() -> bool:
    """BARRO_THEME_BOARD_CACHE_ENRICH 플래그(기본 ON) 해석.

    [2026-07-08 회귀 수정] 이 잡은 표시용 캐시의 유일한 데이터 공급원이다 —
    기본 OFF로 두면 GET /api/themes/{id}/stocks 의 기본 경로(enrich 미지정)가
    캐시에서조차 price/value_traded 를 못 받아 항상 null 이 된다(실측 확인,
    docs/03-analysis/2026-07-08-theme-implementation-issues-and-fix-design.md
    §2-A). 세마포어(_THEME_QUOTE_SEMAPHORE)·종목별 20s 캐시가 이미 부하를
    통제하므로 기본 ON 으로 되돌린다. 끄려면 =0.
    """
    return os.environ.get(_ENRICH_ENV, "1").strip().lower() in {"1", "true", "yes", "on"}


async def _refresh_all_themes() -> None:
    """전체 테마의 표시용 캐시를 1회 갱신. 예외는 삼켜 로깅만(스케줄러 무영향)."""
    try:
        from backend.core.scheduler.market_hours import is_open_rush

        if is_open_rush():
            logger.debug("테마보드 캐시 갱신 — 개장 유예 구간, 이번 사이클 보류")
            return

        from backend.api.routes import themes_calendar_news as tcn

        themes = await tcn.fetch_themes()
        if not themes:
            return
        enrich = _live_enrich_enabled()
        results = await asyncio.gather(
            *(tcn.fetch_theme_stocks(t.id, enrich=enrich) for t in themes),
            return_exceptions=True,
        )
        now = _time.monotonic()
        updated = 0
        for theme, stocks in zip(themes, results):
            if isinstance(stocks, BaseException) or stocks is None:
                continue
            tcn._THEME_STOCKS_CACHE[theme.id] = (now, stocks)
            updated += 1
        logger.info("테마보드 캐시 갱신: %d/%d 테마 (enrich=%s)", updated, len(themes), enrich)
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
