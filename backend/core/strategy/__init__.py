"""
전략 엔진 패키지

지원 전략:
  - f_zone   : F존 매매 (급등 후 눌림목 반등)
  - sf_zone  : SF존/슈퍼존 매매 (F존 강화 버전)
  - blue_line: 블루라인 매매 (이평선 돌파)
  - watermelon: 수박 매매 (거래량 폭발 단타)
  - crypto_breakout: 암호화폐 돌파 매매

백테스팅:
  - StrategyBacktester: 전략 백테스팅 엔진
  - SyntheticDataLoader: 합성 OHLCV 데이터 생성기
  - BacktestReport / BacktestMetrics / BacktestTrade
"""

from backend.core.strategy.f_zone import FZoneStrategy, FZoneParams, FZoneAnalysis
from backend.core.strategy.blue_line import BlueLineStrategy, BlueLineParams
from backend.core.strategy.crypto_breakout import CryptoBreakoutStrategy, CryptoBreakoutParams
from backend.core.strategy.backtester import (
    StrategyBacktester,
    SyntheticDataLoader,
    BacktestReport,
    BacktestMetrics,
    BacktestTrade,
    BacktestConfig,
    ExitParams,
    run_multi_strategy_backtest,
    print_comparison_table,
)

__all__ = [
    # 전략
    "FZoneStrategy",
    "FZoneParams",
    "FZoneAnalysis",
    "BlueLineStrategy",
    "BlueLineParams",
    "CryptoBreakoutStrategy",
    "CryptoBreakoutParams",
    # 백테스팅
    "StrategyBacktester",
    "SyntheticDataLoader",
    "BacktestReport",
    "BacktestMetrics",
    "BacktestTrade",
    "BacktestConfig",
    "ExitParams",
    "run_multi_strategy_backtest",
    "print_comparison_table",
]
