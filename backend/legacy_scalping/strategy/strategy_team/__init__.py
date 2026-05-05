"""
전략 최적화 팀 에이전트

5개 전문 에이전트가 매매 기록·시장 데이터·종목 특성을 분석하여
당일 매매 전략 파라미터를 동적으로 최적화한다.

에이전트 구성:
  - TradePatternAgent  : 과거 매매 패턴 학습 → 반복 실수 차단
  - EntryTimingAgent   : 시간대별 승률 분석 → 최적 진입 시간대 도출
  - RiskRewardAgent    : 종목별 리스크/리워드 프로파일 평가
  - SizingAgent        : 변동성·신뢰도 기반 동적 포지션 사이징
  - ExitOptimizer      : 익절/손절 기준 동적 조정

사용:
  coordinator = StrategyCoordinator(config)
  params = coordinator.optimize(trade_log_path, watchlist, cache_data)
  # → params를 IntradayFilter, EntrySignalGenerator에 주입
"""

# BAR-41 패치: ai-trade 의 옛 절대 import 는 namespace 격리와 충돌해 비활성화.
# 깊은 경로 사용:
#   from backend.legacy_scalping.strategy.strategy_team.coordinator import (
#       StrategyCoordinator, StrategyParams,
#   )
