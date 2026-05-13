#!/usr/bin/env python3
"""f_zone × LG전자(066570) -627k 손실 추적.

REPORT_600BARS.md 에서 f_zone 이 LG전자에 3 trades, 0% win, -627,521 손실로
나타남. 다음을 확인:
  - 정확한 진입 일자
  - 진입가 / 청산가 / 청산 사유 (sl / tp1~3 / time_exit)
  - 진입 직전 5봉 + 진입 직후 5봉 컨텍스트
  - F-zone 진입 조건(눌림목 -5~-0.5% + 거래량 감소 + 양봉반등)이 어떻게 충족됐는지
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore", category=DeprecationWarning)

from backend.core.backtester import IntradaySimulator  # noqa: E402
from backend.models.market import MarketType, OHLCV  # noqa: E402

SYMBOL = "066570"
NAME = "LG전자"
OUT_DIR = ROOT / "analysis" / "imports" / "2026-05-13"
TRACE_MD = OUT_DIR / "FZONE_LG_TRACE.md"
TRADES_CSV = OUT_DIR / "fzone_lg_trades.csv"
CONTEXT_CSV = OUT_DIR / "fzone_lg_context.csv"


def load_candles() -> list[OHLCV]:
    raw = json.loads((ROOT / "data" / "ohlcv_cache" / f"{SYMBOL}.json").read_text())
    out: list[OHLCV] = []
    for row in sorted(raw["data"], key=lambda r: r["date"]):
        out.append(OHLCV(
            symbol=SYMBOL,
            timestamp=datetime.strptime(row["date"], "%Y%m%d"),
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume=float(row.get("volume", 0)),
            market_type=MarketType.STOCK,
        ))
    return out


def pair_trades(trades: list, strategy_id: str) -> list[dict]:
    """f_zone strategy 의 buy-sell pair 를 시계열 순으로 묶음.

    동일 진입에 TP1/TP2/TP3 부분 청산 + 잔여 SL/time 청산이 발생할 수 있으므로
    하나의 entry 에 여러 exit 가 매핑됨.
    """
    sub = [t for t in trades if t.strategy_id == strategy_id]
    pairs: list[dict] = []
    current = None
    for t in sub:
        if t.side == "buy" and t.reason == "entry":
            if current and current["remaining"] > 0:
                # 진입 중에 새 진입 발생 — 이전 close-out
                pairs.append(current)
            current = {
                "entry_ts": t.timestamp,
                "entry_price": float(t.price),
                "qty": float(t.qty),
                "remaining": float(t.qty),
                "exits": [],
            }
        elif t.side == "sell" and current is not None:
            current["exits"].append({
                "ts": t.timestamp,
                "price": float(t.price),
                "qty": float(t.qty),
                "reason": t.reason,
            })
            current["remaining"] -= float(t.qty)
            if current["remaining"] <= 0.001:
                pairs.append(current)
                current = None
    if current:
        pairs.append(current)
    return pairs


def trade_pnl(pair: dict) -> float:
    """entry_price 기준 buy + sell 가중평균 PnL (수수료 제외 gross)."""
    entry_value = pair["entry_price"] * pair["qty"]
    sell_value = sum(e["price"] * e["qty"] for e in pair["exits"])
    return sell_value - entry_value


def format_pair_table(pairs: list[dict]) -> str:
    out = [
        "| # | entry_date | entry_px | qty | exit_summary | gross_pnl |",
        "|---|------------|---------:|----:|--------------|----------:|",
    ]
    for i, p in enumerate(pairs, 1):
        exit_str = "; ".join(
            f"{e['ts'].strftime('%Y-%m-%d')} {e['reason']}@{e['price']:,.0f}({e['qty']:.0f}주)"
            for e in p["exits"]
        ) or "—"
        out.append(
            f"| {i} | {p['entry_ts'].strftime('%Y-%m-%d')} | {p['entry_price']:,.0f} | "
            f"{p['qty']:.0f} | {exit_str} | {trade_pnl(p):+,.0f} |"
        )
    return "\n".join(out)


def write_trades_csv(pairs: list[dict]) -> None:
    rows = ["trade_no,entry_ts,entry_price,exit_ts,exit_price,exit_qty,reason,leg_pnl"]
    for i, p in enumerate(pairs, 1):
        for e in p["exits"]:
            leg_pnl = (e["price"] - p["entry_price"]) * e["qty"]
            rows.append(
                f"{i},{p['entry_ts'].date()},{p['entry_price']:.0f},"
                f"{e['ts'].date()},{e['price']:.0f},{e['qty']:.0f},"
                f"{e['reason']},{leg_pnl:+.0f}"
            )
    TRADES_CSV.write_text("\n".join(rows), encoding="utf-8")


def write_context_csv(candles: list[OHLCV], pairs: list[dict], window: int = 5) -> None:
    """각 진입 시점 ±window 봉 OHLCV."""
    date_to_idx = {c.timestamp.date(): i for i, c in enumerate(candles)}
    rows = ["trade_no,offset,date,open,high,low,close,volume,pct_chg"]
    for i, p in enumerate(pairs, 1):
        idx = date_to_idx.get(p["entry_ts"].date())
        if idx is None:
            continue
        for off in range(-window, window + 1):
            j = idx + off
            if not (0 <= j < len(candles)):
                continue
            c = candles[j]
            prev_close = candles[j - 1].close if j > 0 else c.close
            pct = (c.close - prev_close) / prev_close * 100 if prev_close else 0
            rows.append(
                f"{i},{off:+d},{c.timestamp.date()},{c.open:.0f},{c.high:.0f},"
                f"{c.low:.0f},{c.close:.0f},{c.volume:.0f},{pct:+.2f}"
            )
    CONTEXT_CSV.write_text("\n".join(rows), encoding="utf-8")


def render_context_md(candles: list[OHLCV], pairs: list[dict], window: int = 3) -> str:
    """진입 시점 ±3봉 텍스트 표 — 한 trade 당 한 블록."""
    date_to_idx = {c.timestamp.date(): i for i, c in enumerate(candles)}
    out = StringIO()
    for i, p in enumerate(pairs, 1):
        idx = date_to_idx.get(p["entry_ts"].date())
        if idx is None:
            continue
        out.write(f"\n### Trade #{i} — entry {p['entry_ts'].date()} @ {p['entry_price']:,.0f}\n\n")
        out.write("| offset | date | open | high | low | close | vol | %chg |\n")
        out.write("|-------:|------|----:|----:|----:|------:|----:|----:|\n")
        for off in range(-window, window + 1):
            j = idx + off
            if not (0 <= j < len(candles)):
                continue
            c = candles[j]
            prev = candles[j - 1].close if j > 0 else c.close
            pct = (c.close - prev) / prev * 100 if prev else 0
            marker = " ← entry signal bar" if off == -1 else (" ← entry execution bar" if off == 0 else "")
            out.write(
                f"| {off:+d} | {c.timestamp.date()} | {c.open:,.0f} | {c.high:,.0f} | "
                f"{c.low:,.0f} | {c.close:,.0f} | {c.volume:,.0f} | {pct:+.2f}%{marker} |\n"
            )
        # 각 exit
        out.write("\n**Exits:**\n")
        for e in p["exits"]:
            edate = e["ts"].date()
            ej = date_to_idx.get(edate)
            ecandle = candles[ej] if ej is not None else None
            extra = ""
            if ecandle:
                extra = (f" — bar low={ecandle.low:,.0f}, high={ecandle.high:,.0f}, "
                         f"close={ecandle.close:,.0f}")
            leg_pnl = (e["price"] - p["entry_price"]) * e["qty"]
            pct_from_entry = (e["price"] - p["entry_price"]) / p["entry_price"] * 100
            out.write(
                f"- {edate} `{e['reason']}` @ {e['price']:,.0f} ({e['qty']:.0f}주, "
                f"{pct_from_entry:+.2f}% / leg PnL {leg_pnl:+,.0f}){extra}\n"
            )
    return out.getvalue()


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candles = load_candles()
    print(f"[load] {SYMBOL} {NAME}: {len(candles)} candles "
          f"({candles[0].timestamp.date()} ~ {candles[-1].timestamp.date()})")

    sim = IntradaySimulator(
        warmup_candles=31,
        position_qty=Decimal("100"),
        entry_on_next_open=True,
        exit_on_intrabar=True,
        commission_pct=0.015,
        tax_pct_on_sell=0.18,
        slippage_pct=0.0,
    )
    result = sim.run(candles, symbol=SYMBOL, strategies=["f_zone"])

    pairs = pair_trades(result.trades, "f_zone")
    print(f"[trades] f_zone entries: {len(pairs)}")

    # 콘솔 요약
    print("\n" + format_pair_table(pairs))
    print(f"\nsim total PnL: {float(result.pnl_by_strategy.get('f_zone', 0)):+,.0f}")

    write_trades_csv(pairs)
    write_context_csv(candles, pairs)
    print(f"\nCSV saved: {TRADES_CSV.relative_to(ROOT)}")
    print(f"CSV saved: {CONTEXT_CSV.relative_to(ROOT)}")

    md = StringIO()
    md.write(f"# f_zone × LG전자(066570) 손실 추적\n\n")
    md.write(
        f"**입력:** `data/ohlcv_cache/{SYMBOL}.json` ({len(candles)} 일봉, "
        f"{candles[0].timestamp.date()} ~ {candles[-1].timestamp.date()})  \n"
        f"**시뮬:** IntradaySimulator (TP +3/+5/+7%, SL -1.5%, commission 0.015%/leg, tax 0.18%)\n\n"
    )
    md.write("## 진입-청산 페어\n\n")
    md.write(format_pair_table(pairs))
    md.write(f"\n\n**sim 보고 PnL**: {float(result.pnl_by_strategy.get('f_zone', 0)):+,.0f}원\n\n")
    md.write("## 진입 시점 캔들 컨텍스트 (±3봉)\n")
    md.write(render_context_md(candles, pairs))

    # 자동 진단
    md.write("\n## 자동 진단\n\n")
    # ExitReason.value 는 "stop_loss" / "tp1" / "tp2" / "tp3" / "time_exit"
    sl_count = sum(1 for p in pairs for e in p["exits"] if e["reason"] == "stop_loss")
    tp_count = sum(1 for p in pairs for e in p["exits"] if e["reason"].startswith("tp"))
    immediate_sl = sum(
        1 for p in pairs for e in p["exits"]
        if e["reason"] == "stop_loss"
        and (e["ts"].date() - p["entry_ts"].date()).days <= 3
    )
    md.write(
        f"- 총 exit leg: {sum(len(p['exits']) for p in pairs)}\n"
        f"- SL leg: {sl_count}\n"
        f"- TP leg: {tp_count}\n"
        f"- **진입 후 3일 내 SL leg**: {immediate_sl} → "
    )
    if immediate_sl == sl_count and sl_count > 0:
        md.write("**모든 청산이 진입 직후 SL** — TP 도달 없이 -1.5% 발동. "
                 "f_zone 의 임펄스 후 매수 로직이 강세주 단기 정점에서 작동하는 패턴.\n")
    elif immediate_sl > 0:
        md.write("일부 trade 가 진입 직후 SL.\n")
    else:
        md.write("진입 직후 SL 패턴 없음.\n")

    # 시그널 바 강도 — 강한 임펄스 후 매수 가설 검증
    md.write("\n### 진입 직전(-1) 시그널 바 강도\n\n")
    md.write("| trade | signal bar date | %chg | 평가 |\n")
    md.write("|-------|-----------------|-----:|------|\n")
    date_to_idx = {c.timestamp.date(): i for i, c in enumerate(candles)}
    for i, p in enumerate(pairs, 1):
        idx = date_to_idx.get(p["entry_ts"].date())
        if idx is None or idx < 1:
            continue
        c = candles[idx - 1]
        prev = candles[idx - 2].close if idx >= 2 else c.close
        pct = (c.close - prev) / prev * 100 if prev else 0
        rating = "약함" if abs(pct) < 3 else ("강함" if abs(pct) < 7 else "**과열**")
        md.write(f"| #{i} | {c.timestamp.date()} | {pct:+.2f}% | {rating} |\n")
    md.write(
        "\n## Caveats\n\n"
        "- 일봉 시뮬: 분봉/틱 가정 전략(f_zone)이 일봉 데이터로 평가될 때 "
        "시그널 캔들 후 다음 캔들 open 진입 → 일봉 갭다운에 매우 취약.\n"
        "- TP +3% / SL -1.5% 가 일봉 변동성에선 너무 좁음 — 정상 변동에서도 즉시 발동.\n"
    )

    TRACE_MD.write_text(md.getvalue(), encoding="utf-8")
    print(f"\nMD saved: {TRACE_MD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
