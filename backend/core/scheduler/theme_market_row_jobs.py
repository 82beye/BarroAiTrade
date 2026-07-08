"""키움 랭킹 row CSV 저장 + 테마 집계 스케줄 잡.

테마 실시간 통계는 개별 테마 종목을 전부 ticker 조회하지 않고, 키움 랭킹 TR
(거래대금상위/등락률상위) row 를 먼저 CSV 로 남긴 뒤 그 row 로 테마 집계를 만든다.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_FLAG_ENV = "BARRO_THEME_MARKET_ROWS_ENABLED"
_INTERVAL_ENV = "BARRO_THEME_MARKET_ROWS_INTERVAL_SEC"
_TOP_N_ENV = "BARRO_THEME_MARKET_ROWS_TOP_N"
_FILTERS_ENV = "BARRO_THEME_MARKET_ROWS_FILTERS"
_DEFAULT_INTERVAL_SEC = 60
_DEFAULT_TOP_N = 100
_DEFAULT_FILTERS = "value,gainers,losers"


def _flag_enabled() -> bool:
    """기본 ON. 운영에서 끄려면 BARRO_THEME_MARKET_ROWS_ENABLED=0."""
    return os.environ.get(_FLAG_ENV, "1").strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


async def _capture_theme_market_rows_job() -> None:
    """키움 랭킹 row CSV 저장 + 테마 집계. 예외는 스케줄러로 전파하지 않는다."""
    try:
        from backend.core.scheduler.market_hours import is_open_rush

        if is_open_rush():
            logger.debug("테마 랭킹 row CSV 저장 — 개장 유예 구간, 이번 사이클 보류")
            return

        from backend.core.themes.market_row_store import capture_theme_market_rows

        result = await capture_theme_market_rows(
            top_n=_int_env(_TOP_N_ENV, _DEFAULT_TOP_N),
            filters=os.environ.get(_FILTERS_ENV, _DEFAULT_FILTERS),
        )
        logger.info(
            "테마 랭킹 row CSV 저장: rows=%s symbols=%s aggregates=%s path=%s",
            result.get("row_count"),
            result.get("symbol_count"),
            result.get("aggregate_count"),
            result.get("rows_csv"),
        )
    except Exception:
        logger.warning("테마 랭킹 row CSV 저장 잡 실패", exc_info=True)


def register_theme_market_row_jobs(scheduler, *, enabled: bool | None = None) -> list[str]:
    if enabled is None:
        enabled = _flag_enabled()
    if not enabled:
        logger.debug("테마 랭킹 row CSV 저장 잡 비활성 (%s=0) — 등록 생략", _FLAG_ENV)
        return []

    from apscheduler.triggers.interval import IntervalTrigger

    interval = max(10, _int_env(_INTERVAL_ENV, _DEFAULT_INTERVAL_SEC))
    job_id = "theme_market_rows_capture"
    scheduler.add_job(
        _capture_theme_market_rows_job,
        IntervalTrigger(seconds=interval),
        id=job_id,
        name=f"테마 랭킹 row CSV 저장 ({interval}s)",
        replace_existing=True,
        misfire_grace_time=30,
        max_instances=1,
    )
    logger.info("테마 랭킹 row CSV 저장 잡 등록 완료: %s (interval=%ds)", job_id, interval)
    return [job_id]


__all__ = ["register_theme_market_row_jobs"]
