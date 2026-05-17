"""F존 고정 청산 vs F존 ATR 청산 비교 — 600봉 캐시 기반.

운영 종목 8개 + 강세 종목 10개(5/16 스캔)로 같은 캔들에 두 청산 정책을 비교.
일회성 실험 스크립트.
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore", category=DeprecationWarning)

from backend.core.backtester import IntradaySimulator
from backend.models.market import MarketType, OHLCV

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "ohlcv_cache"

OP_SYMBOLS = ["319400", "066570", "090710", "010170", "003280", "012200", "356680", "012860"]
BULL_SYMBOLS = ["159010", "187870", "009155", "336260", "009150", "163730", "402340", "144960", "252400", "262260"]


def load(sym: str) -> list[OHLCV]:
    raw = json.loads((CACHE / f"{sym}.json").read_text())
    return [
        OHLCV(
            symbol=sym, timestamp=datetime.strptime(r["date"], "%Y%m%d"),
            open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
            close=float(r["close"]), volume=float(r.get("volume", 0)),
            market_type=MarketType.STOCK,
        )
        for r in sorted(raw["data"], key=lambda r: r["date"])
    ]


def run_group(name: str, symbols: list[str]) -> None:
    print(f"\n=== {name} ({len(symbols)}종목) ===")
    print(
        f"  {'sym':<8} {'Fixed':>20} {'ATR':>20} {'차이':>14}"
    )
    fixed_total = atr_total = Decimal("0")
    fixed_trades = atr_trades = 0
    rows = 0
    for sym in symbols:
        try:
            candles = load(sym)
        except FileNotFoundError:
            print(f"  {sym} 캐시 없음 — 스킵")
            continue

        sim_fixed = IntradaySimulator(
            warmup_candles=31, position_qty=Decimal("100"),
            entry_on_next_open=True, exit_on_intrabar=True,
            commission_pct=0.015, tax_pct_on_sell=0.18,
            f_zone_atr_exit=False,
        )
        r_fix = sim_fixed.run(candles, symbol=sym, strategies=["f_zone"])

        sim_atr = IntradaySimulator(
            warmup_candles=31, position_qty=Decimal("100"),
            entry_on_next_open=True, exit_on_intrabar=True,
            commission_pct=0.015, tax_pct_on_sell=0.18,
            f_zone_atr_exit=True,
        )
        r_atr = sim_atr.run(candles, symbol=sym, strategies=["f_zone"])

        pf = float(r_fix.pnl_by_strategy.get("f_zone", 0))
        pa = float(r_atr.pnl_by_strategy.get("f_zone", 0))
        nf = sum(1 for t in r_fix.trades if t.strategy_id == "f_zone" and t.side == "buy")
        na = sum(1 for t in r_atr.trades if t.strategy_id == "f_zone" and t.side == "buy")
        wf = r_fix.win_rate_by_strategy.get("f_zone", 0.0) * 100
        wa = r_atr.win_rate_by_strategy.get("f_zone", 0.0) * 100
        fixed_total += Decimal(str(pf))
        atr_total += Decimal(str(pa))
        fixed_trades += nf
        atr_trades += na
        rows += 1
        print(
            f"  {sym:<8} {f'{nf}건 {pf:+,.0f} {wf:.0f}%':>20} "
            f"{f'{na}건 {pa:+,.0f} {wa:.0f}%':>20} {pa - pf:>+14,.0f}"
        )
    diff = float(atr_total) - float(fixed_total)
    print(
        f"  {'합계':<8} {f'{fixed_trades}건 {float(fixed_total):+,.0f}':>20} "
        f"{f'{atr_trades}건 {float(atr_total):+,.0f}':>20} {diff:>+14,.0f}"
    )


run_group("운영 종목 8개 (5/12~13)", OP_SYMBOLS)
run_group("강세 종목 10개 (5/16)", BULL_SYMBOLS)
