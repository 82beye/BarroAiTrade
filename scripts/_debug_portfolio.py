"""PortfolioSimulator cash 흐름 디버그 — 다종목 + cash 재구성."""
from __future__ import annotations

import json
import sys
import warnings
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore", category=DeprecationWarning)

from backend.core.backtester import PortfolioSimulator
from backend.models.market import MarketType, OHLCV

ROOT = Path(__file__).resolve().parent.parent


def load(sym: str) -> list[OHLCV]:
    raw = json.loads((ROOT / "data" / "ohlcv_cache" / f"{sym}.json").read_text())
    return [
        OHLCV(
            symbol=sym, timestamp=datetime.strptime(r["date"], "%Y%m%d"),
            open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
            close=float(r["close"]), volume=float(r.get("volume", 0)),
            market_type=MarketType.STOCK,
        )
        for r in sorted(raw["data"], key=lambda r: r["date"])
    ]


SYMS = ["319400", "066570", "090710", "010170", "003280", "012200", "356680", "012860"]
candles_by = {s: load(s) for s in SYMS}

psim = PortfolioSimulator(
    Decimal("45117579"),
    max_per_position=Decimal("0.1"),
    max_total_position=Decimal("0.9"),
    max_concurrent=10,
    commission_pct=0.015,
    tax_pct_on_sell=0.18,
)
r = psim.run(
    candles_by,
    strategies=["f_zone", "sf_zone", "gold_zone", "swing_38", "scalping_consensus"],
)

print(f"total_pnl      : {r.metrics.total_pnl:,}")
print(f"initial        : {r.initial_capital:,}")
print(f"final          : {r.final_capital:,}")
print(f"open_count     : {r.open_positions_count}")
print(f"expected(0가정) : {r.initial_capital + r.metrics.total_pnl:,}")
print(f"차이           : {r.final_capital - (r.initial_capital + r.metrics.total_pnl):,}")
print()

# 종목별 buy/sell qty 매칭
buys = defaultdict(list)
sells = defaultdict(list)
for t in r.trades:
    (buys if t.side == "buy" else sells)[t.symbol].append(t)
print("종목별 buy_qty vs sell_qty:")
for sym in SYMS:
    bq = sum((t.qty for t in buys[sym]), Decimal("0"))
    sq = sum((t.qty for t in sells[sym]), Decimal("0"))
    flag = "  <<< 미청산" if bq != sq else ""
    print(f"  {sym}: buy {len(buys[sym])}건/{bq}주  sell {len(sells[sym])}건/{sq}주{flag}")
print()

# cash 재구성 — 거래 기록만으로
rate = Decimal("0.00015")
tax_rate = Decimal("0.0018")
cash_check = r.initial_capital
for t in r.trades:
    notional = t.price * t.qty
    if t.side == "buy":
        cash_check -= notional + notional * rate
    else:
        cash_check += notional - notional * rate - notional * tax_rate
print(f"거래기록 재구성 cash : {cash_check:,}")
print()

# 066570 거래 시간순 — qty 누적 추적
print("066570 거래 시간순 (qty 누적):")
running = Decimal("0")
for t in sorted(
    [t for t in r.trades if t.symbol == "066570"], key=lambda t: t.timestamp
):
    if t.side == "buy":
        running += t.qty
    else:
        running -= t.qty
    print(
        f"  {t.timestamp:%Y-%m-%d} {t.side:4} qty={t.qty:>12} "
        f"price={t.price:>10} reason={t.reason:10} 보유={running}"
    )
