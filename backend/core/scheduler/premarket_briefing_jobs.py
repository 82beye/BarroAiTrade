"""개장 전 Telegram 브리핑 스케줄 잡.

전일 일봉/누적 체결 기반 결과이므로 평일 08:25 KST에 한 번 실행한다. 5분 상시 배치는
동일 메시지 반복, Kiwoom 조회 경쟁, Telegram 스팸만 늘리므로 사용하지 않는다. 실행
프로세스 내부에서 실패할 때만 5분 간격 최대 2회 재시도한다.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_FLAG_ENV = "BARRO_PREMARKET_BRIEFING_ENABLED"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "premarket_telegram_briefing.py"


def _enabled() -> bool:
    return os.environ.get(_FLAG_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


async def _run_premarket_briefing_job() -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(_SCRIPT),
            "--attempts", "3",
            "--retry-delay", "300",
            cwd=str(_REPO_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await proc.communicate()
        tail = (output or b"")[-1500:].decode("utf-8", errors="replace")
        if proc.returncode == 0:
            logger.info("개장 전 Telegram 브리핑 완료: %s", tail)
        else:
            logger.error("개장 전 Telegram 브리핑 실패 rc=%s: %s", proc.returncode, tail)
    except Exception:
        logger.exception("개장 전 Telegram 브리핑 잡 실행 실패")


def register_premarket_briefing_jobs(
    scheduler, *, enabled: bool | None = None,
) -> list[str]:
    if enabled is None:
        enabled = _enabled()
    if not enabled:
        logger.debug("개장 전 브리핑 비활성(%s 미설정)", _FLAG_ENV)
        return []

    from apscheduler.triggers.cron import CronTrigger

    job_id = "premarket_telegram_briefing"
    scheduler.add_job(
        _run_premarket_briefing_job,
        CronTrigger(
            day_of_week="mon-fri", hour=8, minute=25,
            timezone="Asia/Seoul",
        ),
        id=job_id,
        name="개장 전 종목·예측·전략 Telegram 브리핑",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=600,
    )
    logger.info("개장 전 Telegram 브리핑 잡 등록: 평일 08:25 KST")
    return [job_id]


__all__ = ["register_premarket_briefing_jobs"]
