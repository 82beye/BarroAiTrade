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

    def build_comprehensive_daily_report(
        self,
        trades: List[Dict[str, Any]],
        active_users: int = 0,
        target_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """BAR-44 스펙 기반 comprehensive 일일 리포트 데이터 빌드

        Args:
            trades: 거래 내역 리스트
            active_users: 활성 사용자 수
            target_date: 조회 대상 날짜

        Returns:
            BAR-44 포매터가 사용할 리포트 데이터
        """
        target_date = target_date or date.today()
        date_str = target_date.isoformat()

        # 날짜 필터링
        daily_trades = [
            t for t in trades
            if t.get("exit_time", "").startswith(date_str)
        ]

        # 기본 통계
        if daily_trades:
            total_pnl = sum(t.get("pnl", 0) for t in daily_trades)
            total_invested = sum(
                t.get("entry_price", 0) * t.get("quantity", 0) for t in daily_trades
            )
            overall_return = (total_pnl / total_invested * 100) if total_invested > 0 else 0.0
        else:
            total_pnl = 0
            overall_return = 0.0

        # 평균 수익률 (사용자별)
        avg_return = overall_return if daily_trades else 0.0

        # 전략별 성과
        blueline_trades = [t for t in daily_trades if t.get("strategy_id") == "blueline"]
        fzone_trades = [t for t in daily_trades if t.get("strategy_id") == "fzone"]

        strategies = {
            "blueline": self._calc_strategy_stats(blueline_trades),
            "fzone": self._calc_strategy_stats(fzone_trades),
        }

        # TOP 3 사용자 (임시: 전략별 최고 수익)
        top_3_users = self._get_top_performers(daily_trades)

        # 경고 메시지 (임시)
        warnings = []
        if overall_return < -5:
            warnings.append("⚠️ 오늘 수익률이 -5% 이하입니다. 포트폴리오 점검이 필요합니다.")

        # 내일 전망 — 당일 거래 중 수익이 있는 상위 종목
        watch_stocks = self._extract_top_performing_stocks(daily_trades)
        tomorrow_forecast = {
            "watch_stocks": watch_stocks,
            "ai_insight": "시장 분석 데이터 준비 중"
        }

        return {
            "date": date_str,
            "overall_return": overall_return,
            "daily_pnl": total_pnl,
            "active_users": active_users,
            "avg_return": avg_return,
            "strategies": strategies,
            "top_3_users": top_3_users,
            "warnings": warnings,
            "tomorrow_forecast": tomorrow_forecast,
        }

    async def send_daily_report(
        self,
        report: Dict[str, Any],
        alert_service: Any = None,
    ) -> None:
        """Telegram으로 일일 리포트 전송

        BAR-44 형식으로 포매팅된 comprehensive 리포트를 전송합니다.
        """
        try:
            from scripts.finance.telegram_integration.daily_report_formatter import daily_report_formatter
            from backend.core.monitoring.telegram_bot import telegram, AlertLevel

            # report 구조에 따라 적절한 포맷 선택
            if "overall_return" in report:
                # comprehensive 리포트 (BAR-44 형식)
                message = daily_report_formatter.format_daily_report(report)
                if telegram.enabled:
                    await telegram.send_raw_message(message)
                else:
                    logger.warning("Telegram 비활성화 — 리포트 로그만 저장됨")
                    logger.info("일일 성과 리포트:\n%s", message)
            else:
                # 기본 리포트 (호환성)
                s = report.get("summary", {})
                sign = "+" if s.get("pnl", 0) >= 0 else ""
                body = (
                    f"매매: {s.get('trades_count', 0)}회 "
                    f"(승: {s.get('win_count', 0)} / 패: {s.get('loss_count', 0)})\n"
                    f"승률: {s.get('win_rate', 0):.1f}%\n"
                    f"손익: {sign}{s.get('pnl', 0):,.0f}원 ({sign}{s.get('pnl_pct', 0):.2f}%)"
                )
                if telegram.enabled:
                    await telegram.send(AlertLevel.INFO, f"일일 리포트 {report.get('date', '')}", body)
                else:
                    logger.warning("Telegram 비활성화 — 리포트 로그만 저장됨")
                    logger.info("일일 리포트 %s:\n%s", report.get('date', ''), body)

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

    def _calc_strategy_stats(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """전략별 성과 통계 계산"""
        if not trades:
            return {
                "return": 0.0,
                "trades": 0,
                "winrate": 0.0,
            }

        total_pnl = sum(t.get("pnl", 0) for t in trades)
        total_invested = sum(
            t.get("entry_price", 0) * t.get("quantity", 0) for t in trades
        )
        strategy_return = (total_pnl / total_invested * 100) if total_invested > 0 else 0.0

        wins = len([t for t in trades if t.get("pnl", 0) > 0])
        total_trades = len(trades)
        winrate = (wins / total_trades * 100) if total_trades > 0 else 0.0

        return {
            "return": strategy_return,
            "trades": total_trades,
            "winrate": winrate,
        }

    def _get_top_performers(self, trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """TOP 3 성과자 추출 (현재: 가상 데이터)

        실제 구현에서는 사용자별 수익률을 집계합니다.
        """
        # TODO: 실제 사용자 수익률 데이터 연동
        if not trades:
            return []

        # 임시: 전체 수익이 있으면 샘플 TOP 3 반환
        total_pnl = sum(t.get("pnl", 0) for t in trades)
        if total_pnl <= 0:
            return []

        # 가장 이익을 본 거래들로부터 TOP 3 사용자 시뮬레이션
        return [
            {
                "rank": 1,
                "nickname": "트레이더A",
                "return": min(total_pnl * 0.15, 10.0),
                "top_stock": "삼성전자"
            },
            {
                "rank": 2,
                "nickname": "트레이더B",
                "return": min(total_pnl * 0.10, 8.0),
                "top_stock": "SK하이닉스"
            },
            {
                "rank": 3,
                "nickname": "트레이더C",
                "return": min(total_pnl * 0.08, 6.0),
                "top_stock": "NAVER"
            },
        ]

    def _extract_top_performing_stocks(self, trades: List[Dict[str, Any]]) -> List[str]:
        """당일 거래 중 수익이 있는 상위 2개 종목 추출

        Args:
            trades: 당일 거래 내역

        Returns:
            상위 수익 종목 코드 리스트 (최대 2개)
        """
        if not trades:
            return []

        # PnL > 0인 거래만 필터링
        profitable_trades = [t for t in trades if t.get("pnl", 0) > 0]
        if not profitable_trades:
            return []

        # 종목별 총 PnL 계산
        symbol_pnl: Dict[str, float] = {}
        for trade in profitable_trades:
            symbol = trade.get("symbol", "")
            if symbol:
                symbol_pnl[symbol] = symbol_pnl.get(symbol, 0) + trade.get("pnl", 0)

        # PnL 순으로 정렬하여 상위 2개 추출
        sorted_symbols = sorted(symbol_pnl.items(), key=lambda x: x[1], reverse=True)
        return [symbol for symbol, _ in sorted_symbols[:2]]


# 전역 인스턴스
report_service = ReportService()
