"""테마 스냅숏 스케줄 잡 등록 (티마 앱 벤치마킹 P1 — 운영 배선).

장중 3개 고정 시점(10:00 / 12:30 / 15:35 KST)에 테마 보드를 동결하기 위해
`backend.core.themes.snapshot.capture_theme_snapshot(slot)` 을 호출하는 APScheduler
잡을 등록한다. PRD §3.2 오전→오후 주도주 로테이션 비교용 타임라인.

배선 지점:
    기존 BAR-49 일일 리포트 스케줄러
    (scripts/finance/telegram_integration/scheduler.py 의 start_scheduler()) 가
    AsyncIOScheduler 를 생성한 뒤 register_theme_snapshot_jobs(_scheduler) 를 호출한다.
    이 스케줄러는 backend/main.py 의 FastAPI lifespan 에서 기동된다.

운영에서 켜는 법:
    export BARRO_THEME_SNAPSHOT_ENABLED=1   # 기본 OFF — 운영에서 명시적으로 켠다
    (백엔드 재기동 → lifespan → start_scheduler → 본 함수가 3개 cron 잡 등록)

안전 원칙(운영 경로 무영향):
    - 플래그 OFF(기본)면 잡을 하나도 등록하지 않는다(동작 byte-identical).
    - 잡 실행 중 예외는 잡 래퍼(_run_snapshot_job) 내부에서 삼켜 로깅만 한다
      → 스케줄러/서버/실거래 경로에 전파되지 않는다.
    - capture 는 gateway 미초기화 시 시세 필드를 null 로 동결한다(snapshot.py, 날조 금지).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# 장중 고정 스냅숏 시점(KST) → cron (hour, minute). snapshot.VALID_SLOTS 와 1:1.
SLOT_CRON = {
    "10:00": (10, 0),
    "12:30": (12, 30),
    "15:35": (15, 35),
}

_FLAG_ENV = "BARRO_THEME_SNAPSHOT_ENABLED"


def _flag_enabled() -> bool:
    """BARRO_THEME_SNAPSHOT_ENABLED 플래그(기본 OFF) 해석 — 저장소 env 관례."""
    return os.environ.get(_FLAG_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


async def _run_snapshot_job(slot: str) -> None:
    """단일 slot 스냅숏 동결 잡. 예외는 삼켜 로깅만(스케줄러 무영향)."""
    try:
        # 지연 import — 스케줄러 로드 시점에 themes/API 그래프를 끌어오지 않는다.
        from backend.core.themes.snapshot import capture_theme_snapshot

        await capture_theme_snapshot(slot)
        logger.info("테마 스냅숏 잡 완료: slot=%s", slot)
    except Exception:
        logger.warning("테마 스냅숏 잡 실패: slot=%s", slot, exc_info=True)


def register_theme_snapshot_jobs(scheduler, *, enabled: bool | None = None) -> list[str]:
    """AsyncIOScheduler 에 10:00/12:30/15:35 KST 테마 스냅숏 잡을 등록한다.

    Args:
        scheduler: APScheduler AsyncIOScheduler 인스턴스(add_job 제공).
        enabled: 강제 on/off (테스트용). None 이면 BARRO_THEME_SNAPSHOT_ENABLED 플래그.

    Returns:
        등록된 잡 id 리스트. 플래그 OFF 면 빈 리스트(잡 미등록).
    """
    if enabled is None:
        enabled = _flag_enabled()
    if not enabled:
        logger.debug("테마 스냅숏 잡 비활성 (%s 미설정) — 등록 생략", _FLAG_ENV)
        return []

    from apscheduler.triggers.cron import CronTrigger

    job_ids: list[str] = []
    for slot, (hour, minute) in SLOT_CRON.items():
        job_id = f"theme_snapshot_{slot.replace(':', '')}"
        scheduler.add_job(
            _run_snapshot_job,
            CronTrigger(hour=hour, minute=minute, timezone="Asia/Seoul"),
            args=[slot],
            id=job_id,
            name=f"테마 스냅숏 {slot}",
            replace_existing=True,
            misfire_grace_time=300,  # 5분 내 지연 실행 허용
        )
        job_ids.append(job_id)
    logger.info("테마 스냅숏 잡 등록 완료: %s", job_ids)
    return job_ids


__all__ = ["SLOT_CRON", "register_theme_snapshot_jobs"]
