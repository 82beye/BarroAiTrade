"""
전략 엔진 패키지

지원 전략:
  - f_zone           : F존 매매 (급등 후 눌림목 반등)
  - sf_zone          : SF존/슈퍼존 매매 (F존 강화 버전)
  - blue_line        : 블루라인 매매 (이평선 돌파)
  - stock_strategy   : 파란점선 + 수박 통합 한국 주식 전략 (수박)
  - crypto_breakout  : 암호화폐 돌파 매매

백테스팅:
  - StrategyBacktester / SyntheticDataLoader / run_multi_strategy_backtest

BAR-45 패치: package __init__ 의 re-export 를 제거 — Python 3.14 + numpy 의
"cannot load module more than once" 충돌 회피. 모든 import 는 sub-module
직접 경로 사용:

    from backend.core.strategy.f_zone import FZoneStrategy
    from backend.core.strategy.backtester import StrategyBacktester
"""
