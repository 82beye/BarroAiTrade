#!/usr/bin/env python3
"""
BAR-49 Telegram 리포트 포매터 간단 테스트 (의존성 최소)
"""
import sys
sys.path.insert(0, '/Users/beye/workspace/BarroAiTrade')


def test_formatter():
    """리포트 포매터 검증"""
    from scripts.finance.telegram_integration.daily_report_formatter import daily_report_formatter

    # 테스트 1: 정상 리포트
    report1 = {
        "date": "2026-05-16",
        "overall_return": 2.45,
        "daily_pnl": 125000,
        "active_users": 150,
        "avg_return": 1.85,
        "strategies": {
            "blueline": {"return": 3.2, "trades": 45, "winrate": 62.5},
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
            "ai_insight": "기술적 반등 신호 감지",
        },
    }

    msg1 = daily_report_formatter.format_daily_report(report1)
    assert "2026-05-16" in msg1, "날짜 없음"
    assert "토" in msg1, "요일 없음"
    assert "+2.45%" in msg1, "수익률 없음"
    assert "125,000" in msg1, "일일손익 없음"
    assert "150" in msg1, "사용자수 없음"
    assert "블루라인(돌파)" in msg1, "전략 없음"
    assert "트레이더A" in msg1, "TOP 3 없음"
    assert "SK하이닉스 -5%" in msg1, "경고 없음"
    print("✅ Test 1: 정상 리포트 포매팅 성공")

    # 테스트 2: 손실 리포트
    report2 = {
        "date": "2026-05-17",
        "overall_return": -1.5,
        "daily_pnl": -75000,
        "active_users": 120,
        "avg_return": -1.2,
        "strategies": {
            "blueline": {"return": -2.0, "trades": 30, "winrate": 40.0},
            "fzone": {"return": 0.5, "trades": 20, "winrate": 55.0},
        },
        "top_3_users": [],
        "warnings": ["포트폴리오 -5% 이상 손실 경고"],
        "tomorrow_forecast": {},
    }

    msg2 = daily_report_formatter.format_daily_report(report2)
    assert "-1.50%" in msg2, "손실율 표시 오류"
    assert "-75,000" in msg2, "손실액 표시 오류"
    assert "-2.00%" in msg2, "전략 손실률 표시 오류"
    print("✅ Test 2: 손실 리포트 포매팅 성공")

    # 테스트 3: 빈 리포트
    report3 = {
        "date": "2026-05-18",
        "overall_return": 0.0,
        "daily_pnl": 0,
        "active_users": 0,
        "avg_return": 0.0,
        "strategies": {
            "blueline": {"return": 0.0, "trades": 0, "winrate": 0.0},
            "fzone": {"return": 0.0, "trades": 0, "winrate": 0.0},
        },
        "top_3_users": [],
        "warnings": [],
        "tomorrow_forecast": {"watch_stocks": [], "ai_insight": ""},
    }

    msg3 = daily_report_formatter.format_daily_report(report3)
    assert "2026-05-18" in msg3, "빈 리포트 날짜 오류"
    assert "특별한 주의사항 없음" in msg3, "경고 없음 메시지 오류"
    print("✅ Test 3: 빈 리포트 포매팅 성공")

    return True


def test_report_service():
    """리포트 서비스 검증"""
    from backend.core.monitoring.report_service import report_service

    trades = [
        {
            "symbol": "005930",
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
            "entry_price": 100000,
            "exit_price": 98000,
            "quantity": 5,
            "pnl": -10000,
            "pnl_pct": -0.02,
            "exit_time": "2026-05-16T15:00:00",
            "strategy_id": "fzone",
        },
    ]

    report = report_service.build_comprehensive_daily_report(
        trades=trades,
        active_users=100,
    )

    assert report["date"] == "2026-05-16", "날짜 오류"
    assert report["overall_return"] > 0, "수익률 계산 오류"
    assert report["daily_pnl"] == 10000, "일일 손익 계산 오류"
    assert report["active_users"] == 100, "사용자 수 오류"
    assert "blueline" in report["strategies"], "전략 없음"
    assert "fzone" in report["strategies"], "전략 없음"
    assert len(report["top_3_users"]) == 3, "TOP 3 없음"
    print("✅ Test 4: Comprehensive 리포트 빌드 성공")

    # 빈 거래 테스트
    empty_report = report_service.build_comprehensive_daily_report(trades=[], active_users=0)
    assert empty_report["daily_pnl"] == 0, "빈 거래 손익 오류"
    assert empty_report["overall_return"] == 0.0, "빈 거래 수익률 오류"
    print("✅ Test 5: 빈 거래 리포트 성공")

    return True


if __name__ == "__main__":
    print("\n🧪 BAR-49 Telegram 리포트 스케줄러 검증\n")

    try:
        print("포매터 테스트 중...")
        test_formatter()
        print()

        print("리포트 서비스 테스트 중...")
        test_report_service()
        print()

        print("=" * 50)
        print("✅ 모든 검증 통과!")
        print("=" * 50)
        print("\n📋 BAR-49 구현 요약:")
        print("  • 스케줄러: 매일 18:00 KST 실행")
        print("  • 리포트: BAR-44 스펙 준수")
        print("  • 포맷: Telegram Markdown")
        print("  • 포함: 포트폴리오 통계, 전략 성과, TOP 3, 경고, 전망")
        print("=" * 50)

    except AssertionError as e:
        print(f"❌ 검증 실패: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
