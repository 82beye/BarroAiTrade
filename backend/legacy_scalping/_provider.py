"""
ScalpingCoordinator ↔ ScalpingConsensusStrategy provider bridge.

AnalysisContext (candles) → StockSnapshot + OHLCV DataFrame →
ScalpingCoordinator.analyze() → ScalpingAnalysis (top-1) 반환.

BAR-78: scalping_consensus provider 연결.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from backend.models.strategy import AnalysisContext

logger = logging.getLogger(__name__)

# legacy_scalping 내부 코드가 `from strategy.xxx import ...` 를 사용하므로
# backend/legacy_scalping 을 sys.path 에 추가해야 coordinator import 가능.
_LEGACY_ROOT = str(Path(__file__).resolve().parent)
if _LEGACY_ROOT not in sys.path:
    sys.path.insert(0, _LEGACY_ROOT)

from strategy.scalping_team.base_agent import (  # noqa: E402
    ScalpingAnalysis,
    StockSnapshot,
)
from strategy.scalping_team.coordinator import ScalpingCoordinator  # noqa: E402

# 최소 config — 필수 키만 제공, 나머지는 coordinator 내부 기본값 사용
_DEFAULT_CONFIG: dict[str, Any] = {
    "scanner": {"cache_dir": "./data/ohlcv_cache"},
    "strategy": {
        "scalping": {
            "default_sl_pct": -3.0,
            "min_consensus": "소수합의",
        },
    },
    "logging": {"trade_log": "./logs/trades.jsonl"},
}


def _ctx_to_snapshot(ctx: AnalysisContext) -> StockSnapshot:
    """AnalysisContext candles → StockSnapshot 변환.

    candles[-1] = 최신 봉, candles[0] = 가장 오래된 봉.
    prev_close = candles[-2].close (없으면 candles[-1].open 사용).
    """
    latest = ctx.candles[-1]
    prev_close = ctx.candles[-2].close if len(ctx.candles) >= 2 else latest.open

    change_pct = 0.0
    if prev_close > 0:
        change_pct = (latest.close - prev_close) / prev_close * 100

    # volume_ratio: 최근 20봉 평균 거래량 대비 비율
    volumes = [c.volume for c in ctx.candles[-20:]]
    avg_vol = sum(volumes) / len(volumes) if volumes else 1.0
    volume_ratio = latest.volume / avg_vol if avg_vol > 0 else 1.0

    return StockSnapshot(
        code=ctx.symbol,
        name=ctx.name or ctx.symbol,
        price=latest.close,
        open=latest.open,
        high=latest.high,
        low=latest.low,
        prev_close=prev_close,
        volume=int(latest.volume),
        change_pct=change_pct,
        trade_value=latest.close * latest.volume,
        volume_ratio=volume_ratio,
        category="상승주",
        score=50.0,
    )


def _ctx_to_ohlcv_df(ctx: AnalysisContext) -> pd.DataFrame:
    """AnalysisContext candles → pandas DataFrame (coordinator cache_data 형식)."""
    return pd.DataFrame(
        {
            "open": [c.open for c in ctx.candles],
            "high": [c.high for c in ctx.candles],
            "low": [c.low for c in ctx.candles],
            "close": [c.close for c in ctx.candles],
            "volume": [c.volume for c in ctx.candles],
        }
    )


def build_scalping_provider(
    config: dict[str, Any] | None = None,
) -> Callable[[AnalysisContext], Optional[Any]]:
    """ScalpingConsensusStrategy.set_analysis_provider() 에 등록할 provider 생성.

    Returns:
        AnalysisContext → Optional[ScalpingAnalysis] callable.
    """
    cfg = config or _DEFAULT_CONFIG
    coordinator = ScalpingCoordinator(cfg)

    def provider(ctx: AnalysisContext) -> Optional[ScalpingAnalysis]:
        try:
            snapshot = _ctx_to_snapshot(ctx)
            ohlcv_df = _ctx_to_ohlcv_df(ctx)

            results = coordinator.analyze(
                snapshots=[snapshot],
                cache_data={ctx.symbol: ohlcv_df},
                intraday_data={},
            )

            if not results:
                return None

            # top-1 (점수 내림차순 정렬 이미 coordinator에서 수행)
            return results[0]

        except Exception as e:
            logger.warning("scalping provider failed for %s: %s", ctx.symbol, e)
            return None

    return provider


__all__ = ["build_scalping_provider"]
