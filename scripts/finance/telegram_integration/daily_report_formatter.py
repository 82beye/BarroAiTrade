"""
BAR-44 스펙에 맞는 일일 성과 리포트 포매터

Telegram 채널로 매일 18:00 KST에 발송할 일일 성과 리포트를 생성합니다.
데이터는 report_data 딕셔너리 형식으로 전달받습니다.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Any, Dict, List, Optional


class DailyReportFormatter:
    """BAR-44 스펙 기반 일일 성과 리포트 포매터"""

    @staticmethod
    def format_daily_report(report_data: Dict[str, Any]) -> str:
        """일일 성과 리포트 메시지 생성

        Args:
            report_data: 리포트 데이터 딕셔너리. 구조:
                {
                    "date": "2026-05-16",
                    "overall_return": 2.5,  # 전체 포트폴리오 수익률 %
                    "daily_pnl": 125000,    # 일일 손익액
                    "active_users": 150,    # 참여 사용자 수
                    "avg_return": 1.8,      # 평균 수익률 %
                    "strategies": {
                        "blueline": {"return": 3.2, "trades": 45, "winrate": 62.0},
                        "fzone": {"return": 2.1, "trades": 38, "winrate": 58.0},
                    },
                    "top_3_users": [
                        {"rank": 1, "nickname": "트레이더A", "return": 5.5, "top_stock": "삼성전자"},
                        {"rank": 2, "nickname": "트레이더B", "return": 4.2, "top_stock": "SK하이닉스"},
                        {"rank": 3, "nickname": "트레이더C", "return": 3.8, "top_stock": "NAVER"},
                    ],
                    "warnings": ["SK하이닉스 -5% 급락 주의"],
                    "tomorrow_forecast": {
                        "watch_stocks": ["005930", "000660"],
                        "ai_insight": "기술적 반등 신호 감지"
                    }
                }

        Returns:
            Telegram Markdown 형식의 리포트 텍스트
        """
        report_date = report_data.get("date", str(date.today()))
        date_obj = datetime.strptime(report_date, "%Y-%m-%d")
        day_of_week = ["월", "화", "수", "목", "금", "토", "일"][date_obj.weekday()]

        lines = []

        # 헤더
        lines.append("📊 BarroAiTrade 일일 성과 리포트")
        lines.append("═" * 35)
        lines.append(f"📅 {report_date} ({day_of_week})")
        lines.append("")

        # 오늘의 성과
        lines.append("🎯 오늘의 성과")
        lines.append("━" * 35)
        overall_return = report_data.get("overall_return", 0.0)
        daily_pnl = report_data.get("daily_pnl", 0)
        active_users = report_data.get("active_users", 0)
        avg_return = report_data.get("avg_return", 0.0)

        # 수익률 표시 (양수는 +, 음수는 -)
        return_str = f"{overall_return:+.2f}%" if overall_return >= 0 else f"{overall_return:.2f}%"
        avg_return_str = f"{avg_return:+.2f}%" if avg_return >= 0 else f"{avg_return:.2f}%"
        pnl_str = f"{daily_pnl:+,} 원" if daily_pnl >= 0 else f"{daily_pnl:,} 원"

        lines.append(f"📈 전체 포트폴리오 수익률: {return_str}")
        lines.append(f"💰 일일 손익액: {pnl_str}")
        lines.append(f"👥 참여 사용자: {active_users:,} 명")
        lines.append(f"📊 평균 수익률: {avg_return_str}")
        lines.append("")

        # 전략별 성과
        lines.append("━" * 35)
        lines.append("💼 전략별 성과")
        lines.append("")

        strategies = report_data.get("strategies", {})

        # 블루라인(돌파)
        blueline = strategies.get("blueline", {})
        bl_return = blueline.get("return", 0.0)
        bl_trades = blueline.get("trades", 0)
        bl_winrate = blueline.get("winrate", 0.0)
        bl_return_str = f"{bl_return:+.2f}%" if bl_return >= 0 else f"{bl_return:.2f}%"

        lines.append("🔵 블루라인(돌파)")
        lines.append(f"├─ 수익률: {bl_return_str}")
        lines.append(f"├─ 거래건수: {bl_trades}")
        lines.append(f"└─ 승률: {bl_winrate:.1f}%")
        lines.append("")

        # F존(모멘텀)
        fzone = strategies.get("fzone", {})
        fz_return = fzone.get("return", 0.0)
        fz_trades = fzone.get("trades", 0)
        fz_winrate = fzone.get("winrate", 0.0)
        fz_return_str = f"{fz_return:+.2f}%" if fz_return >= 0 else f"{fz_return:.2f}%"

        lines.append("🟣 F존(모멘텀)")
        lines.append(f"├─ 수익률: {fz_return_str}")
        lines.append(f"├─ 거래건수: {fz_trades}")
        lines.append(f"└─ 승률: {fz_winrate:.1f}%")
        lines.append("")

        # TOP 3 성과자
        lines.append("━" * 35)
        lines.append("🏆 오늘의 TOP 3 성과자")
        lines.append("")

        top_users = report_data.get("top_3_users", [])
        medals = ["🥇", "🥈", "🥉"]

        for i, user in enumerate(top_users[:3]):
            medal = medals[i] if i < len(medals) else ""
            nickname = user.get("nickname", "익명")
            user_return = user.get("return", 0.0)
            top_stock = user.get("top_stock", "N/A")
            user_return_str = f"{user_return:+.1f}%" if user_return >= 0 else f"{user_return:.1f}%"

            lines.append(f"{medal} {nickname}")
            lines.append(f"   └─ {user_return_str} | {top_stock}")
            lines.append("")

        # 주의사항
        lines.append("━" * 35)
        lines.append("⚠️ 주의사항")
        lines.append("")

        warnings = report_data.get("warnings", [])
        if warnings:
            for warning in warnings:
                lines.append(f"• {warning}")
        else:
            lines.append("• 특별한 주의사항 없음")

        lines.append("")

        # 내일 전망
        lines.append("━" * 35)
        lines.append("💡 내일 전망")
        lines.append("")

        forecast = report_data.get("tomorrow_forecast", {})
        watch_stocks = forecast.get("watch_stocks", [])
        ai_insight = forecast.get("ai_insight", "")

        if watch_stocks:
            stocks_str = ", ".join(watch_stocks)
            lines.append(f"주목할 종목: {stocks_str}")
        if ai_insight:
            lines.append(f"AI 분석 포인트: {ai_insight}")

        if not watch_stocks and not ai_insight:
            lines.append("내일의 분석 데이터가 아직 준비되지 않았습니다.")

        lines.append("")
        lines.append("─" * 35)
        lines.append("💬 공유하기 | 📱 앱 열기 | 🎯 전략 수정")

        return "\n".join(lines)

    @staticmethod
    def format_empty_report(report_date: str) -> str:
        """거래 없음 시 알림 메시지"""
        return f"📭 {report_date} 일일 성과 리포트\n\n거래 내역이 없습니다."


# 전역 인스턴스
daily_report_formatter = DailyReportFormatter()
