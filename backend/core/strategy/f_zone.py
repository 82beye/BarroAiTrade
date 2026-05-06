"""
F존 매매 전략 (F-Zone Trading Strategy)

원리:
  급등(기준봉) 이후 발생하는 눌림목 구간(F존)에서 진입,
  이동평균선 지지를 확인하고 반등 캔들이 나올 때 매수.
  분할 익절(+3%, +5%)과 고정 손절(-2%)로 관리.

F존 vs SF존(슈퍼존):
  F존  — 기준봉 + 눌림목 + 이평선 지지 확인
  SF존 — F존 조건 + 추가 강도(거래량 재증가, 강한 기준봉, 테마 연속성)

출처: thetrading2021 (서희파더 이재상) 특허 매매기법 기반 구현
참고: https://www.youtube.com/@thetrading2021/videos
      https://cafe.naver.com/thetrading2021
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from decimal import Decimal
from typing import Any, List, Optional, Tuple

import numpy as np
import pandas as pd

from backend.core.strategy.base import Strategy
from backend.models.market import OHLCV, MarketType
from backend.models.position import Position
from backend.models.signal import EntrySignal
from backend.models.strategy import (
    Account,
    AnalysisContext,
    ExitPlan,
    StopLoss,
    TakeProfitTier,
)

logger = logging.getLogger(__name__)

# ── 전략 파라미터 ─────────────────────────────────────────────────────────────

@dataclass
class FZoneParams:
    """F존 전략 파라미터 (기본값은 실전 검증 기준)"""

    # 기준봉 조건
    impulse_min_gain_pct: float = 0.03        # 기준봉 최소 상승률: 3%
    impulse_volume_ratio: float = 2.0         # 기준봉 거래량 배율 (평균 대비): 200%
    impulse_lookback: int = 5                 # 기준봉 탐색 과거 봉 수

    # 눌림목 조건
    pullback_min_pct: float = -0.05           # 눌림 최대 하락: -5%
    pullback_max_pct: float = -0.005          # 눌림 최소 하락: -0.5%
    pullback_volume_ratio: float = 0.7        # 눌림 시 거래량 감소 비율 (기준봉 대비)
    pullback_max_candles: int = 10            # 눌림 최대 허용 봉 수

    # 이동평균선 지지
    ma_periods: List[int] = field(default_factory=lambda: [5, 20, 60])
    ma_support_tolerance: float = 0.01        # 이평선 ±1% 이내 접근을 지지로 간주

    # 반등 확인
    bounce_min_gain_pct: float = 0.005        # 반등 최소 상승: 0.5%
    bounce_volume_ratio: float = 1.2          # 반등 시 거래량 증가 비율 (눌림 평균 대비)

    # SF존 추가 조건
    sf_impulse_min_gain_pct: float = 0.05     # SF존 기준봉 최소 상승: 5%
    sf_volume_ratio: float = 3.0              # SF존 거래량 배율: 300%

    # 이동평균선 계산용 데이터 최소 수
    min_candles: int = 60


# ── 분석 결과 ─────────────────────────────────────────────────────────────────

@dataclass
class FZoneAnalysis:
    """F존 분석 결과"""
    symbol: str
    has_impulse: bool = False
    impulse_gain_pct: float = 0.0
    impulse_volume_ratio: float = 0.0
    impulse_bar_idx: int = -1

    has_pullback: bool = False
    pullback_pct: float = 0.0
    pullback_candles: int = 0

    ma_support: Optional[int] = None          # 지지된 이평선 기간 (5, 20, 60)
    ma_touch_pct: float = 0.0                 # 이평선 근접 정도

    has_bounce: bool = False
    bounce_gain_pct: float = 0.0
    bounce_volume_ratio: float = 0.0

    is_f_zone: bool = False
    is_sf_zone: bool = False
    score: float = 0.0
    reason: str = ""


# ── F존 전략 엔진 ─────────────────────────────────────────────────────────────

class FZoneStrategy(Strategy):
    """
    F존/SF존 매매 전략 엔진

    사용법:
        strategy = FZoneStrategy()
        signal = strategy.analyze(symbol, name, candles, market_type)
        if signal:
            # 진입 신호 처리
    """

    STRATEGY_ID = "f_zone_v1"

    def __init__(self, params: Optional[FZoneParams] = None) -> None:
        self.params = params or FZoneParams()

    # ── 공개 인터페이스 ────────────────────────────────────────────────────────

    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        """
        BAR-46: F존 v2 직접 구현 (BAR-45 의 `_analyze_impl` shim 제거).

        AnalysisContext 의 candles 를 분석하여 F존/SF존 진입 신호 반환.
        후속 BAR 가 ctx.theme_context / ctx.news_context 활용 가능.
        """
        symbol = ctx.symbol
        name = ctx.name or ctx.symbol
        candles = ctx.candles
        market_type = ctx.market_type
        p = self.params

        if len(candles) < p.min_candles:
            logger.debug("%s: 캔들 수 부족 (%d < %d)", symbol, len(candles), p.min_candles)
            return None

        # pandas DataFrame으로 변환 (오래된 → 최신 순)
        df = self._to_dataframe(candles)
        analysis = FZoneAnalysis(symbol=symbol)

        # 1단계: 기준봉 탐색
        self._detect_impulse(df, analysis)
        if not analysis.has_impulse:
            return None

        # 2단계: 눌림목 확인
        self._detect_pullback(df, analysis)
        if not analysis.has_pullback:
            return None

        # 3단계: 이동평균선 지지 확인
        self._check_ma_support(df, analysis)

        # 4단계: 반등 캔들 확인
        self._detect_bounce(df, analysis)
        if not analysis.has_bounce:
            return None

        # 5단계: F존/SF존 판정 및 점수 계산
        self._score_and_classify(analysis)
        if not analysis.is_f_zone:
            return None

        signal_type = "sf_zone" if analysis.is_sf_zone else "f_zone"
        current_price = candles[0].close

        return EntrySignal(
            symbol=symbol,
            name=name,
            price=current_price,
            signal_type=signal_type,
            score=round(analysis.score, 2),
            reason=analysis.reason,
            market_type=market_type,
            strategy_id=self.STRATEGY_ID,
            timestamp=datetime.now(),
            metadata={
                "impulse_gain_pct": round(analysis.impulse_gain_pct, 4),
                "impulse_volume_ratio": round(analysis.impulse_volume_ratio, 2),
                "pullback_pct": round(analysis.pullback_pct, 4),
                "pullback_candles": analysis.pullback_candles,
                "ma_support": analysis.ma_support,
                "bounce_gain_pct": round(analysis.bounce_gain_pct, 4),
                "bounce_volume_ratio": round(analysis.bounce_volume_ratio, 2),
            },
        )

    # ── BAR-46: F존 정책 override (Strategy v2) ────────────────────────────────

    def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan:
        """F존 정책: TP1=+3% (50%) + TP2=+5% (50%) + SL=-2% + 14:50 강제 + breakeven +1.5%."""
        avg = Decimal(str(position.avg_price))

        take_profits = [
            TakeProfitTier(
                price=avg * Decimal("1.03"),
                qty_pct=Decimal("0.5"),
                condition="F존 TP1 +3%",
            ),
            TakeProfitTier(
                price=avg * Decimal("1.05"),
                qty_pct=Decimal("0.5"),
                condition="F존 TP2 +5%",
            ),
        ]

        # KRX 정규장 강제 청산 (14:50). crypto 는 None.
        time_exit = dtime(14, 50) if ctx.market_type == MarketType.STOCK else None

        return ExitPlan(
            take_profits=take_profits,
            stop_loss=StopLoss(fixed_pct=Decimal("-0.02")),
            time_exit=time_exit,
            breakeven_trigger=Decimal("0.015"),
        )

    def position_size(self, signal: EntrySignal, account: Account) -> Decimal:
        """F존 강도(score) 기반 비중: ≥0.7 → 30%, 0.5~0.7 → 20%, <0.5 → 10%."""
        if account.available <= 0:
            return Decimal(0)

        score = Decimal(str(signal.score))
        if score >= Decimal("0.7"):
            ratio = Decimal("0.30")
        elif score >= Decimal("0.5"):
            ratio = Decimal("0.20")
        else:
            ratio = Decimal("0.10")

        max_invest = account.available * ratio
        price = Decimal(str(signal.price))
        if price <= 0:
            return Decimal(0)
        return (max_invest / price).quantize(Decimal("1"))

    def health_check(self) -> dict[str, Any]:
        """F존 health_check — params sanity + 데이터 충분성 임계값."""
        p = self.params
        return {
            "strategy_id": self.STRATEGY_ID,
            "ready": p.min_candles >= 60 and p.impulse_lookback > 0,
            "min_candles": p.min_candles,
            "impulse_lookback": p.impulse_lookback,
        }

    # ── 내부 분석 단계 ─────────────────────────────────────────────────────────

    def _detect_impulse(self, df: pd.DataFrame, analysis: FZoneAnalysis) -> None:
        """기준봉 탐색: 거래량 폭발 + 강한 양봉"""
        p = self.params
        n = len(df)

        # 평균 거래량 (전체 기준)
        avg_volume = df["volume"].mean()
        if avg_volume == 0:
            return

        # 최근 봉부터 impulse_lookback 범위에서 기준봉 탐색
        # df는 오래된→최신 순이므로 뒤에서부터 탐색
        search_start = max(0, n - p.impulse_lookback - 1)
        search_end = n - 1  # 마지막(현재) 봉은 제외

        best_gain = 0.0
        best_idx = -1

        for i in range(search_start, search_end):
            row = df.iloc[i]
            if row["open"] == 0:
                continue

            gain_pct = (row["close"] - row["open"]) / row["open"]
            vol_ratio = row["volume"] / avg_volume

            if gain_pct >= p.impulse_min_gain_pct and vol_ratio >= p.impulse_volume_ratio:
                if gain_pct > best_gain:
                    best_gain = gain_pct
                    best_idx = i
                    analysis.impulse_volume_ratio = vol_ratio

        if best_idx >= 0:
            analysis.has_impulse = True
            analysis.impulse_gain_pct = best_gain
            analysis.impulse_bar_idx = best_idx
            logger.debug(
                "%s: 기준봉 발견 idx=%d, gain=%.2f%%, vol_ratio=%.1fx",
                analysis.symbol, best_idx, best_gain * 100, analysis.impulse_volume_ratio,
            )

    def _detect_pullback(self, df: pd.DataFrame, analysis: FZoneAnalysis) -> None:
        """눌림목 확인: 기준봉 이후 거래량 감소 + 조정"""
        p = self.params
        n = len(df)
        imp_idx = analysis.impulse_bar_idx
        if imp_idx < 0:
            return

        impulse_close = df.iloc[imp_idx]["close"]
        impulse_volume = df.iloc[imp_idx]["volume"]

        # 기준봉 이후 봉들 (기준봉 바로 다음부터 현재 전까지)
        pullback_range = df.iloc[imp_idx + 1 : n - 1]
        if pullback_range.empty:
            return

        # 최저점과 해당 시점의 하락률
        low_prices = pullback_range["low"]
        if low_prices.empty:
            return

        lowest_price = low_prices.min()
        pullback_pct = (lowest_price - impulse_close) / impulse_close if impulse_close > 0 else 0

        if p.pullback_max_pct >= pullback_pct >= p.pullback_min_pct:
            # 눌림 구간 거래량 평균
            avg_pullback_vol = pullback_range["volume"].mean()
            vol_ratio = avg_pullback_vol / impulse_volume if impulse_volume > 0 else 1.0

            if vol_ratio <= p.pullback_volume_ratio:
                analysis.has_pullback = True
                analysis.pullback_pct = pullback_pct
                analysis.pullback_candles = len(pullback_range)
                logger.debug(
                    "%s: 눌림목 확인 pct=%.2f%%, candles=%d, vol_ratio=%.2f",
                    analysis.symbol, pullback_pct * 100,
                    analysis.pullback_candles, vol_ratio,
                )

    def _check_ma_support(self, df: pd.DataFrame, analysis: FZoneAnalysis) -> None:
        """이동평균선 지지 확인: 현재 가격이 이평선 근처"""
        p = self.params
        current_low = df.iloc[-1]["low"]
        current_close = df.iloc[-1]["close"]

        best_touch_pct = float("inf")
        best_ma = None

        for period in p.ma_periods:
            if len(df) < period:
                continue
            ma_val = df["close"].iloc[-period:].mean()
            if ma_val == 0:
                continue

            # 저점이 이평선 ±tolerance 이내인지 확인
            touch_pct = abs(current_low - ma_val) / ma_val
            if touch_pct <= p.ma_support_tolerance and touch_pct < best_touch_pct:
                best_touch_pct = touch_pct
                best_ma = period

        if best_ma is not None:
            analysis.ma_support = best_ma
            analysis.ma_touch_pct = best_touch_pct
            logger.debug(
                "%s: 이평선(%d일) 지지 touch_pct=%.3f%%",
                analysis.symbol, best_ma, best_touch_pct * 100,
            )

    def _detect_bounce(self, df: pd.DataFrame, analysis: FZoneAnalysis) -> None:
        """반등 확인: 현재(마지막) 봉이 반등 신호"""
        p = self.params
        n = len(df)
        if n < 3:
            return

        current = df.iloc[-1]
        prev = df.iloc[-2]

        if current["open"] == 0:
            return

        # 반등 캔들: 양봉 + 최소 상승률
        bounce_gain_pct = (current["close"] - current["open"]) / current["open"]
        if bounce_gain_pct < p.bounce_min_gain_pct:
            return

        # 반등 거래량 증가 확인
        imp_idx = analysis.impulse_bar_idx
        pullback_range = df.iloc[imp_idx + 1 : n - 1] if imp_idx >= 0 else df.iloc[:-1]
        avg_pullback_vol = pullback_range["volume"].mean() if not pullback_range.empty else 0

        bounce_vol_ratio = (
            current["volume"] / avg_pullback_vol if avg_pullback_vol > 0 else 1.0
        )

        if bounce_vol_ratio >= p.bounce_volume_ratio:
            analysis.has_bounce = True
            analysis.bounce_gain_pct = bounce_gain_pct
            analysis.bounce_volume_ratio = bounce_vol_ratio
            logger.debug(
                "%s: 반등 확인 gain=%.2f%%, vol_ratio=%.2f",
                analysis.symbol, bounce_gain_pct * 100, bounce_vol_ratio,
            )

    def _score_and_classify(self, analysis: FZoneAnalysis) -> None:
        """F존/SF존 판정 및 종합 점수 계산 (0~10점)"""
        p = self.params
        score = 0.0
        reasons = []

        if not (analysis.has_impulse and analysis.has_pullback and analysis.has_bounce):
            return

        # 기준봉 점수 (최대 3점)
        gain_score = min(analysis.impulse_gain_pct / p.sf_impulse_min_gain_pct, 1.0) * 2.0
        vol_score = min(analysis.impulse_volume_ratio / p.sf_volume_ratio, 1.0) * 1.0
        score += gain_score + vol_score
        reasons.append(
            f"기준봉 +{analysis.impulse_gain_pct*100:.1f}%(거래량 {analysis.impulse_volume_ratio:.1f}x)"
        )

        # 눌림목 점수 (최대 2점): 얕을수록 좋음
        pullback_depth = abs(analysis.pullback_pct)
        pullback_score = max(0.0, 1.0 - pullback_depth / 0.05) * 2.0
        score += pullback_score
        reasons.append(f"눌림 {analysis.pullback_pct*100:.1f}%")

        # 이평선 지지 점수 (최대 2점)
        if analysis.ma_support is not None:
            ma_score = 2.0 - (analysis.ma_touch_pct / p.ma_support_tolerance)
            score += max(0.0, ma_score)
            reasons.append(f"{analysis.ma_support}일 이평선 지지")
        else:
            reasons.append("이평선 지지 미확인")

        # 반등 점수 (최대 3점)
        bounce_score = min(analysis.bounce_gain_pct / 0.02, 1.0) * 1.5
        bounce_vol_score = min(analysis.bounce_volume_ratio / 2.0, 1.0) * 1.5
        score += bounce_score + bounce_vol_score
        reasons.append(
            f"반등 +{analysis.bounce_gain_pct*100:.1f}%(거래량 {analysis.bounce_volume_ratio:.1f}x)"
        )

        analysis.score = min(score, 10.0)
        analysis.is_f_zone = analysis.score >= 4.0

        # SF존: 기준봉이 특히 강하고 점수가 높을 때
        analysis.is_sf_zone = (
            analysis.is_f_zone
            and analysis.impulse_gain_pct >= p.sf_impulse_min_gain_pct
            and analysis.impulse_volume_ratio >= p.sf_volume_ratio
            and analysis.score >= 7.0
        )

        zone_label = "SF존(슈퍼존)" if analysis.is_sf_zone else "F존"
        analysis.reason = f"[{zone_label}] " + " | ".join(reasons)

    # ── 유틸리티 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _to_dataframe(candles: List[OHLCV]) -> pd.DataFrame:
        """OHLCV 리스트 → pandas DataFrame (오래된 순)"""
        rows = [
            {
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in reversed(candles)  # 최신→오래된 순 → 오래된→최신 순으로 변환
        ]
        df = pd.DataFrame(rows)
        df.set_index("timestamp", inplace=True)
        return df
