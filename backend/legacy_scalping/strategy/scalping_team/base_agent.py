"""
스캘핑 팀 에이전트 베이스 클래스

각 에이전트는 당일 상승 종목의 실시간/과거 데이터를 분석하여
스캘핑 진입 타이밍 시그널을 생성한다.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class StockSnapshot:
    """종목 실시간 스냅샷"""
    code: str
    name: str
    price: float           # 현재가
    open: float            # 시가
    high: float            # 고가
    low: float             # 저가
    prev_close: float      # 전일 종가
    volume: int            # 당일 거래량
    change_pct: float      # 등락률 (%)
    trade_value: float     # 거래대금
    volume_ratio: float    # 거래량 비율 (vs 20일 평균)
    category: str          # "급등주", "거래폭증", "대량매집", "강세주", "상승주"
    score: float           # 주도주 종합 점수 (0-100)


@dataclass
class ScalpingSignal:
    """개별 에이전트의 스캘핑 분석 시그널"""
    agent_name: str
    code: str
    name: str

    # 진입 판단
    entry_score: float = 0.0        # 진입 매력도 (0-100)
    confidence: float = 0.0         # 분석 신뢰도 (0-1)
    timing: str = ""                # "즉시", "대기", "눌림목대기", "관망"
    reasons: List[str] = field(default_factory=list)

    # 진입 조건
    entry_price_zone: Optional[float] = None   # 최적 진입가 (None=현재가)
    entry_trigger: str = ""                     # 진입 트리거 설명

    # 스캘핑 파라미터 제안
    scalp_tp_pct: Optional[float] = None       # 스캘핑 익절 %
    scalp_sl_pct: Optional[float] = None       # 스캘핑 손절 %
    hold_minutes: Optional[int] = None         # 예상 보유 시간 (분)


@dataclass
class ScalpingAnalysis:
    """스캘핑 팀 종합 분석 결과 (종목별)"""
    code: str
    name: str
    rank: int = 0

    # 종합 판단
    total_score: float = 0.0         # 종합 진입 점수 (0-100)
    confidence: float = 0.0          # 종합 신뢰도
    timing: str = ""                 # 최종 타이밍 판단
    consensus_level: str = ""        # "만장일치", "다수합의", "소수합의", "의견분분"

    # 종합 파라미터
    optimal_entry_price: float = 0   # 최적 진입가
    scalp_tp_pct: float = 3.0       # 스캘핑 익절 %
    scalp_sl_pct: float = -3.0      # 스캘핑 손절 % (검증: -1.5→-3.0)
    hold_minutes: int = 15          # 예상 보유 시간

    # 에이전트별 상세
    agent_signals: Dict[str, ScalpingSignal] = field(default_factory=dict)
    top_reasons: List[str] = field(default_factory=list)

    # 급등 유형 (gap_up / intraday / mixed / unknown)
    surge_type: str = 'unknown'

    # 2026-04-07: 변동성 비례 SL/트레일링용 1분봉 ATR
    intraday_atr: float = 0.0

    # 원본 데이터
    snapshot: Optional[StockSnapshot] = None


class BaseScalpingAgent(ABC):
    """스캘핑 분석 에이전트 베이스"""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def analyze(
        self,
        snapshot: StockSnapshot,
        ohlcv: Optional[pd.DataFrame],
        intraday_prices: List[dict],
    ) -> Optional[ScalpingSignal]:
        """
        종목 스캘핑 진입 타이밍 분석

        Args:
            snapshot: 실시간 종목 스냅샷
            ohlcv: 과거 일봉 데이터 (DataFrame: date,open,high,low,close,volume)
            intraday_prices: 당일 분봉/틱 데이터 [{time, price, volume}, ...]

        Returns:
            ScalpingSignal or None (데이터 부족 시)
        """
        ...
