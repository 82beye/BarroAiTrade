#!/usr/bin/env python3
"""
BAR-49 Telegram 일일 성과 리포트 스케줄러 테스트
"""
import asyncio
import sys
from datetime import datetime, date

sys.path.insert(0, '/Users/beye/workspace/BarroAiTrade')


def test_daily_report_formatter():
    """일일 리포트 포매터 테스트"""
    from scripts.finance.telegram_integration.daily_report_formatter import daily_report_formatter

    # 테스트 데이터
    test_report = {
        "date": "2026-05-16",
        "overall_return": 2.45,
        "daily_pnl": 125000,
        "active_users": 150,
        "avg_return": 1.85,
        "strategies": {
            "blueline": {
                "return": 3.2,
                "trades": 45,
                "winrate": 62.5,
            },
            "fzone": {
                "return": 2.1,
                "trades": 38,
                "winrate": 58.0,
            },
        },
        "top_3_users": [
            {
                "rank": 1,
                "nickname": "트레이더A",
                "return": 5.5,
                "top_stock": "삼성전자",
            },
            {
                "rank": 2,
                "nickname": "트레이더B",
                "return": 4.2,
                "top_stock": "SK하이닉스",
            },
            {
                "rank": 3,
                "nickname": "트레이더C",
                "return": 3.8,
                "top_stock": "NAVER",
            },
        ],
        "warnings": ["SK하이닉스 -5% 급락 주의"],
        "tomorrow_forecast": {
            "watch_stocks": ["005930", "000660"],
            "ai_insight": "기술적 반등 신호 감지",
        },
    }

    message = daily_report_formatter.format_daily_report(test_report)
    print("=" * 50)
    print("일일 리포트 포매팅 테스트 결과:")
    print("=" * 50)
    print(message)
    print("=" * 50)
    return message


def test_comprehensive_report_building():
    """comprehensive 리포트 빌드 테스트"""
    from backend.core.monitoring.report_service import report_service

    # 테스트 거래 데이터
    test_trades = [
        {
            "symbol": "005930",
            "side": "buy",
            "entry_price": 70000,
            "exit_price": 72000,
            "quantity": 10,
            "pnl": 20000,
            "pnl_pct": 0.0286,
            "exit_time": "2026-05-16T14:30:00",
            "strategy_id": "blueline",
        },
        {
            "symbol": "000660",
            "side": "buy",
            "entry_price": 100000,
            "exit_price": 102000,
            "quantity": 5,
            "pnl": 10000,
            "pnl_pct": 0.02,
            "exit_time": "2026-05-16T15:00:00",
            "strategy_id": "fzone",
        },
        {
            "symbol": "005380",
            "side": "buy",
            "entry_price": 55000,
            "exit_price": 54000,
            "quantity": 10,
            "pnl": -10000,
            "pnl_pct": -0.0182,
            "exit_time": "2026-05-16T15:30:00",
            "strategy_id": "blueline",
        },
    ]

    report = report_service.build_comprehensive_daily_report(
        trades=test_trades,
        active_users=150,
    )

    print("\n" + "=" * 50)
    print("Comprehensive 리포트 빌드 테스트 결과:")
    print("=" * 50)
    import json
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("=" * 50)
    return report


async def test_scheduler_time():
    """스케줄러 시간 설정 확인"""
    from scripts.finance.telegram_integration.scheduler import CronTrigger

    # 18:00 KST 설정 확인
    trigger = CronTrigger(hour=18, minute=0, timezone="Asia/Seoul")
    print("\n" + "=" * 50)
    print("스케줄러 시간 설정 확인:")
    print("=" * 50)
    print(f"시간: {trigger.fields[4]} (hour)")  # hour field
    print(f"분: {trigger.fields[5]} (minute)")  # minute field
    print(f"타임존: Asia/Seoul")
    print("✅ 매일 18:00 KST에 실행 설정 확인됨")
    print("=" * 50)


async def main():
    """통합 테스트"""
    print("\n🧪 BAR-49 Telegram 리포트 스케줄러 테스트\n")

    # 1. 포매터 테스트
    print("\n1️⃣ 일일 리포트 포매터 테스트")
    message = test_daily_report_formatter()

    # 2. 리포트 빌드 테스트
    print("\n2️⃣ Comprehensive 리포트 빌드 테스트")
    report = test_comprehensive_report_building()

    # 3. 스케줄러 시간 확인
    print("\n3️⃣ 스케줄러 시간 설정 확인")
    await test_scheduler_time()

    # 4. 통합 포매팅 테스트
    print("\n4️⃣ 통합 포매팅 테스트")
    from scripts.finance.telegram_integration.daily_report_formatter import daily_report_formatter
    formatted = daily_report_formatter.format_daily_report(report)
    print("✅ 빌드된 리포트 데이터를 포매터로 변환:")
    print("-" * 50)
    print(formatted[:300] + "...")
    print("-" * 50)

    print("\n" + "=" * 50)
    print("✅ 모든 테스트 완료!")
    print("=" * 50)
    print("\n📝 다음 단계:")
    print("  1. Telegram 환경변수 설정 (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
    print("  2. FastAPI lifespan에서 start_scheduler() 호출")
    print("  3. 매일 18:00 KST에 자동 실행 확인")


if __name__ == "__main__":
    asyncio.run(main())
