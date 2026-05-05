"""
팀 에이전트 코디네이터

5개 전문 에이전트의 분석 결과를 가중 합산하여
최종 상승 예측 순위를 산출

에이전트별 가중치:
  - momentum  (25%): 추세 연속성
  - volume    (20%): 거래량은 가격에 선행
  - technical (20%): 기술적 셋업 확인
  - breakout  (15%): 돌파 임박 확인
  - timing    (20%): 진입 타이밍 적합도
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from scanner.agents.base_agent import AgentSignal
from scanner.agents.momentum_agent import MomentumAgent
from scanner.agents.volume_agent import VolumeAgent
from scanner.agents.technical_agent import TechnicalAgent
from scanner.agents.breakout_agent import BreakoutAgent
from scanner.agents.timing_agent import TimingAgent
from scanner.ohlcv_cache import OHLCVCache

logger = logging.getLogger(__name__)


@dataclass
class PredictionResult:
    """최종 상승 예측 결과"""
    rank: int
    code: str
    name: str
    total_score: float          # 가중 합산 점수 (0~100)
    confidence: float           # 종합 신뢰도 (0~1)
    agent_scores: Dict[str, float] = field(default_factory=dict)
    top_reasons: List[str] = field(default_factory=list)
    consensus_level: str = ""   # 합의 수준: 만장일치/다수/소수/단독


class PredictionCoordinator:
    """팀 에이전트 코디네이터"""

    DEFAULT_WEIGHTS = {
        "momentum": 0.25,
        "volume": 0.20,
        "technical": 0.20,
        "breakout": 0.15,
        "timing": 0.20,
    }

    def __init__(self, config: dict):
        self.config = config

        pred_config = config.get('prediction', {})
        self.top_n = pred_config.get('top_n', 20)
        self.min_consensus = pred_config.get('min_consensus_agents', 2)

        # 에이전트별 가중치 (설정에서 오버라이드 가능)
        weight_config = pred_config.get('agent_weights', {})
        self.weights = {
            k: weight_config.get(k, v)
            for k, v in self.DEFAULT_WEIGHTS.items()
        }

        # 에이전트 초기화
        self.agents = {
            "momentum": MomentumAgent(),
            "volume": VolumeAgent(),
            "technical": TechnicalAgent(),
            "breakout": BreakoutAgent(),
            "timing": TimingAgent(),
        }

        cache_dir = config.get('scanner', {}).get('cache_dir', './data/ohlcv_cache')
        self.cache = OHLCVCache(cache_dir)

    def predict(
        self,
        universe: List[dict],
        top_n: Optional[int] = None,
    ) -> List[PredictionResult]:
        """
        팀 에이전트 상승 예측 실행

        Args:
            universe: [{"code": "005930", "name": "삼성전자", ...}, ...]
            top_n: 상위 N개 반환

        Returns:
            점수 내림차순 PredictionResult 리스트
        """
        if top_n is None:
            top_n = self.top_n

        logger.info(f"팀 에이전트 예측 시작 (유니버스: {len(universe)}종목)")

        # ── 1. 캐시 일괄 로드 ──
        cache_data = self._load_cache(universe)
        logger.info(f"캐시 로드 완료: {len(cache_data)}종목")

        # ── 2. 각 에이전트 독립 분석 ──
        all_signals: Dict[str, Dict[str, AgentSignal]] = {}

        for agent_name, agent in self.agents.items():
            signals = agent.analyze_universe(universe, cache_data)
            all_signals[agent_name] = signals

        # ── 3. 가중 합산 + 합의 수준 결정 ──
        results = self._merge_signals(universe, all_signals)
        logger.info(f"팀 에이전트 합산 완료: {len(results)}종목 후보")

        # ── 4. 최소 합의 필터 + 정렬 ──
        filtered = [
            r for r in results
            if self._count_agents(r) >= self.min_consensus
        ]
        filtered.sort(key=lambda r: r.total_score, reverse=True)

        for i, r in enumerate(filtered[:top_n], 1):
            r.rank = i

        logger.info(
            f"팀 에이전트 예측 완료: "
            f"{len(filtered)}종목 (합의 {self.min_consensus}개+ 에이전트)"
        )

        return filtered[:top_n]

    def _load_cache(self, universe: List[dict]) -> Dict[str, pd.DataFrame]:
        """유니버스 전체 캐시 일괄 로드"""
        cache_data = {}
        for stock in universe:
            code = stock['code']
            df = self.cache.load(code)
            if df is not None:
                cache_data[code] = df
        return cache_data

    def _merge_signals(
        self,
        universe: List[dict],
        all_signals: Dict[str, Dict[str, AgentSignal]],
    ) -> List[PredictionResult]:
        """
        각 에이전트 신호를 가중 합산

        점수 = sum(에이전트 점수 * 가중치 * 신뢰도)
        """
        # 모든 종목 코드 수집 (어떤 에이전트에서든 신호가 있는)
        all_codes = set()
        for signals in all_signals.values():
            all_codes.update(signals.keys())

        # 이름 매핑
        name_map = {s['code']: s.get('name', '') for s in universe}

        results = []
        for code in all_codes:
            agent_scores = {}
            all_reasons = []
            total_score = 0.0
            total_confidence = 0.0
            agents_active = 0

            for agent_name, signals in all_signals.items():
                signal = signals.get(code)
                if signal and signal.score > 0:
                    weight = self.weights.get(agent_name, 0)
                    weighted = signal.score * weight * signal.confidence
                    total_score += weighted
                    total_confidence += signal.confidence
                    agent_scores[agent_name] = round(signal.score, 1)
                    agents_active += 1
                    # 각 에이전트 상위 사유 1개만
                    if signal.reasons:
                        all_reasons.append(
                            f"[{agent_name}] {signal.reasons[0]}"
                        )

            if agents_active == 0:
                continue

            avg_confidence = total_confidence / agents_active

            # 합의 수준
            consensus = self._consensus_label(agents_active)

            # 합의 보너스: 더 많은 에이전트가 동의할수록 보너스
            consensus_bonus = 1.0 + (agents_active - 1) * 0.1
            total_score = min(total_score * consensus_bonus, 100.0)

            results.append(PredictionResult(
                rank=0,
                code=code,
                name=name_map.get(code, ''),
                total_score=round(total_score, 2),
                confidence=round(avg_confidence, 2),
                agent_scores=agent_scores,
                top_reasons=all_reasons[:4],
                consensus_level=consensus,
            ))

        return results

    @staticmethod
    def _count_agents(result: PredictionResult) -> int:
        """활성 에이전트 수"""
        return len(result.agent_scores)

    @staticmethod
    def _consensus_label(n: int) -> str:
        if n >= 5:
            return "만장일치"
        if n >= 4:
            return "강한합의"
        if n >= 3:
            return "다수합의"
        if n >= 2:
            return "소수합의"
        return "단독판단"
