"""
ReportService — 일일 매매 요약 리포트 생성

매매 내역을 집계하여 일일 손익, 승률, 매매 통계를 계산.
AlertService를 통해 Telegram으로 일일 리포트 전송.
"""
from __future__ import annotations

import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ReportService:
    """일일 매매 리포트 생성 및 전송"""

    def __init__(self) -> None:
        self._daily_reports: Dict[str, Dict[str, Any]] = {}  # date -> report

    # ── 리포트 생성 ───────────────────────────────────────────────────────────

    def build_daily_report(
        self,
        trades: List[Dict[str, Any]],
        target_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """매매 내역으로 일일 리포트 생성

        Args:
            trades: PositionManager.get_trade_history() 반환 값
            target_date: 조회 대상 날짜 (기본: 오늘)

        Returns:
            리포트 딕셔너리
        """
        target_date = target_date or date.today()
        date_str = target_date.isoformat()

        # 날짜 필터
        daily_trades = [
            t for t in trades
            if t.get("exit_time", "").startswith(date_str)
        ]

        if not daily_trades:
            return self._empty_report(date_str)

        wins = [t for t in daily_trades if t.get("pnl", 0) > 0]
        losses = [t for t in daily_trades if t.get("pnl", 0) <= 0]
        total_pnl = sum(t.get("pnl", 0) for t in daily_trades)
        total_invested = sum(
            t.get("entry_price", 0) * t.get("quantity", 0) for t in daily_trades
        )
        pnl_pct = total_pnl / total_invested if total_invested > 0 else 0.0

        report = {
            "date": date_str,
            "summary": {
                "trades_count": len(daily_trades),
                "win_count": len(wins),
                "loss_count": len(losses),
                "win_rate": len(wins) / len(daily_trades) * 100 if daily_trades else 0.0,
                "pnl": total_pnl,
                "pnl_pct": pnl_pct * 100,
                "avg_win": sum(t.get("pnl", 0) for t in wins) / len(wins) if wins else 0,
                "avg_loss": sum(t.get("pnl", 0) for t in losses) / len(losses) if losses else 0,
            },
            "trades": [self._format_trade(t) for t in daily_trades],
            "generated_at": datetime.now().isoformat(),
        }

        self._daily_reports[date_str] = report
        return report

    def get_cached_report(self, date_str: str) -> Optional[Dict[str, Any]]:
        """캐시된 일일 리포트 반환"""
        return self._daily_reports.get(date_str)

    def build_performance_summary(
        self,
        trades: List[Dict[str, Any]],
        period: str = "1m",
    ) -> Dict[str, Any]:
        """기간별 성과 요약

        Args:
            period: "1w" | "1m" | "3m" | "ytd" | "all"
        """
        if not trades:
            return self._empty_performance(period)

        total_pnl = sum(t.get("pnl", 0) for t in trades)
        total_invested = sum(
            t.get("entry_price", 0) * t.get("quantity", 0) for t in trades
        )
        wins = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) <= 0]

        win_rate = len(wins) / len(trades) * 100 if trades else 0.0
        total_return = total_pnl / total_invested * 100 if total_invested > 0 else 0.0

        avg_win = sum(t.get("pnl", 0) for t in wins) / len(wins) if wins else 0
        avg_loss = abs(sum(t.get("pnl", 0) for t in losses) / len(losses)) if losses else 1
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0

        return {
            "period": period,
            "summary": {
                "total_return": total_return,
                "win_rate": win_rate,
                "profit_factor": profit_factor,
                "max_drawdown": self._calc_max_drawdown(trades),
                "sharpe_ratio": 0.0,  # TODO: 실제 Sharpe 계산
                "trades_count": len(trades),
                "total_pnl": total_pnl,
            },
            "monthly": [],  # TODO: 월별 집계
        }

    async def send_daily_report(
        self,
        report: Dict[str, Any],
        alert_service: Any,
    ) -> None:
        """Telegram으로 일일 리포트 전송"""
        try:
            s = report.get("summary", {})
            sign = "+" if s.get("pnl", 0) >= 0 else ""
            body = (
                f"매매: {s.get('trades_count', 0)}회 "
                f"(승: {s.get('win_count', 0)} / 패: {s.get('loss_count', 0)})\n"
                f"승률: {s.get('win_rate', 0):.1f}%\n"
                f"손익: {sign}{s.get('pnl', 0):,.0f}원 ({sign}{s.get('pnl_pct', 0):.2f}%)"
            )
            from backend.core.monitoring.telegram_bot import AlertLevel
            await alert_service._send(AlertLevel.INFO, f"일일 리포트 {report.get('date', '')}", body)
        except Exception as e:
            logger.error("일일 리포트 전송 실패: %s", e)

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _empty_report(self, date_str: str) -> Dict[str, Any]:
        return {
            "date": date_str,
            "summary": {
                "trades_count": 0,
                "win_count": 0,
                "loss_count": 0,
                "win_rate": 0.0,
                "pnl": 0,
                "pnl_pct": 0.0,
                "avg_win": 0,
                "avg_loss": 0,
            },
            "trades": [],
            "generated_at": datetime.now().isoformat(),
        }

    def _empty_performance(self, period: str) -> Dict[str, Any]:
        return {
            "period": period,
            "summary": {
                "total_return": 0.0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
                "trades_count": 0,
                "total_pnl": 0,
            },
            "monthly": [],
        }

    def _format_trade(self, trade: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "symbol": trade.get("symbol", ""),
            "side": trade.get("side", "sell"),
            "entry_price": trade.get("entry_price", 0),
            "exit_price": trade.get("exit_price", 0),
            "quantity": trade.get("quantity", 0),
            "pnl": trade.get("pnl", 0),
            "pnl_pct": trade.get("pnl_pct", 0) * 100,
            "exit_time": trade.get("exit_time", ""),
            "strategy_id": trade.get("strategy_id", ""),
        }

    def _calc_max_drawdown(self, trades: List[Dict[str, Any]]) -> float:
        """최대 낙폭 계산"""
        if not trades:
            return 0.0
        pnl_series = [t.get("pnl", 0) for t in trades]
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for pnl in pnl_series:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / peak * 100 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd


# 전역 인스턴스
report_service = ReportService()
