"""
전략 에이전트 기본 클래스

예측 에이전트(scanner/agents)와 달리 전략 에이전트는
매매 기록(trades.jsonl)과 OHLCV 캐시를 함께 분석하여
'전략 파라미터 조정 권고'를 반환한다.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class StrategySignal:
    """개별 전략 에이전트의 분석 결과"""
    agent_name: str
    confidence: float                   # 0~1 신뢰도
    reasons: List[str] = field(default_factory=list)

    # 파라미터 조정 권고 (None이면 해당 항목 미변경)
    cooldown_minutes: Optional[int] = None
    max_entries_per_stock: Optional[int] = None
    max_bb_excess_pct: Optional[float] = None
    max_breakout_pct: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_1_pct: Optional[float] = None
    take_profit_2_pct: Optional[float] = None
    breakeven_buffer_pct: Optional[float] = None
    position_size_multiplier: Optional[float] = None
    entry_start_delay_minutes: Optional[int] = None

    # 종목별 개별 조정 {code: multiplier}
    stock_boost: Dict[str, float] = field(default_factory=dict)
    stock_penalty: Dict[str, float] = field(default_factory=dict)
    # 매수 금지 종목
    blacklist_codes: List[str] = field(default_factory=list)


@dataclass
class TradeRecord:
    """매매 기록 (분석 입력용)"""
    action: str
    code: str
    name: str
    qty: int
    price: float
    timestamp: str          # ISO format
    amount: int = 0
    entry_price: float = 0.0
    pnl_pct: float = 0.0
    exit_type: str = ""
    reason: str = ""
    daily_pnl_pct: float = 0.0


class BaseStrategyAgent(ABC):
    """전략 에이전트 기본 클래스"""

    AGENT_NAME: str = "base"

    @abstractmethod
    def analyze(
        self,
        trades: List[TradeRecord],
        cache_data: Dict[str, pd.DataFrame],
        watchlist: List[dict],
    ) -> Optional[StrategySignal]:
        """
        분석 실행

        Args:
            trades: 최근 매매 기록 (최대 최근 5일)
            cache_data: OHLCV 캐시 {code: DataFrame}
            watchlist: 오늘의 관심종목 리스트

        Returns:
            StrategySignal 또는 None (분석 불가 시)
        """
        ...
