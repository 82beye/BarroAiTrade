"""
팀 에이전트 기반 상승 예측 시스템

4개의 전문 에이전트가 독립적으로 OHLCV 캐시를 분석하고
Coordinator가 가중 합산하여 최종 상승 예측 순위를 산출

사용:
    python main.py --predict              # 장 시작 전 상승 예측 (상위 20)
    python main.py --predict --top 30     # 상위 30종목
"""

from scanner.agents.coordinator import PredictionCoordinator

__all__ = ["PredictionCoordinator"]
