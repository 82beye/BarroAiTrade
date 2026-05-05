"""
스캘핑 백테스트 검증 팀 코디네이터

5명의 검증 에이전트가 어제 전체 종목 캔들 데이터를 기반으로
스캘핑 로직의 실효성을 검증한다.

에이전트 구성:
  1. EntryAccuracyAgent  - 진입 정확도 검증 (진입가 대비 고점/저점 도달)
  2. ExitEfficiencyAgent - 청산 효율 검증 (TP/SL 도달률, 최적 수익)
  3. TimingQualityAgent  - 타이밍 품질 검증 (즉시 vs 대기 vs 관망 정확도)
  4. ScoreCalibrationAgent - 점수 교정 검증 (점수 ↔ 실제 수익률 상관)
  5. PnLSimulationAgent  - 손익 시뮬레이션 (어제 실전 가동 시 총 P&L)
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from scanner.ohlcv_cache import OHLCVCache
from strategy.scalping_team.base_agent import (
    ScalpingAnalysis, StockSnapshot,
)
from strategy.scalping_team.coordinator import ScalpingCoordinator

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 데이터 구조
# ─────────────────────────────────────────────────────────────

@dataclass
class CandleData:
    """단일 종목의 어제 캔들 + 메타"""
    code: str
    name: str
    open: float
    high: float
    low: float
    close: float
    prev_close: float
    volume: int
    change_pct: float
    trade_value: float
    volume_ratio: float   # 거래량 / 20일 평균


@dataclass
class SimulatedTrade:
    """시뮬레이션 매매 1건"""
    code: str
    name: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    exit_reason: str       # "TP", "SL", "강제청산"
    scalping_score: float
    timing: str
    scalp_tp_pct: float
    scalp_sl_pct: float
    max_profit_pct: float  # 장중 최대 수익
    max_loss_pct: float    # 장중 최대 손실


@dataclass
class AgentVerdict:
    """개별 검증 에이전트 판정"""
    agent_name: str
    grade: str             # A/B/C/D/F
    score: float           # 0~100
    findings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


@dataclass
class VerificationReport:
    """검증 팀 종합 리포트"""
    target_date: str
    total_candidates: int
    analyzed_stocks: int
    simulated_trades: int
    # 종합
    overall_grade: str
    overall_score: float
    total_pnl_pct: float
    win_rate: float
    profit_factor: float
    # 에이전트별 판정
    verdicts: List[AgentVerdict] = field(default_factory=list)
    # 시뮬레이션 상세
    trades: List[SimulatedTrade] = field(default_factory=list)
    # 최종 권고
    recommendations: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────
# 검증 에이전트 #1: 진입 정확도
# ─────────────────────────────────────────────────────────────

class EntryAccuracyAgent:
    """
    진입 정확도 검증

    스캘핑 팀이 "즉시 진입" 판정한 종목의 진입가 대비
    장중 고점/저점 도달률을 검증한다.
    """
    NAME = "진입정확도검증"

    def verify(
        self,
        analyses: List[ScalpingAnalysis],
        candles: Dict[str, CandleData],
    ) -> AgentVerdict:
        verdict = AgentVerdict(agent_name=self.NAME, grade="", score=0.0)

        if not analyses:
            verdict.grade = "N/A"
            verdict.findings.append("분석 대상 없음")
            return verdict

        total = 0
        profitable_entries = 0
        entry_to_high_pcts = []
        entry_to_low_pcts = []

        for a in analyses:
            candle = candles.get(a.code)
            if not candle:
                continue
            total += 1

            entry = a.optimal_entry_price or candle.open
            if entry <= 0:
                continue

            # 진입가 대비 고점까지 = 최대 잠재 수익
            max_up = (candle.high - entry) / entry * 100
            # 진입가 대비 저점까지 = 최대 잠재 손실
            max_down = (candle.low - entry) / entry * 100

            entry_to_high_pcts.append(max_up)
            entry_to_low_pcts.append(max_down)

            # 진입 후 +1% 이상 수익 가능했으면 성공
            if max_up >= 1.0:
                profitable_entries += 1

        if total == 0:
            verdict.grade = "N/A"
            verdict.findings.append("유효 캔들 데이터 없음")
            return verdict

        accuracy = profitable_entries / total * 100
        avg_max_up = np.mean(entry_to_high_pcts)
        avg_max_down = np.mean(entry_to_low_pcts)

        verdict.score = min(accuracy, 100)
        verdict.grade = self._grade(accuracy)

        verdict.findings.append(
            f"진입 후 +1% 도달률: {profitable_entries}/{total} "
            f"({accuracy:.0f}%)")
        verdict.findings.append(
            f"평균 최대수익: +{avg_max_up:.1f}% | "
            f"평균 최대손실: {avg_max_down:.1f}%")

        if accuracy < 50:
            verdict.recommendations.append(
                "진입 정확도 50% 미만: 진입 필터 강화 또는 "
                "min_score 상향 필요")
        if avg_max_down < -3.0:
            verdict.recommendations.append(
                f"평균 최대손실 {avg_max_down:.1f}%: "
                f"손절폭 확대 또는 진입 타이밍 개선 필요")

        return verdict

    @staticmethod
    def _grade(accuracy: float) -> str:
        if accuracy >= 80:
            return "A"
        if accuracy >= 60:
            return "B"
        if accuracy >= 40:
            return "C"
        if accuracy >= 20:
            return "D"
        return "F"


# ─────────────────────────────────────────────────────────────
# 검증 에이전트 #2: 청산 효율
# ─────────────────────────────────────────────────────────────

class ExitEfficiencyAgent:
    """
    청산 효율 검증

    설정된 TP/SL 대비 일봉 고가/저가를 비교하여
    익절/손절 도달 가능성과 최적 TP/SL을 분석한다.
    """
    NAME = "청산효율검증"

    def verify(
        self,
        analyses: List[ScalpingAnalysis],
        candles: Dict[str, CandleData],
    ) -> AgentVerdict:
        verdict = AgentVerdict(agent_name=self.NAME, grade="", score=0.0)

        tp_reached = 0
        sl_reached = 0
        both_reached = 0
        total = 0
        optimal_tps = []  # 실현 가능했던 최적 TP

        for a in analyses:
            candle = candles.get(a.code)
            if not candle:
                continue
            total += 1

            entry = a.optimal_entry_price or candle.open
            if entry <= 0:
                continue

            tp_target = entry * (1 + a.scalp_tp_pct / 100)
            sl_target = entry * (1 + a.scalp_sl_pct / 100)

            hit_tp = candle.high >= tp_target
            hit_sl = candle.low <= sl_target

            if hit_tp:
                tp_reached += 1
            if hit_sl:
                sl_reached += 1
            if hit_tp and hit_sl:
                both_reached += 1

            # 실현 가능한 최적 TP (고점 기준)
            max_tp = (candle.high - entry) / entry * 100
            if max_tp > 0:
                optimal_tps.append(max_tp)

        if total == 0:
            verdict.grade = "N/A"
            verdict.findings.append("유효 데이터 없음")
            return verdict

        tp_rate = tp_reached / total * 100
        sl_rate = sl_reached / total * 100
        avg_optimal_tp = np.mean(optimal_tps) if optimal_tps else 0

        # 점수: TP 도달률이 높고 SL 도달률이 낮을수록 좋음
        verdict.score = min(max(tp_rate - sl_rate / 2, 0), 100)
        verdict.grade = self._grade(verdict.score)

        verdict.findings.append(
            f"TP 도달: {tp_reached}/{total} ({tp_rate:.0f}%)")
        verdict.findings.append(
            f"SL 도달: {sl_reached}/{total} ({sl_rate:.0f}%)")
        verdict.findings.append(
            f"TP+SL 동시: {both_reached}/{total} (순서 미확인)")
        verdict.findings.append(
            f"실현 가능 평균 최대 TP: +{avg_optimal_tp:.1f}%")

        if tp_rate < 30:
            verdict.recommendations.append(
                f"TP 도달률 {tp_rate:.0f}% 저조: "
                f"TP +{avg_optimal_tp:.1f}% (실현 가능 평균)으로 하향 고려")
        if sl_rate > 60:
            verdict.recommendations.append(
                f"SL 도달률 {sl_rate:.0f}% 과다: "
                f"SL 확대 또는 진입 타이밍 개선 필요")

        return verdict

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 60:
            return "A"
        if score >= 40:
            return "B"
        if score >= 20:
            return "C"
        if score >= 0:
            return "D"
        return "F"


# ─────────────────────────────────────────────────────────────
# 검증 에이전트 #3: 타이밍 품질
# ─────────────────────────────────────────────────────────────

class TimingQualityAgent:
    """
    타이밍 품질 검증

    "즉시" 판정 종목 vs "관망" 판정 종목의 실제 수익률을
    비교하여 타이밍 판단의 정확도를 검증한다.
    """
    NAME = "타이밍품질검증"

    def verify(
        self,
        analyses: List[ScalpingAnalysis],
        candles: Dict[str, CandleData],
    ) -> AgentVerdict:
        verdict = AgentVerdict(agent_name=self.NAME, grade="", score=0.0)

        # 타이밍별 그룹
        timing_groups: Dict[str, List[float]] = {
            '즉시': [], '대기': [], '눌림목대기': [], '관망': [],
        }

        for a in analyses:
            candle = candles.get(a.code)
            if not candle:
                continue

            entry = a.optimal_entry_price or candle.open
            if entry <= 0:
                continue

            # 시가 매수 → 종가 청산 기준 수익률
            pnl = (candle.close - entry) / entry * 100
            timing = a.timing or '관망'
            if timing in timing_groups:
                timing_groups[timing].append(pnl)

        # 분석
        group_stats = {}
        for timing, pnls in timing_groups.items():
            if pnls:
                group_stats[timing] = {
                    'count': len(pnls),
                    'avg_pnl': np.mean(pnls),
                    'win_rate': sum(1 for p in pnls if p > 0) / len(pnls) * 100,
                }

        verdict.findings.append("타이밍별 실제 성과:")
        for timing in ['즉시', '대기', '눌림목대기', '관망']:
            if timing in group_stats:
                s = group_stats[timing]
                verdict.findings.append(
                    f"  {timing}: {s['count']}종목 | "
                    f"평균PnL {s['avg_pnl']:+.1f}% | "
                    f"승률 {s['win_rate']:.0f}%")

        # 점수: "즉시"가 "관망"보다 수익률이 높으면 타이밍 정확
        imm = group_stats.get('즉시', {})
        obs = group_stats.get('관망', {})

        imm_avg = imm.get('avg_pnl', 0) if imm else 0
        obs_avg = obs.get('avg_pnl', 0) if obs else 0

        if imm and obs:
            diff = imm_avg - obs_avg
            verdict.score = min(max(50 + diff * 10, 0), 100)
            if diff > 0:
                verdict.findings.append(
                    f"즉시({imm_avg:+.1f}%) > 관망({obs_avg:+.1f}%): "
                    f"타이밍 판단 유효 (+{diff:.1f}%p)")
            else:
                verdict.findings.append(
                    f"즉시({imm_avg:+.1f}%) <= 관망({obs_avg:+.1f}%): "
                    f"타이밍 판단 역전 ({diff:+.1f}%p)")
        elif imm:
            verdict.score = min(max(50 + imm_avg * 10, 0), 100)
        else:
            verdict.score = 30

        verdict.grade = self._grade(verdict.score)

        if imm_avg < 0:
            verdict.recommendations.append(
                f"즉시 진입 평균 PnL {imm_avg:+.1f}%: "
                f"진입 기준 강화 필요")

        return verdict

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 70:
            return "A"
        if score >= 50:
            return "B"
        if score >= 30:
            return "C"
        if score >= 15:
            return "D"
        return "F"


# ─────────────────────────────────────────────────────────────
# 검증 에이전트 #4: 점수 교정
# ─────────────────────────────────────────────────────────────

class ScoreCalibrationAgent:
    """
    점수 교정 검증

    스캘핑 점수 ↔ 실제 일봉 수익률의 상관관계를 분석하여
    점수 시스템의 예측력을 검증한다.
    """
    NAME = "점수교정검증"

    def verify(
        self,
        analyses: List[ScalpingAnalysis],
        candles: Dict[str, CandleData],
    ) -> AgentVerdict:
        verdict = AgentVerdict(agent_name=self.NAME, grade="", score=0.0)

        scores = []
        pnls = []

        for a in analyses:
            candle = candles.get(a.code)
            if not candle:
                continue

            entry = a.optimal_entry_price or candle.open
            if entry <= 0:
                continue

            pnl = (candle.close - entry) / entry * 100
            scores.append(a.total_score)
            pnls.append(pnl)

        if len(scores) < 5:
            verdict.grade = "N/A"
            verdict.findings.append(f"데이터 부족 ({len(scores)}종목)")
            return verdict

        # 상관계수
        corr = float(np.corrcoef(scores, pnls)[0, 1])

        # 점수 구간별 승률
        high_score = [(s, p) for s, p in zip(scores, pnls) if s >= 60]
        mid_score = [(s, p) for s, p in zip(scores, pnls) if 40 <= s < 60]
        low_score = [(s, p) for s, p in zip(scores, pnls) if s < 40]

        verdict.findings.append(f"점수-수익률 상관계수: {corr:.3f}")

        for label, group in [("60+", high_score), ("40-59", mid_score), ("<40", low_score)]:
            if group:
                avg_p = np.mean([p for _, p in group])
                wr = sum(1 for _, p in group if p > 0) / len(group) * 100
                verdict.findings.append(
                    f"  점수 {label}: {len(group)}종목 | "
                    f"평균PnL {avg_p:+.1f}% | 승률 {wr:.0f}%")

        # 점수: 상관계수 기반
        verdict.score = min(max((corr + 0.3) / 0.6 * 100, 0), 100)
        verdict.grade = self._grade(verdict.score)

        if corr < 0:
            verdict.recommendations.append(
                f"점수-수익률 음의 상관({corr:.2f}): "
                f"스캘핑 에이전트 가중치 재조정 필요")
        elif corr < 0.2:
            verdict.recommendations.append(
                f"점수-수익률 약한 상관({corr:.2f}): "
                f"추가 시그널 보강 고려")

        return verdict

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 70:
            return "A"
        if score >= 50:
            return "B"
        if score >= 30:
            return "C"
        if score >= 15:
            return "D"
        return "F"


# ─────────────────────────────────────────────────────────────
# 검증 에이전트 #5: 손익 시뮬레이션
# ─────────────────────────────────────────────────────────────

class PnLSimulationAgent:
    """
    손익 시뮬레이션

    어제 스캘핑 로직이 정상 가동되었다면
    어떤 매매가 발생하고 총 P&L이 얼마였을지 시뮬레이션한다.

    가정:
      - 시가에 매수 (최적 진입가 또는 시가)
      - 장중 고가/저가 순서는 '먼저 SL 체크, 그 다음 TP 체크' (보수적)
      - SL/TP 모두 미도달 시 종가에 강제청산
      - 최대 5종목 동시 보유, 종목당 500만원
    """
    NAME = "손익시뮬레이션"

    def verify(
        self,
        analyses: List[ScalpingAnalysis],
        candles: Dict[str, CandleData],
        min_score: float = 60,
        required_timing: str = "즉시",
        max_positions: int = 5,
        amount_per_stock: float = 5_000_000,
    ) -> Tuple[AgentVerdict, List[SimulatedTrade]]:
        verdict = AgentVerdict(agent_name=self.NAME, grade="", score=0.0)
        trades: List[SimulatedTrade] = []

        # 진입 대상 필터
        eligible = [
            a for a in analyses
            if a.total_score >= min_score and a.timing == required_timing
        ]
        # 점수 높은 순서로 최대 max_positions
        eligible.sort(key=lambda a: a.total_score, reverse=True)
        eligible = eligible[:max_positions]

        if not eligible:
            verdict.grade = "N/A"
            verdict.findings.append(
                f"진입 조건 충족 종목 없음 "
                f"(score >= {min_score} & timing = {required_timing})")

            # 차선 후보 분석
            all_immediate = [a for a in analyses if a.timing == required_timing]
            if all_immediate:
                best = max(all_immediate, key=lambda a: a.total_score)
                verdict.findings.append(
                    f"최고 점수 '즉시' 종목: [{best.code}] {best.name} "
                    f"{best.total_score:.0f}점")
            return verdict, trades

        verdict.findings.append(
            f"진입 대상: {len(eligible)}종목 (score >= {min_score})")

        total_pnl = 0.0
        wins = 0
        losses = 0

        for a in eligible:
            candle = candles.get(a.code)
            if not candle:
                continue

            entry = a.optimal_entry_price or candle.open
            if entry <= 0:
                continue

            tp_price = entry * (1 + a.scalp_tp_pct / 100)
            sl_price = entry * (1 + a.scalp_sl_pct / 100)

            max_profit = (candle.high - entry) / entry * 100
            max_loss = (candle.low - entry) / entry * 100

            # 보수적 시뮬레이션: SL 먼저 체크
            if candle.low <= sl_price:
                # SL 도달
                exit_price = sl_price
                exit_reason = "SL"
            elif candle.high >= tp_price:
                # TP 도달
                exit_price = tp_price
                exit_reason = "TP"
            else:
                # 미도달 → 종가 청산
                exit_price = candle.close
                exit_reason = "강제청산"

            pnl_pct = (exit_price - entry) / entry * 100

            trade = SimulatedTrade(
                code=a.code,
                name=a.name,
                entry_price=entry,
                exit_price=exit_price,
                pnl_pct=round(pnl_pct, 2),
                exit_reason=exit_reason,
                scalping_score=a.total_score,
                timing=a.timing,
                scalp_tp_pct=a.scalp_tp_pct,
                scalp_sl_pct=a.scalp_sl_pct,
                max_profit_pct=round(max_profit, 2),
                max_loss_pct=round(max_loss, 2),
            )
            trades.append(trade)
            total_pnl += pnl_pct

            if pnl_pct > 0:
                wins += 1
            else:
                losses += 1

        # 결과 정리
        total_trades = len(trades)
        if total_trades > 0:
            win_rate = wins / total_trades * 100
            avg_win = np.mean([t.pnl_pct for t in trades if t.pnl_pct > 0]) if wins > 0 else 0
            avg_loss = np.mean([t.pnl_pct for t in trades if t.pnl_pct <= 0]) if losses > 0 else 0

            verdict.findings.append(
                f"총 매매: {total_trades}건 | 승: {wins} | 패: {losses}")
            verdict.findings.append(
                f"승률: {win_rate:.0f}%")
            verdict.findings.append(
                f"총 P&L: {total_pnl:+.2f}%")
            verdict.findings.append(
                f"평균 이익: {avg_win:+.1f}% | 평균 손실: {avg_loss:+.1f}%")

            for t in trades:
                verdict.findings.append(
                    f"  [{t.code}] {t.name}: "
                    f"{t.entry_price:,.0f}→{t.exit_price:,.0f} "
                    f"({t.pnl_pct:+.1f}% {t.exit_reason}) "
                    f"[점수:{t.scalping_score:.0f}] "
                    f"최대↑+{t.max_profit_pct:.1f}% 최대↓{t.max_loss_pct:.1f}%")

            pnl_amount = amount_per_stock * total_trades * total_pnl / 100
            verdict.findings.append(
                f"\n예상 수익금: {pnl_amount:+,.0f}원 "
                f"(종목당 {amount_per_stock/10000:.0f}만원 × {total_trades}종목)")

            verdict.score = min(max(50 + total_pnl * 10, 0), 100)
        else:
            verdict.score = 0

        verdict.grade = self._grade(verdict.score)

        if total_pnl < 0:
            verdict.recommendations.append(
                f"시뮬레이션 총손실 {total_pnl:+.1f}%: "
                f"스캘핑 진입 조건 재검토 필요")

        return verdict, trades

    @staticmethod
    def _grade(score: float) -> str:
        if score >= 70:
            return "A"
        if score >= 50:
            return "B"
        if score >= 30:
            return "C"
        if score >= 15:
            return "D"
        return "F"


# ─────────────────────────────────────────────────────────────
# 코디네이터
# ─────────────────────────────────────────────────────────────

class VerificationCoordinator:
    """
    스캘핑 백테스트 검증 팀 코디네이터

    5명의 검증 에이전트를 순차 실행하고
    종합 리포트를 생성한다.
    """

    def __init__(self, config: dict):
        self.config = config
        self._cache_dir = config.get(
            'scanner', {}).get('cache_dir', './data/ohlcv_cache')
        self.cache = OHLCVCache(self._cache_dir)

        self.agents = [
            EntryAccuracyAgent(),
            ExitEfficiencyAgent(),
            TimingQualityAgent(),
            ScoreCalibrationAgent(),
        ]
        self.pnl_agent = PnLSimulationAgent()

    def run(
        self,
        target_date: str = None,
        min_change_pct: float = 2.0,
        min_trade_value: float = 1_000_000_000,
    ) -> VerificationReport:
        """
        검증 실행

        Args:
            target_date: 검증 대상일 (기본: 어제)
            min_change_pct: 주도주 최소 상승률
            min_trade_value: 주도주 최소 거래대금
        """
        if target_date is None:
            from datetime import timedelta
            target_date = (date.today() - timedelta(days=1)).isoformat()

        logger.info("=" * 60)
        logger.info(f"스캘핑 백테스트 검증 시작 ({target_date})")
        logger.info("=" * 60)

        # ── 1. 어제 캔들 데이터 로드 ──
        candles, all_candles = self._load_candles(
            target_date, min_change_pct, min_trade_value)
        logger.info(f"주도주 후보: {len(candles)}종목 (전체 {len(all_candles)}종목)")

        if not candles:
            logger.warning("주도주 후보 없음 — 검증 중단")
            return VerificationReport(
                target_date=target_date,
                total_candidates=0, analyzed_stocks=0,
                simulated_trades=0, overall_grade="N/A",
                overall_score=0, total_pnl_pct=0,
                win_rate=0, profit_factor=0,
            )

        # ── 2. StockSnapshot 생성 + 스캘핑 팀 분석 ──
        snapshots = self._build_snapshots(candles)
        scalping_coord = ScalpingCoordinator(self.config)
        analyses = scalping_coord.analyze(snapshots)
        logger.info(f"스캘핑 팀 분석 완료: {len(analyses)}종목")

        # ── 3. 5명 검증 에이전트 실행 ──
        verdicts: List[AgentVerdict] = []

        for agent in self.agents:
            try:
                v = agent.verify(analyses, candles)
                verdicts.append(v)
                logger.info(
                    f"  [{v.agent_name}] {v.grade} ({v.score:.0f}점)")
            except Exception as e:
                logger.error(f"  [{agent.NAME}] 오류: {e}", exc_info=True)

        # PnL 시뮬레이션 (별도 — trades 반환)
        scalp_config = self.config.get('strategy', {}).get('scalping', {})
        pnl_verdict, sim_trades = self.pnl_agent.verify(
            analyses, candles,
            min_score=scalp_config.get('min_score', 60),
            required_timing=scalp_config.get('required_timing', '즉시'),
        )
        verdicts.append(pnl_verdict)
        logger.info(
            f"  [{pnl_verdict.agent_name}] {pnl_verdict.grade} "
            f"({pnl_verdict.score:.0f}점)")

        # ── 4. 종합 리포트 생성 ──
        report = self._build_report(
            target_date, candles, analyses, verdicts, sim_trades)

        logger.info("=" * 60)
        logger.info(
            f"검증 완료: {report.overall_grade} "
            f"({report.overall_score:.0f}점) | "
            f"P&L: {report.total_pnl_pct:+.2f}%")
        logger.info("=" * 60)

        return report

    def _load_candles(
        self,
        target_date: str,
        min_change_pct: float,
        min_trade_value: float,
    ) -> Tuple[Dict[str, CandleData], Dict[str, CandleData]]:
        """어제 캔들 데이터 로드 + 주도주 후보 필터"""
        import glob

        all_candles: Dict[str, CandleData] = {}
        leading_candles: Dict[str, CandleData] = {}

        files = glob.glob(f'{self._cache_dir}/*.json')
        for fpath in files:
            code = fpath.split('/')[-1].replace('.json', '')
            if code == 'meta':
                continue

            df = self.cache.load(code)
            if df is None or len(df) < 21:
                continue

            # 대상일 데이터 확인
            last_date_str = str(df.index[-1])[:10] if hasattr(
                df.index[-1], 'date') else str(df.iloc[-1].get('date', ''))[:10]
            if target_date not in last_date_str:
                continue

            close = df['close'].values
            volume = df['volume'].values
            open_vals = df['open'].values
            high = df['high'].values
            low = df['low'].values

            prev_close = close[-2] if len(close) >= 2 else close[-1]
            if prev_close <= 0:
                continue

            change_pct = (close[-1] - prev_close) / prev_close * 100
            trade_value = float(close[-1] * volume[-1])

            # 20일 평균 거래량
            vol_20d = volume[-21:-1].astype(float)
            avg_vol = float(np.mean(vol_20d)) if len(vol_20d) > 0 else 1

            candle = CandleData(
                code=code,
                name=code,  # 이름은 나중에 업데이트
                open=float(open_vals[-1]),
                high=float(high[-1]),
                low=float(low[-1]),
                close=float(close[-1]),
                prev_close=float(prev_close),
                volume=int(volume[-1]),
                change_pct=round(change_pct, 2),
                trade_value=trade_value,
                volume_ratio=round(volume[-1] / avg_vol, 2) if avg_vol > 0 else 0,
            )

            all_candles[code] = candle

            if change_pct >= min_change_pct and trade_value >= min_trade_value:
                leading_candles[code] = candle

        return leading_candles, all_candles

    def _build_snapshots(
        self, candles: Dict[str, CandleData],
    ) -> List[StockSnapshot]:
        """CandleData → StockSnapshot 변환"""
        snapshots = []
        for code, c in candles.items():
            snapshots.append(StockSnapshot(
                code=code,
                name=c.name,
                price=c.close,
                open=c.open,
                high=c.high,
                low=c.low,
                prev_close=c.prev_close,
                volume=c.volume,
                change_pct=c.change_pct,
                trade_value=c.trade_value,
                volume_ratio=c.volume_ratio,
                category=self._classify(c),
                score=0,
            ))
        return snapshots

    @staticmethod
    def _classify(c: CandleData) -> str:
        if c.change_pct >= 15:
            return "급등주"
        if c.volume_ratio >= 5 and c.change_pct >= 5:
            return "거래폭증"
        if c.change_pct >= 8:
            return "강세주"
        return "상승주"

    def _build_report(
        self,
        target_date: str,
        candles: Dict[str, CandleData],
        analyses: List[ScalpingAnalysis],
        verdicts: List[AgentVerdict],
        trades: List[SimulatedTrade],
    ) -> VerificationReport:
        """종합 리포트 생성"""
        # P&L 계산
        total_pnl = sum(t.pnl_pct for t in trades)
        wins = sum(1 for t in trades if t.pnl_pct > 0)
        losses = sum(1 for t in trades if t.pnl_pct <= 0)
        win_rate = wins / len(trades) * 100 if trades else 0

        avg_win = np.mean(
            [t.pnl_pct for t in trades if t.pnl_pct > 0]) if wins > 0 else 0
        avg_loss = abs(np.mean(
            [t.pnl_pct for t in trades if t.pnl_pct <= 0])) if losses > 0 else 1
        profit_factor = avg_win / avg_loss if avg_loss > 0 else 0

        # 종합 점수 (에이전트 평균)
        valid_scores = [v.score for v in verdicts if v.grade != "N/A"]
        overall_score = np.mean(valid_scores) if valid_scores else 0

        # 종합 등급
        if overall_score >= 70:
            overall_grade = "A"
        elif overall_score >= 50:
            overall_grade = "B"
        elif overall_score >= 30:
            overall_grade = "C"
        elif overall_score >= 15:
            overall_grade = "D"
        else:
            overall_grade = "F"

        # 종합 권고
        recommendations = []
        for v in verdicts:
            recommendations.extend(v.recommendations)

        return VerificationReport(
            target_date=target_date,
            total_candidates=len(candles),
            analyzed_stocks=len(analyses),
            simulated_trades=len(trades),
            overall_grade=overall_grade,
            overall_score=round(overall_score, 1),
            total_pnl_pct=round(total_pnl, 2),
            win_rate=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
            verdicts=verdicts,
            trades=trades,
            recommendations=recommendations,
        )

    @staticmethod
    def format_report(report: VerificationReport) -> str:
        """콘솔 출력용 리포트 포맷"""
        lines = [
            "=" * 70,
            f"  스캘핑 백테스트 검증 리포트 ({report.target_date})",
            "=" * 70,
            "",
            f"  종합 등급: {report.overall_grade} ({report.overall_score:.0f}점)",
            f"  주도주 후보: {report.total_candidates}종목",
            f"  스캘핑 분석: {report.analyzed_stocks}종목",
            f"  시뮬레이션: {report.simulated_trades}건",
            "",
            f"  총 P&L: {report.total_pnl_pct:+.2f}%",
            f"  승률: {report.win_rate:.0f}%",
            f"  손익비: {report.profit_factor:.2f}",
            "",
            "-" * 70,
            "  검증 에이전트별 판정",
            "-" * 70,
        ]

        for v in report.verdicts:
            lines.append(
                f"\n  [{v.agent_name}] 등급: {v.grade} ({v.score:.0f}점)")
            for f in v.findings:
                lines.append(f"    {f}")

        if report.recommendations:
            lines.append("")
            lines.append("-" * 70)
            lines.append("  종합 권고사항")
            lines.append("-" * 70)
            for r in report.recommendations:
                lines.append(f"    - {r}")

        lines.append("")
        lines.append("=" * 70)
        return "\n".join(lines)
