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

    # ── 분석 종목 알림 ────────────────────────────────────────────────────────

    async def on_daily_scan_result(self, signals: list) -> None:
        """당일 스캔 결과 전송"""
        SIGNAL_TYPE_KR = {
            "blue_line": "파란점선",
            "watermelon": "수박",
            "f_zone": "F존",
            "sf_zone": "SF존",
            "crypto_breakout": "돌파",
        }
        if not signals:
            await self._send(AlertLevel.INFO, "당일 분석 종목", "스캔 완료 — 신호 없음")
            return

        lines = [f"📊 당일 분석 종목 ({len(signals)}개)"]
        for i, sig in enumerate(signals, 1):
            signal_kr = SIGNAL_TYPE_KR.get(sig.signal_type, sig.signal_type)
            lines.append(
                f"{'①②③④⑤⑥⑦⑧⑨⑩'[i-1] if i <= 10 else str(i)} "
                f"{sig.name} ({sig.symbol}) | {signal_kr} | 점수: {sig.score:.1f} | {sig.price:,.0f}원"
            )

        await self._send(AlertLevel.INFO, "", "\n".join(lines))

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

    async def on_daily_report(self, report: dict) -> None:
        """일일 P&L 리포트 Telegram 전송"""
        summary = report.get("summary", {})
        date_str = report.get("date", "")
        total_trades = summary.get("total_trades", 0)
        if total_trades == 0:
            await self._send(AlertLevel.INFO, f"일일 리포트 ({date_str})", "오늘 매매 없음")
            return
        win_rate = summary.get("win_rate_pct", 0.0)
        total_pnl = summary.get("total_pnl", 0.0)
        pnl_pct = summary.get("total_pnl_pct", 0.0)
        level = AlertLevel.SUCCESS if total_pnl >= 0 else AlertLevel.WARNING
        body = (
            f"총 매매: {total_trades}건\n"
            f"승률: {win_rate:.1f}%\n"
            f"손익: {total_pnl:+,.0f}원 ({pnl_pct:+.2%})"
        )
        await self._send(level, f"일일 리포트 ({date_str})", body)

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    async def _send(self, level: AlertLevel, title: str, body: str = "") -> None:
        try:
            await self._notifier.send(level, title, body)
        except Exception as e:
            logger.error("알림 전송 실패: %s", e)


# 전역 인스턴스
alert_service = AlertService()
