#!/usr/bin/env python3
"""
BAR-44: 4 전략 합성 데이터 베이스라인 측정.

옵션 2 (Plan §1.2): 합성 데이터 generator + run_multi_strategy_backtest 활용.
Fixed seed=42 로 재현성 보장. 실제 5년 OHLCV 백테스트는 BAR-44b 후속.

사용:
    .venv/bin/python scripts/run_baseline.py
"""
from __future__ import annotations

import json
from pathlib import Path

from backend.core.strategy.backtester import (
    BacktestConfig,
    SyntheticDataLoader,
    run_multi_strategy_backtest,
)
from backend.core.strategy.blue_line import BlueLineStrategy
from backend.core.strategy.crypto_breakout import CryptoBreakoutStrategy
from backend.core.strategy.f_zone import FZoneStrategy
from backend.core.strategy.stock_strategy import StockStrategy
from backend.models.market import MarketType


def run_baseline(seed: int = 42, num_candles: int = 250) -> dict:
    """4 전략 합성 베이스라인 측정.

    Args:
        seed: 합성 데이터 random seed (재현성 보장)
        num_candles: 거래일 수 (250 = 약 1년 KRX)

    Returns:
        {strategy_id: BacktestReport}
    """
    candles = SyntheticDataLoader.generate(
        symbol="TEST",
        market_type=MarketType.STOCK,
        num_candles=num_candles,
        seed=seed,
    )

    strategies = [
        FZoneStrategy(),
        BlueLineStrategy(),
        StockStrategy(),
        CryptoBreakoutStrategy(),
    ]

    config = BacktestConfig()
    reports = run_multi_strategy_backtest(
        strategies=strategies,
        candles=candles,
        symbol="TEST",
        market_type=MarketType.STOCK,
        config=config,
        name="BAR-44 baseline",
    )
    return reports


def format_report(reports: dict) -> str:
    """4 전략 결과를 markdown 표로 포맷."""
    lines = [
        "| Strategy | 거래수 | 승률 | 누적수익 | MDD | Sharpe |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for sid, r in reports.items():
        m = r.metrics
        lines.append(
            f"| `{sid}` | {len(r.trades)} | {m.win_rate*100:.1f}% | "
            f"{m.total_return_pct*100:.2f}% | {m.max_drawdown*100:.2f}% | "
            f"{m.sharpe_ratio:.2f} |"
        )
    return "\n".join(lines)


def main() -> None:
    reports = run_baseline()

    print("=== BAR-44 베이스라인 (합성 데이터, seed=42, 250 거래일) ===")
    print()
    print(format_report(reports))
    print()

    # JSON 저장 (test_baseline 의 재현성 검증용)
    out_dir = Path("docs/04-report")
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        sid: {
            "trades": len(r.trades),
            "win_rate": r.metrics.win_rate,
            "total_return_pct": r.metrics.total_return_pct,
            "max_drawdown": r.metrics.max_drawdown,
            "sharpe_ratio": r.metrics.sharpe_ratio,
        }
        for sid, r in reports.items()
    }
    json_path = out_dir / "PHASE-0-baseline.json"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"JSON 저장: {json_path}")


if __name__ == "__main__":
    main()
