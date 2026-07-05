"""인앱 일일 OHLCV 캐시 동기화 스케줄 잡 (default-OFF).

장마감(15:30 KST) 이후 15:40 에 `scripts/update_ohlcv_cache.py` 를 subprocess 로
실행하여 일봉 OHLCV 캐시(<repo_root>/data/ohlcv_cache, 또는 BARRO_OHLCV_CACHE_DIR)를
거래소(키움 KRX) 기준으로 최신화한다. 차트/시세 폴백이 참조하는 캐시가 그 대상이다.

운영에서 켜는 법(★ 운영 머신에서만 ★):
    export BARRO_OHLCV_SYNC_ENABLED=1   # 기본 OFF — 운영에서 명시적으로 켠다
    export KIWOOM_APP_KEY=... KIWOOM_APP_SECRET=...   # 키움 자체 OpenAPI 키 필요
    (백엔드 재기동 → lifespan → start_scheduler → 본 함수가 cron 잡 1개 등록)

배선 지점:
    scripts/finance/telegram_integration/scheduler.py 의 start_scheduler() 가
    AsyncIOScheduler 를 생성한 뒤 register_ohlcv_sync_jobs(_scheduler) 를 호출한다.
    theme_snapshot_jobs.py 와 동일한 add-on 배선 패턴.

안전 원칙(운영/실거래 경로 무영향):
    - 플래그 OFF(기본)면 잡을 하나도 등록하지 않는다(동작 byte-identical).
    - 잡 실행 중 예외·비정상 종료는 잡 래퍼(_run_ohlcv_sync_job) 내부에서 삼켜
      로깅만 한다 → 스케줄러/서버/실거래 경로에 전파되지 않는다.
    - update_ohlcv_cache.py 는 읽기 전용 시세/캔들 조회(ka10081)만 수행한다(주문 무관).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_FLAG_ENV = "BARRO_OHLCV_SYNC_ENABLED"

# 장마감 이후 EOD 갱신 시점(KST). update_ohlcv_cache.py 는 15:30 이후에만 당일봉을 받는다.
_SYNC_HOUR = 15
_SYNC_MINUTE = 40

# repo_root: 이 파일 backend/core/scheduler/ohlcv_sync_jobs.py → parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "update_ohlcv_cache.py"


def _flag_enabled() -> bool:
    """BARRO_OHLCV_SYNC_ENABLED 플래그(기본 OFF) 해석 — 저장소 env 관례."""
    return os.environ.get(_FLAG_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


async def _run_ohlcv_sync_job() -> None:
    """일봉 OHLCV 캐시 동기화 잡. 예외·비정상 종료는 삼켜 로깅만(스케줄러 무영향)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(_SCRIPT),
            cwd=str(_REPO_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            logger.info("OHLCV 동기화 잡 완료 (rc=0)")
        else:
            tail = (stdout or b"")[-500:].decode("utf-8", errors="replace")
            logger.warning(
                "OHLCV 동기화 잡 비정상 종료 rc=%s: %s", proc.returncode, tail
            )
    except Exception:
        logger.warning("OHLCV 동기화 잡 실패", exc_info=True)


def register_ohlcv_sync_jobs(scheduler, *, enabled: bool | None = None) -> list[str]:
    """AsyncIOScheduler 에 15:40 KST 일봉 OHLCV 캐시 동기화 잡을 등록한다.

    Args:
        scheduler: APScheduler AsyncIOScheduler 인스턴스(add_job 제공).
        enabled: 강제 on/off (테스트용). None 이면 BARRO_OHLCV_SYNC_ENABLED 플래그.

    Returns:
        등록된 잡 id 리스트. 플래그 OFF 면 빈 리스트(잡 미등록).
    """
    if enabled is None:
        enabled = _flag_enabled()
    if not enabled:
        logger.debug("OHLCV 동기화 잡 비활성 (%s 미설정) — 등록 생략", _FLAG_ENV)
        return []

    from apscheduler.triggers.cron import CronTrigger

    job_id = "ohlcv_daily_sync"
    scheduler.add_job(
        _run_ohlcv_sync_job,
        CronTrigger(hour=_SYNC_HOUR, minute=_SYNC_MINUTE, timezone="Asia/Seoul"),
        id=job_id,
        name="OHLCV 일봉 캐시 동기화",
        replace_existing=True,
        misfire_grace_time=600,  # 10분 내 지연 실행 허용
    )
    logger.info(
        "OHLCV 동기화 잡 등록 완료: %s (%02d:%02d KST)",
        job_id, _SYNC_HOUR, _SYNC_MINUTE,
    )
    return [job_id]


__all__ = ["register_ohlcv_sync_jobs"]
