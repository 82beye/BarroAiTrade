"""
ATR 기반 시장 상태 분석기

ATR(5)/ATR(20) 비율로 시장 변동성을 측정하고,
급락장에서 매매를 자동 중단/축소.

시장 레벨:
  NORMAL  — ATR비율 < 1.5           → 정상 매매 (x1.0)
  CAUTION — 1.5~2.0 또는 MA20 하회  → 포지션 x0.5, 손절 -1.5%
  WARNING — 2.0~3.0                 → 포지션 x0.25, 손절 -1.0%
  EXTREME — ≥3.0 또는 일변동 -4%↓   → 매수 중단, 손절 -0.5%
"""

import asyncio
import logging
from enum import Enum
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from scanner.indicators import calc_atr

logger = logging.getLogger(__name__)


class MarketLevel(Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    WARNING = "WARNING"
    EXTREME = "EXTREME"


@dataclass
class IndexMetrics:
    """개별 지수 메트릭"""
    name: str               # "KOSPI" / "KOSDAQ"
    close: float
    daily_change_pct: float  # 전일 대비 등락률
    ma20: float
    below_ma20: bool
    atr_short: float         # ATR(5)
    atr_long: float          # ATR(20)
    atr_ratio: float         # ATR(5) / ATR(20)
    level: MarketLevel


@dataclass
class MarketCondition:
    """양 지수 종합 시장 상태"""
    kospi: Optional[IndexMetrics]
    kosdaq: Optional[IndexMetrics]
    overall_level: MarketLevel
    # 매매 파라미터
    entry_allowed: bool
    position_size_multiplier: float
    stop_loss_override: Optional[float]  # None이면 기본 손절 사용


class MarketConditionAnalyzer:
    """ATR 기반 시장 상태 분석기"""

    def __init__(self, config: dict):
        mc_config = config.get('risk', {}).get('market_condition', {})
        self.enabled = mc_config.get('enabled', True)

        thresholds = mc_config.get('thresholds', {})
        self.caution_ratio = thresholds.get('caution_ratio', 1.5)
        self.warning_ratio = thresholds.get('warning_ratio', 2.0)
        self.extreme_ratio = thresholds.get('extreme_ratio', 3.0)

        self.atr_short_period = mc_config.get('atr_short_period', 5)
        self.atr_long_period = mc_config.get('atr_long_period', 20)

        drops = mc_config.get('drops', {})
        self.caution_drop_pct = drops.get('caution_drop_pct', -2.0)
        self.extreme_drop_pct = drops.get('extreme_drop_pct', -4.0)

        sizing = mc_config.get('sizing', {})
        self.sizing = {
            MarketLevel.NORMAL: sizing.get('normal', 1.0),
            MarketLevel.CAUTION: sizing.get('caution', 0.5),
            MarketLevel.WARNING: sizing.get('warning', 0.25),
            MarketLevel.EXTREME: sizing.get('extreme', 0.0),
        }

        stop_loss = mc_config.get('stop_loss', {})
        self.stop_overrides = {
            MarketLevel.CAUTION: stop_loss.get('caution', -1.5),
            MarketLevel.WARNING: stop_loss.get('warning', -1.0),
            MarketLevel.EXTREME: stop_loss.get('extreme', -0.5),
        }

    async def analyze(self, kiwoom_api) -> MarketCondition:
        """코스피·코스닥 양 지수를 분석하여 시장 상태 반환"""
        if not self.enabled:
            return MarketCondition(
                kospi=None, kosdaq=None,
                overall_level=MarketLevel.NORMAL,
                entry_allowed=True,
                position_size_multiplier=1.0,
                stop_loss_override=None,
            )

        kospi = await self._analyze_index(kiwoom_api, "001", "KOSPI")
        kosdaq = await self._analyze_index(kiwoom_api, "101", "KOSDAQ")

        # 나쁜 쪽 기준 판정
        levels = []
        if kospi:
            levels.append(kospi.level)
        if kosdaq:
            levels.append(kosdaq.level)

        if not levels:
            overall = MarketLevel.NORMAL
        else:
            level_order = [MarketLevel.NORMAL, MarketLevel.CAUTION,
                           MarketLevel.WARNING, MarketLevel.EXTREME]
            overall = max(levels, key=lambda lv: level_order.index(lv))

        size_mult = self.sizing[overall]
        entry_allowed = size_mult > 0
        stop_override = self.stop_overrides.get(overall)

        condition = MarketCondition(
            kospi=kospi,
            kosdaq=kosdaq,
            overall_level=overall,
            entry_allowed=entry_allowed,
            position_size_multiplier=size_mult,
            stop_loss_override=stop_override,
        )

        logger.info(
            f"시장 상태: {overall.value} | "
            f"포지션배율: x{size_mult} | "
            f"매수허용: {entry_allowed} | "
            f"손절override: {stop_override}"
        )

        return condition

    async def _analyze_index(
        self, kiwoom_api, index_code: str, name: str
    ) -> Optional[IndexMetrics]:
        """개별 지수 분석 (데이터 부족 시 최대 3회 재시도)"""
        max_attempts = 3
        df = None

        for attempt in range(max_attempts):
            try:
                df = await kiwoom_api.get_index_ohlcv(index_code, count=30)
            except Exception as e:
                if attempt < max_attempts - 1:
                    wait = 2 * (attempt + 1)
                    logger.warning(f"{name} 지수 조회 실패, {wait}s 후 재시도 ({attempt+1}/{max_attempts}): {e}")
                    await asyncio.sleep(wait)
                    continue
                logger.error(f"{name} 지수 분석 실패 ({max_attempts}회 시도): {e}")
                return None

            if df is not None and len(df) >= self.atr_long_period:
                break

            if attempt < max_attempts - 1:
                wait = 2 * (attempt + 1)
                logger.info(f"{name} 지수 데이터 부족 (count={len(df) if df is not None else 0}), {wait}s 후 재시도 ({attempt+1}/{max_attempts})")
                await asyncio.sleep(wait)
        else:
            logger.warning(f"{name} 지수 데이터 부족 ({max_attempts}회 시도 후 포기, count={len(df) if df is not None else 0})")
            return None

        try:
            close = df['close'].iloc[-1]
            prev_close = df['close'].iloc[-2]
            daily_change_pct = (close - prev_close) / prev_close * 100

            ma20 = df['close'].rolling(20).mean().iloc[-1]
            below_ma20 = close < ma20

            atr_short = calc_atr(
                df['high'], df['low'], df['close'], self.atr_short_period
            ).iloc[-1]
            atr_long = calc_atr(
                df['high'], df['low'], df['close'], self.atr_long_period
            ).iloc[-1]
            atr_ratio = atr_short / atr_long if atr_long > 0 else 1.0

            level = self._classify_level(atr_ratio, daily_change_pct, below_ma20)

            logger.info(
                f"  {name}: {close:,.1f} ({daily_change_pct:+.1f}%) | "
                f"MA20: {ma20:,.1f} {'▼' if below_ma20 else '▲'} | "
                f"ATR: {atr_short:.1f}/{atr_long:.1f} = {atr_ratio:.2f} → {level.value}"
            )

            return IndexMetrics(
                name=name,
                close=close,
                daily_change_pct=daily_change_pct,
                ma20=ma20,
                below_ma20=below_ma20,
                atr_short=atr_short,
                atr_long=atr_long,
                atr_ratio=atr_ratio,
                level=level,
            )

        except Exception as e:
            logger.error(f"{name} 지수 분석 실패: {e}")
            return None

    def _classify_level(
        self, atr_ratio: float, daily_change_pct: float, below_ma20: bool
    ) -> MarketLevel:
        """ATR 비율 + 일변동 + MA20 기반 레벨 판정"""
        # 기본 ATR 비율 판정
        if atr_ratio >= self.extreme_ratio:
            level = MarketLevel.EXTREME
        elif atr_ratio >= self.warning_ratio:
            level = MarketLevel.WARNING
        elif atr_ratio >= self.caution_ratio:
            level = MarketLevel.CAUTION
        else:
            level = MarketLevel.NORMAL

        # 급락 오버라이드
        if daily_change_pct <= self.extreme_drop_pct:
            level = MarketLevel.EXTREME
        elif daily_change_pct <= self.caution_drop_pct and level.value == "NORMAL":
            level = MarketLevel.CAUTION

        # MA20 하회 시 한 단계 에스컬레이션
        if below_ma20:
            escalation = {
                MarketLevel.NORMAL: MarketLevel.CAUTION,
                MarketLevel.CAUTION: MarketLevel.WARNING,
                MarketLevel.WARNING: MarketLevel.EXTREME,
                MarketLevel.EXTREME: MarketLevel.EXTREME,
            }
            level = escalation[level]

        return level
