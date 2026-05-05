"""
에이전트 기본 클래스

모든 전문 에이전트는 BaseAgent를 상속하여
analyze_stock()에서 종목별 점수를 반환
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class AgentSignal:
    """개별 에이전트의 종목 분석 결과"""
    code: str
    name: str
    score: float              # 0~100 정규화 점수
    confidence: float         # 0~1 신뢰도 (데이터 충분성)
    reasons: List[str] = field(default_factory=list)


class BaseAgent(ABC):
    """에이전트 기본 클래스"""

    # 서브클래스에서 오버라이드
    AGENT_NAME: str = "base"
    MIN_DATA_LENGTH: int = 20

    def analyze_universe(
        self,
        universe: List[dict],
        cache_data: Dict[str, pd.DataFrame],
    ) -> Dict[str, AgentSignal]:
        """
        전체 유니버스를 분석하여 종목별 신호를 반환

        Args:
            universe: [{"code": "005930", "name": "삼성전자"}, ...]
            cache_data: {code: DataFrame} 사전 로드된 캐시

        Returns:
            {code: AgentSignal}
        """
        signals = {}

        for stock in universe:
            code = stock['code']
            name = stock.get('name', '')
            df = cache_data.get(code)

            if df is None or len(df) < self.MIN_DATA_LENGTH:
                continue

            try:
                signal = self.analyze_stock(code, name, df)
                if signal and signal.score > 0:
                    signals[code] = signal
            except Exception as e:
                logger.debug(f"[{self.AGENT_NAME}] {code} 분석 오류: {e}")

        logger.info(
            f"[{self.AGENT_NAME}] 분석 완료: "
            f"{len(signals)}/{len(universe)}종목 신호 생성"
        )
        return signals

    @abstractmethod
    def analyze_stock(
        self, code: str, name: str, df: pd.DataFrame,
    ) -> Optional[AgentSignal]:
        """단일 종목 분석 (서브클래스에서 구현)"""
        ...
