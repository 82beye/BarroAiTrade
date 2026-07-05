"""테마 보드 라이브 갱신 스케줄 잡 (default-OFF).

장중(09~15시 KST) 5분마다 큐레이션 시드(theme_map.json) 기반 테마 보드를 캐시/거래소
시세로 재갱신한다(`backend.core.themes.theme_refresher.refresh_themes_from_seed`).
테마 그룹 자체는 고정(시드), 종목별 스코어(등락률)만 시세로 갱신 — 뉴스 실시간 재분류가
아님(정직성, theme_refresher 모듈 docstring 참조).

배선 지점:
    scripts/finance/telegram_integration/scheduler.py 의 start_scheduler() 가
    AsyncIOScheduler 를 생성한 뒤 register_theme_live_refresh_jobs(_scheduler) 를 호출한다.
    theme_snapshot_jobs.py / ohlcv_sync_jobs.py 와 동일한 add-on 배선 패턴.

운영에서 켜는 법:
    export BARRO_THEME_LIVE_REFRESH_ENABLED=1   # 기본 OFF — 운영에서 명시적으로 켠다
    (백엔드 재기동 → lifespan → start_scheduler → 본 함수가 cron 잡 1개 등록)

안전 원칙(운영/실거래 경로 무영향):
    - 플래그 OFF(기본)면 잡을 하나도 등록하지 않는다(동작 byte-identical).
    - 잡 실행 중 예외는 잡 래퍼(_run_theme_live_refresh_job) 내부에서 삼켜 로깅만 한다
      → 스케줄러/서버/실거래 경로에 전파되지 않는다.
    - refresh_themes_from_seed 는 읽기 전용 시세 조회(cache_quotes)만 쓴다(주문 무관).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_FLAG_ENV = "BARRO_THEME_LIVE_REFRESH_ENABLED"


def _flag_enabled() -> bool:
    """BARRO_THEME_LIVE_REFRESH_ENABLED 플래그(기본 OFF) 해석 — 저장소 env 관례."""
    return os.environ.get(_FLAG_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


async def _run_theme_live_refresh_job() -> None:
    """테마 라이브 갱신 잡. 예외는 삼켜 로깅만(스케줄러 무영향)."""
    try:
        # 지연 import — 스케줄러 로드 시점에 themes/DB 그래프를 끌어오지 않는다.
        from backend.core.themes.theme_refresher import refresh_themes_from_seed

        result = await refresh_themes_from_seed()
        logger.info("테마 라이브 갱신 잡 완료: %s", result)
    except Exception:
        logger.warning("테마 라이브 갱신 잡 실패", exc_info=True)


def register_theme_live_refresh_jobs(scheduler, *, enabled: bool | None = None) -> list[str]:
    """AsyncIOScheduler 에 5분 주기(09~15시 KST, 평일) 테마 라이브 갱신 잡을 등록한다.

    Args:
        scheduler: APScheduler AsyncIOScheduler 인스턴스(add_job 제공).
        enabled: 강제 on/off (테스트용). None 이면 BARRO_THEME_LIVE_REFRESH_ENABLED 플래그.

    Returns:
        등록된 잡 id 리스트. 플래그 OFF 면 빈 리스트(잡 미등록).
    """
    if enabled is None:
        enabled = _flag_enabled()
    if not enabled:
        logger.debug("테마 라이브 갱신 잡 비활성 (%s 미설정) — 등록 생략", _FLAG_ENV)
        return []

    from apscheduler.triggers.cron import CronTrigger

    job_id = "theme_live_refresh"
    scheduler.add_job(
        _run_theme_live_refresh_job,
        CronTrigger(
            minute="*/5",
            hour="9-15",
            day_of_week="mon-fri",
            timezone="Asia/Seoul",
        ),
        id=job_id,
        name="테마 라이브 갱신 (5분)",
        replace_existing=True,
        misfire_grace_time=120,  # 2분 내 지연 실행 허용
    )
    logger.info("테마 라이브 갱신 잡 등록 완료: %s", job_id)
    return [job_id]


__all__ = ["register_theme_live_refresh_jobs"]
