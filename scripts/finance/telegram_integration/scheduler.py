"""
APScheduler 기반 일일 리포트 자동 전송 스케줄러

매일 오전 9시 (KST)에 종목 분석 리포트 및 일일 손익 리포트를 Telegram으로 전송.
FastAPI lifespan에서 start_scheduler() / stop_scheduler()를 호출하여 사용.

긴급 알림은 send_urgent_signal()을 통해 즉시 전송 가능.
"""
from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


# ── 스케줄 작업 ───────────────────────────────────────────────────────────────

async def _daily_report_job() -> None:
    """매일 09:00 KST 실행: 일일 손익 리포트 Telegram 전송"""
    try:
        from backend.core.monitoring.report_service import report_service
        from backend.core.monitoring.alert_service import alert_service
        from backend.core.state import app_state

        position_manager = getattr(app_state, "position_manager", None)
        trades = position_manager.get_trade_history() if position_manager else []

        report = report_service.build_daily_report(trades)
        await report_service.send_daily_report(report, alert_service)
        logger.info("일일 리포트 스케줄 전송 완료")
    except Exception as e:
        logger.error("일일 리포트 스케줄 전송 실패: %s", e)


# ── 공개 인터페이스 ───────────────────────────────────────────────────────────

def start_scheduler() -> AsyncIOScheduler:
    """스케줄러 시작

    FastAPI lifespan의 yield 이전에 호출:
        scheduler = start_scheduler()
        yield
        stop_scheduler()

    Returns:
        실행 중인 AsyncIOScheduler 인스턴스
    """
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # 매일 오전 9시 KST — 일일 리포트
    _scheduler.add_job(
        _daily_report_job,
        CronTrigger(hour=9, minute=0, timezone="Asia/Seoul"),
        id="daily_report",
        name="일일 리포트 전송",
        replace_existing=True,
        misfire_grace_time=300,  # 5분 내 실행 지연 허용
    )

    _scheduler.start()
    logger.info("스케줄러 시작: 매일 09:00 KST 일일 리포트 전송")
    return _scheduler


def stop_scheduler() -> None:
    """스케줄러 중지 (FastAPI lifespan 종료 시 호출)"""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("스케줄러 중지됨")


async def send_report_now() -> None:
    """즉시 일일 리포트 전송 (긴급 상황 또는 수동 트리거용)"""
    await _daily_report_job()


async def send_urgent_signal(signal, reason: str = "") -> None:
    """긴급 종목 신호 즉시 전송

    Args:
        signal: EntrySignal 객체
        reason: 긴급 전송 사유
    """
    try:
        from backend.core.monitoring.telegram_bot import telegram
        from scripts.finance.telegram_integration.report_generator import report_generator

        if not telegram.enabled:
            logger.warning("Telegram 비활성화 — 긴급 신호 전송 건너뜀")
            return

        text = report_generator.generate_urgent_alert(signal, reason)
        await telegram.send_raw_message(text)
        logger.info("긴급 신호 전송 완료: %s", signal.symbol)
    except Exception as e:
        logger.error("긴급 신호 전송 실패: %s", e)
