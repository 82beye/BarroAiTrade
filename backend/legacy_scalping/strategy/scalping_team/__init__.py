"""
스캘핑 타이밍 전문가 팀 에이전트 (10인)

당일 상승률 높은 종목을 대상으로
최적의 스캘핑 진입 타이밍을 분석한다.

BAR-41 패치: ai-trade 의 옛 절대 import (`from strategy.scalping_team...`)
는 BarroAiTrade 의 namespace 격리(`backend.legacy_scalping.*`)와 충돌하므로
re-export 를 비활성화. 깊은 경로 import 사용 권장:

    from backend.legacy_scalping.strategy.scalping_team.coordinator import (
        ScalpingCoordinator,
    )

정식 re-export 정리는 후속 BAR (BAR-43/BAR-50) 에서.
"""
