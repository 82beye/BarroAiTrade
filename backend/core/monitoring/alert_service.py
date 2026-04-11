"""
AlertService — 매매 이벤트 알림 서비스

진입/청산/리스크경고/에러 이벤트를 Telegram으로 알림.
TelegramNotifier를 내부적으로 사용하며, 알림 실패는 시스템을 중단시키지 않음.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from backend.core.monitoring.telegram_bot import TelegramNotifier, AlertLevel, telegram

logger = logging.getLogger(__name__)


class AlertService:
    """매매 이벤트 알림 서비스"""

    def __init__(self, notifier: Optional[TelegramNotifier] = None) -> None:
        self._notifier = notifier or telegram

    # ── 진입 알림 ─────────────────────────────────────────────────────────────

    async def on_entry(
        self,
        symbol: str,
        name: str,
        price: float,
        quantity: float,
        strategy_id: str,
        score: float = 0.0,
        reason: str = "",
    ) -> None:
        title = f"매수 체결: {name} ({symbol})"
        body_parts = [
            f"가격: {price:,.0f}원",
            f"수량: {quantity:.0f}주",
            f"전략: {strategy_id}",
        ]
        if score:
            body_parts.append(f"점수: {score:.1f}")
        if reason:
            body_parts.append(f"사유: {reason}")
        await self._send(AlertLevel.TRADE, title, "\n".join(body_parts))

    # ── 청산 알림 ─────────────────────────────────────────────────────────────

    async def on_exit(
        self,
        symbol: str,
        name: str,
        price: float,
        quantity: float,
        pnl: float,
        pnl_pct: float,
        exit_type: str,
    ) -> None:
        level = AlertLevel.SUCCESS if pnl >= 0 else AlertLevel.WARNING
        title = f"매도 체결: {name} ({symbol})"
        sign = "+" if pnl >= 0 else ""
        body = "\n".join([
            f"가격: {price:,.0f}원",
            f"수량: {quantity:.0f}주",
            f"손익: {sign}{pnl:,.0f}원 ({sign}{pnl_pct:.2%})",
            f"유형: {exit_type}",
        ])
        await self._send(level, title, body)

    # ── 리스크 경고 ───────────────────────────────────────────────────────────

    async def on_risk_blocked(self, symbol: str, reason: str) -> None:
        await self._send(
            AlertLevel.WARNING,
            f"주문 차단: {symbol}",
            reason,
        )

    async def on_daily_limit_breached(self, pnl_pct: float) -> None:
        await self._send(
            AlertLevel.ERROR,
            "일일 손실 한도 초과",
            f"현재 손실률: {pnl_pct:.2%}\n전량 청산 진행 중",
        )

    async def on_force_close(self, symbols: list, reason: str) -> None:
        if not symbols:
            return
        await self._send(
            AlertLevel.ERROR,
            "강제 청산 실행",
            f"대상: {', '.join(symbols)}\n사유: {reason}",
        )

    # ── 시스템 알림 ───────────────────────────────────────────────────────────

    async def on_error(self, component: str, error: str) -> None:
        await self._send(
            AlertLevel.ERROR,
            f"시스템 오류: {component}",
            error[:500],
        )

    async def on_system_start(self, mode: str, market: str) -> None:
        await self._send(
            AlertLevel.INFO,
            "BarroAiTrade 시작",
            f"모드: {mode} | 마켓: {market}",
        )

    async def on_system_stop(self, reason: str = "") -> None:
        body = f"사유: {reason}" if reason else ""
        await self._send(AlertLevel.INFO, "BarroAiTrade 중지", body)

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    async def _send(self, level: AlertLevel, title: str, body: str = "") -> None:
        try:
            await self._notifier.send(level, title, body)
        except Exception as e:
            logger.error("알림 전송 실패: %s", e)


# 전역 인스턴스
alert_service = AlertService()
