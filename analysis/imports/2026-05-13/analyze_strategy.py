#!/usr/bin/env python3
"""전략 정밀 분석 — 8 운영 종목 × 600봉 (일봉) 범용.

사용:
    ./venv/bin/python analyze_strategy.py --strategy=swing_38
    ./venv/bin/python analyze_strategy.py --strategy=f_zone
    ./venv/bin/python analyze_strategy.py --strategy=gold_zone

산출:
  - stdout: 종목별 ranking, exit reason 분포, win/loss 분포
  - {STRATEGY}_ANALYSIS.md: 마크다운 보고서
  - {strategy}_all_trades.csv: 모든 trade entry-exit pair

원본: analyze_fzone.py (f_zone 전용, 보존)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

_env_root = os.environ.get("PROJECT_ROOT")
if _env_root:
    ROOT = Path(_env_root).resolve()
else:
    ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

warnings.filterwarnings("ignore", category=DeprecationWarning)

from backend.core.backtester import IntradaySimulator  # noqa: E402
from backend.models.market import MarketType, OHLCV  # noqa: E402

CACHE_DIR = ROOT / "data" / "ohlcv_cache"
OUT_DIR = ROOT / "analysis" / "imports" / "2026-05-13"

SYMBOLS = {
    "319400": "현대무벡스",
    "066570": "LG전자",
    "090710": "휴림로봇",
    "010170": "대한광통신",
    "003280": "흥아해운",
    "012200": "계양전기",
    "356680": "엑스게이트",
    "012860": "모베이스전자",
}


def load_candles(symbol: str) -> list[OHLCV]:
    raw = json.loads((CACHE_DIR / f"{symbol}.json").read_text())
    out: list[OHLCV] = []
    for row in sorted(raw["data"], key=lambda r: r["date"]):
        out.append(OHLCV(
            symbol=symbol,
            timestamp=datetime.strptime(row["date"], "%Y%m%d"),
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume=float(row.get("volume", 0)),
            market_type=MarketType.STOCK,
        ))
    return out


def pair_trades(trades, sid: str) -> list[dict]:
    sub = [t for t in trades if t.strategy_id == sid]
    pairs: list[dict] = []
    cur = None
    for t in sub:
        if t.side == "buy" and t.reason == "entry":
            if cur and cur["remaining"] > 0:
                pairs.append(cur)
            cur = {
                "entry_ts": t.timestamp, "entry_price": float(t.price),
                "qty": float(t.qty), "remaining": float(t.qty), "exits": [],
            }
        elif t.side == "sell" and cur is not None:
            cur["exits"].append({
                "ts": t.timestamp, "price": float(t.price),
                "qty": float(t.qty), "reason": t.reason,
            })
            cur["remaining"] -= float(t.qty)
            if cur["remaining"] <= 0.001:
                pairs.append(cur)
                cur = None
    if cur:
        pairs.append(cur)
    return pairs


def trade_metrics(p: dict) -> dict:
    # 실현 PnL = sum((exit_price - entry_price) × exit_qty)
    # 부분 청산(TP1 만 등) 의 잔여 qty 는 무시 — 미청산 분에 −entry_value 가
    # 잘못 카운트되는 종전 버그 회피.
    gross_pnl = sum((e["price"] - p["entry_price"]) * e["qty"] for e in p["exits"])
    # 마지막 exit reason 으로 trade 분류 (TP 부분 청산 후 잔여 SL/time 잡혀도 TP 우세로 봄)
    reasons = [e["reason"] for e in p["exits"]]
    if not reasons:
        primary = "open"
    elif any(r.startswith("tp") for r in reasons) and "stop_loss" not in reasons:
        primary = "tp_only"
    elif "stop_loss" in reasons and not any(r.startswith("tp") for r in reasons):
        primary = "sl_only"
    elif "stop_loss" in reasons and any(r.startswith("tp") for r in reasons):
        primary = "tp_then_sl"
    elif "time_exit" in reasons:
        primary = "time_exit"
    else:
        primary = reasons[-1]
    # entry → first exit hold days
    hold_days = (p["exits"][0]["ts"] - p["entry_ts"]).days if p["exits"] else 0
    return {
        "gross_pnl": gross_pnl,
        "primary_outcome": primary,
        "hold_days": hold_days,
        "is_win": gross_pnl > 0,
    }


def analyze_symbol(symbol: str, name: str, strategy_id: str,
                   position_value: float | None = None) -> dict:
    candles = load_candles(symbol)
    kwargs = dict(
        warmup_candles=31, position_qty=Decimal("100"),
        entry_on_next_open=True, exit_on_intrabar=True,
        commission_pct=0.015, tax_pct_on_sell=0.18, slippage_pct=0.0,
    )
    if position_value is not None:
        kwargs["position_value"] = Decimal(str(position_value))
    sim = IntradaySimulator(**kwargs)
    result = sim.run(candles, symbol=symbol, strategies=[strategy_id])
    pairs = pair_trades(result.trades, strategy_id)
    enriched = [{**p, **trade_metrics(p)} for p in pairs]
    total_pnl = float(result.pnl_by_strategy.get(strategy_id, 0))
    wins = sum(1 for e in enriched if e["is_win"])
    losses = len(enriched) - wins
    return {
        "symbol": symbol, "name": name, "n_candles": len(candles),
        "pairs": enriched, "total_pnl": total_pnl,
        "n_trades": len(enriched), "wins": wins, "losses": losses,
        "win_rate": wins / len(enriched) if enriched else 0.0,
    }


def fmt_money(v) -> str:
    try:
        return f"{int(round(float(v))):+,}"
    except Exception:
        return "—"


def write_trades_csv(all_results: list[dict]) -> None:
    rows = [
        "symbol,name,entry_date,entry_price,qty,primary_outcome,hold_days,gross_pnl,n_exits,"
        "first_exit_date,first_exit_reason,first_exit_price"
    ]
    for r in all_results:
        for p in r["pairs"]:
            first = p["exits"][0] if p["exits"] else {}
            rows.append(
                f"{r['symbol']},{r['name']},{p['entry_ts'].date()},"
                f"{p['entry_price']:.0f},{p['qty']:.0f},"
                f"{p['primary_outcome']},{p['hold_days']},{p['gross_pnl']:+.0f},"
                f"{len(p['exits'])},"
                f"{first.get('ts', '').date() if first else ''},"
                f"{first.get('reason', '')},"
                f"{first.get('price', 0):.0f}"
            )
    TRADES_CSV.write_text("\n".join(rows), encoding="utf-8")


def build_report(all_results: list[dict], strategy_id: str) -> str:
    out = StringIO()
    out.write(f"# {strategy_id} 정밀 분석 — 8 운영 종목 × 600봉\n\n")
    out.write(f"_시뮬: IntradaySimulator (전략별 _exit_plan_for_strategy 분기). 자세한 정책은 코드 참조._\n\n")

    # A. 종목별 ranking
    out.write("## A. 종목별 효율 ranking\n\n")
    out.write("| symbol | name | trades | wins/losses | win% | total_pnl | pnl/trade |\n")
    out.write("|--------|------|-------:|------------:|-----:|----------:|----------:|\n")
    sorted_results = sorted(all_results, key=lambda r: r["total_pnl"], reverse=True)
    for r in sorted_results:
        ppt = r["total_pnl"] / r["n_trades"] if r["n_trades"] else 0
        out.write(
            f"| {r['symbol']} | {r['name']} | {r['n_trades']} | "
            f"{r['wins']}W/{r['losses']}L | {r['win_rate']*100:.1f}% | "
            f"{fmt_money(r['total_pnl'])} | {fmt_money(ppt)} |\n"
        )
    # 종합
    total_trades = sum(r["n_trades"] for r in all_results)
    total_pnl = sum(r["total_pnl"] for r in all_results)
    total_wins = sum(r["wins"] for r in all_results)
    out.write(
        f"| **합계** | — | **{total_trades}** | "
        f"**{total_wins}W/{total_trades-total_wins}L** | "
        f"**{total_wins/total_trades*100:.1f}%** | "
        f"**{fmt_money(total_pnl)}** | "
        f"**{fmt_money(total_pnl/total_trades if total_trades else 0)}** |\n\n"
    )

    # B. Exit reason 분포
    out.write("## B. Exit reason 분포 (전체 trade 의 primary outcome)\n\n")
    outcome_counter: Counter = Counter()
    outcome_pnl: dict = defaultdict(float)
    for r in all_results:
        for p in r["pairs"]:
            outcome_counter[p["primary_outcome"]] += 1
            outcome_pnl[p["primary_outcome"]] += p["gross_pnl"]
    out.write("| outcome | count | % | total_pnl | mean_pnl |\n")
    out.write("|---------|------:|--:|----------:|---------:|\n")
    for outcome, cnt in outcome_counter.most_common():
        pct = cnt / total_trades * 100
        mean = outcome_pnl[outcome] / cnt
        out.write(
            f"| `{outcome}` | {cnt} | {pct:.0f}% | "
            f"{fmt_money(outcome_pnl[outcome])} | {fmt_money(mean)} |\n"
        )
    out.write("\n")

    # C. Winning vs Losing 분석
    out.write("## C. Winning vs Losing trade 비교\n\n")
    wins = [p for r in all_results for p in r["pairs"] if p["is_win"]]
    losses = [p for r in all_results for p in r["pairs"] if not p["is_win"]]

    def stats(trades):
        if not trades:
            return {"n": 0, "mean_pnl": 0, "mean_hold": 0, "sum_pnl": 0}
        pnls = [p["gross_pnl"] for p in trades]
        holds = [p["hold_days"] for p in trades]
        return {
            "n": len(trades),
            "mean_pnl": sum(pnls) / len(pnls),
            "mean_hold": sum(holds) / len(holds),
            "sum_pnl": sum(pnls),
        }

    ws = stats(wins)
    ls = stats(losses)
    out.write("| 분류 | count | mean PnL | mean hold(days) | total PnL |\n")
    out.write("|------|------:|---------:|----------------:|----------:|\n")
    out.write(
        f"| **Winning** | {ws['n']} | {fmt_money(ws['mean_pnl'])} | "
        f"{ws['mean_hold']:.1f} | {fmt_money(ws['sum_pnl'])} |\n"
    )
    out.write(
        f"| **Losing** | {ls['n']} | {fmt_money(ls['mean_pnl'])} | "
        f"{ls['mean_hold']:.1f} | {fmt_money(ls['sum_pnl'])} |\n\n"
    )

    if ws["mean_pnl"] and ls["mean_pnl"]:
        rr = abs(ws["mean_pnl"] / ls["mean_pnl"])
        out.write(f"**Reward/Risk ratio (mean)**: {rr:.2f}:1")
        if rr < 1:
            out.write(" ⚠️ — losing 평균 손실이 winning 평균 수익보다 큼\n\n")
        else:
            out.write("\n\n")

    # D. 종목별 손실 패턴 (큰 손실 종목)
    out.write("## D. 손실 종목 상세\n\n")
    losing_symbols = [r for r in all_results if r["total_pnl"] < 0]
    losing_symbols.sort(key=lambda r: r["total_pnl"])
    for r in losing_symbols:
        out.write(f"### {r['symbol']} {r['name']} — total {fmt_money(r['total_pnl'])} ({r['n_trades']} trades)\n\n")
        out.write("| # | entry | exit (first) | reason | hold | gross_pnl |\n")
        out.write("|---|-------|-------------|--------|-----:|----------:|\n")
        for i, p in enumerate(r["pairs"], 1):
            ext = p["exits"][0] if p["exits"] else None
            ext_str = f"{ext['ts'].date()} @ {ext['price']:,.0f}" if ext else "—"
            reason = p["primary_outcome"]
            out.write(
                f"| {i} | {p['entry_ts'].date()} @ {p['entry_price']:,.0f} | "
                f"{ext_str} | `{reason}` | {p['hold_days']}d | "
                f"{fmt_money(p['gross_pnl'])} |\n"
            )
        out.write("\n")

    # E. 종목별 수익 종목 (positive)
    out.write("## E. 수익 종목 (참고)\n\n")
    pos = [r for r in all_results if r["total_pnl"] >= 0]
    pos.sort(key=lambda r: r["total_pnl"], reverse=True)
    out.write("| symbol | name | n_trades | win% | total_pnl |\n")
    out.write("|--------|------|---------:|-----:|----------:|\n")
    for r in pos:
        out.write(
            f"| {r['symbol']} | {r['name']} | {r['n_trades']} | "
            f"{r['win_rate']*100:.1f}% | {fmt_money(r['total_pnl'])} |\n"
        )
    out.write("\n")

    # 결론·가설
    out.write("## 결론 · 가설 후보\n\n")
    out.write(
        f"- **합계 {total_trades} trades / {total_wins}W ({total_wins/total_trades*100:.0f}%) / "
        f"{fmt_money(total_pnl)}** — 음수 핵심 원인:\n"
    )
    sl_count = outcome_counter.get("sl_only", 0) + outcome_counter.get("tp_then_sl", 0)
    tp_count = outcome_counter.get("tp_only", 0)
    out.write(
        f"  - SL leg 포함 trade: {sl_count}/{total_trades} ({sl_count/total_trades*100:.0f}%)\n"
        f"  - TP only trade: {tp_count}/{total_trades} ({tp_count/total_trades*100:.0f}%)\n"
        f"  - R/R ratio: {abs(ws['mean_pnl']/ls['mean_pnl']):.2f}:1\n\n"
        if ws["mean_pnl"] and ls["mean_pnl"] else "\n"
    )

    out.write("**다음 가설 후보:**\n\n")
    out.write(
        "1. **TP1 도달률 vs 즉시 SL 비율** — TP1(+3%) 도달 trade 가 많다면 TP1 부분 청산 후 잔여를 trailing\n"
        "2. **종목별 변동성에 따라 SL 임계 조정** — sf_zone 처럼 일부 종목에 ATR SL 적용 (단 sf_zone 패턴 그대로면 R:R 깨질 위험)\n"
        "3. **진입 시점 시그널 강도(score)와 결과 상관관계** — score 낮은 진입 거부\n"
        "4. **breakeven 트리거 활용** — 현재 +1% 도달 시 SL을 entry+0 으로 이동 (이미 구현됨, 효과 검증)\n"
        "5. **f_zone 적용 종목 풀 제한** — winning ratio 70%+ 종목 군에만 적용\n"
    )
    return out.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description="전략별 정밀 분석 (8 종목 × 600봉)")
    ap.add_argument("--strategy", required=True,
                    choices=["f_zone", "sf_zone", "gold_zone", "swing_38", "scalping_consensus"],
                    help="분석할 strategy_id")
    ap.add_argument("--position-value", type=float, default=None,
                    help="S1: 종목당 명목 가치 고정 (예: 1000000). None 이면 100주 고정.")
    ap.add_argument("--suffix", default="",
                    help="출력 파일명 접미사 (예: '_S1')")
    args = ap.parse_args()
    strategy_id = args.strategy

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_md = OUT_DIR / f"{strategy_id.upper()}_ANALYSIS{args.suffix}.md"
    trades_csv = OUT_DIR / f"{strategy_id}_all_trades{args.suffix}.csv"

    all_results = []
    for sym, name in SYMBOLS.items():
        try:
            r = analyze_symbol(sym, name, strategy_id, position_value=args.position_value)
            all_results.append(r)
            print(f"[OK] {sym} {name:<10} trades={r['n_trades']:>3} "
                  f"win={r['wins']:>3} pnl={fmt_money(r['total_pnl'])}")
        except FileNotFoundError:
            print(f"[SKIP] {sym}")
        except Exception as e:
            print(f"[ERR ] {sym}: {type(e).__name__}: {e}")

    if not all_results:
        return 2

    # write_trades_csv 가 module-level TRADES_CSV 를 참조하지 않도록 inline
    rows = [
        "symbol,name,entry_date,entry_price,qty,primary_outcome,hold_days,gross_pnl,n_exits,"
        "first_exit_date,first_exit_reason,first_exit_price"
    ]
    for r in all_results:
        for p in r["pairs"]:
            first = p["exits"][0] if p["exits"] else {}
            rows.append(
                f"{r['symbol']},{r['name']},{p['entry_ts'].date()},"
                f"{p['entry_price']:.0f},{p['qty']:.0f},"
                f"{p['primary_outcome']},{p['hold_days']},{p['gross_pnl']:+.0f},"
                f"{len(p['exits'])},"
                f"{first.get('ts', '').date() if first else ''},"
                f"{first.get('reason', '')},"
                f"{first.get('price', 0):.0f}"
            )
    trades_csv.write_text("\n".join(rows), encoding="utf-8")
    report = build_report(all_results, strategy_id)
    report_md.write_text(report, encoding="utf-8")
    print(f"\nMD : {report_md.relative_to(ROOT)}")
    print(f"CSV: {trades_csv.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
