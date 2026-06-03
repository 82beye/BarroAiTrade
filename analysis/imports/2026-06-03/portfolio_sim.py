#!/usr/bin/env python3
"""포트폴리오 계좌 시뮬레이션 — 수정 전략(RSI 필터) vs 현재 운영 (BAR-OPS-10, 2026-06-03).

rsi_tf_sweep.py 의 풀링 통계(모든 신호 다 잡음)와 달리, **운영 사이징을 그대로 모사**:
  - 단일 자본풀, 최대 10종목 동시보유, 종목당 8%(=예수금 80%÷10) 균등 고정배분.
  - 슬롯(10) 가득 차면 신규 신호는 **건너뜀**(현실 제약) → 거래수·수익이 sweep 보다 보수적.
  - 진입/청산 결정은 검증된 SymbolPrecompute(=SupertrendStrategy 와 일치) 재사용,
    체결은 다음봉 open, 비용 왕복 0.21%. 포트폴리오 단리 수익률(%) + equity curve.

비교 설정:
  - Baseline (NO_RSI)            : 현재 운영(ADX≥25 + FLIP≥1.0).
  - RSI 10m centerline p14 (+exit): 기본 후보(베이크된 기본값, opt-in 시).
  - RSI 5m  centerline p14 (entry): 운영컷 총수익 최고 RSI 변형.

사용:
  ./venv/bin/python analysis/imports/2026-06-03/portfolio_sim.py [--max-symbols N] [--max-pos 10]
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Tuple

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here))                       # rsi_tf_sweep import
_env_root = os.environ.get("PROJECT_ROOT")
ROOT = Path(_env_root).resolve() if _env_root else _here.parents[2]
sys.path.insert(0, str(ROOT))

from rsi_tf_sweep import (  # noqa: E402
    COST,
    DEFAULT_AUDIT,
    DEFAULT_CACHE,
    RSI_SIGNAL_PERIOD,
    TF_TABLE,
    SymbolPrecompute,
    Variant,
    liquidity_rank,
    load_5m,
    traded_symbols,
)

CONFIGS = [
    ("Baseline (NO_RSI, 현재 운영)", Variant("base", False, 2, 14, "signal_cross", False)),
    ("ST+RSI확인 10m 교합 (진입+청산)", Variant("c10", True, 2, 14, "signal_cross", True)),
    ("ST+RSI확인 10m 교합 (진입만)", Variant("c5", True, 2, 14, "signal_cross", False)),
]


def walk_ts(pre: SymbolPrecompute, v: Variant, warmup: int) -> List[Tuple[datetime, datetime, float]]:
    """(entry_ts, exit_ts, net_ret_pct) 라운드트립 — 결정은 SymbolPrecompute 재사용."""
    candles, n = pre.candles, pre.n
    trips: List[Tuple[datetime, datetime, float]] = []
    holding = False
    ep = 0.0
    ei = 0
    i = warmup
    while i < n - 1:
        if not holding:
            if pre.entry_ok(v, i):
                ei = i + 1
                ep = float(candles[ei].open)
                holding = True
        else:
            if pre.exit_ok(v, i):
                xp = float(candles[i + 1].open)
                net = ((xp - ep) / ep - COST) * 100 if ep > 0 else 0.0
                trips.append((candles[ei].timestamp, candles[i + 1].timestamp, net))
                holding = False
        i += 1
    if holding and ep > 0:
        xp = float(candles[-1].close)
        net = ((xp - ep) / ep - COST) * 100
        trips.append((candles[ei].timestamp, candles[-1].timestamp, net))
    return trips


def portfolio(all_trips, *, max_pos: int, per_alloc: float):
    """동시보유 max_pos 제약 하 그리디(진입시각 순) 체결. 각 포지션 per_alloc(자본%) 고정.

    반환: taken = [(exit_ts, contrib_pct, net_ret_pct)] (contrib = per_alloc × net%)."""
    trips = sorted(all_trips, key=lambda t: t[0])
    open_exits: List[datetime] = []
    taken = []
    skipped = 0
    for ent, ext, net in trips:
        open_exits = [x for x in open_exits if x > ent]   # 종료된 슬롯 해제
        if len(open_exits) >= max_pos:
            skipped += 1
            continue
        open_exits.append(ext)
        taken.append((ext, per_alloc * net, net))
    return taken, skipped


def metrics(taken, *, lo=None, hi=None):
    sub = [(ext, c, net) for (ext, c, net) in taken
           if (lo is None or ext.date() >= lo) and (hi is None or ext.date() <= hi)]
    sub.sort(key=lambda t: t[0])
    if not sub:
        return dict(n=0, ret=0.0, win=0.0, mdd=0.0, avg=0.0, curve=[])
    contribs = [c for _, c, _ in sub]
    nets = [net for _, _, net in sub]
    total = sum(contribs)
    wins = sum(1 for net in nets if net > 0)
    curve = []
    run = 0.0
    peak = 0.0
    mdd = 0.0
    for c in contribs:
        run += c
        curve.append(run)
        peak = max(peak, run)
        mdd = max(mdd, peak - run)
    return dict(n=len(sub), ret=total, win=wins / len(sub) * 100,
                mdd=mdd, avg=statistics.mean(nets), curve=curve,
                times=[ext for ext, _, _ in sub])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    ap.add_argument("--audit", default=str(DEFAULT_AUDIT))
    ap.add_argument("--max-symbols", type=int, default=63, help="0=전체 실거래 종목")
    ap.add_argument("--max-pos", type=int, default=10)
    ap.add_argument("--per-alloc", type=float, default=0.08, help="종목당 자본 비중(80%%÷10)")
    ap.add_argument("--is-end", default="20260522")
    ap.add_argument("--out", default=str(_here / "PORTFOLIO_SIM.md"))
    ap.add_argument("--chart", default=str(_here / "portfolio_sim_equity.png"))
    args = ap.parse_args()

    cache_dir = Path(args.cache_dir)
    is_end = datetime.strptime(args.is_end, "%Y%m%d").date()
    oos_lo = date.fromordinal(is_end.toordinal() + 1)
    warmup = max(35, (14 + RSI_SIGNAL_PERIOD + 2) * max(TF_TABLE.values()))

    traded = [s for s in traded_symbols(Path(args.audit)) if (cache_dir / f"{s}.json").exists()]
    if args.max_symbols and len(traded) > args.max_symbols:
        traded = traded[: args.max_symbols]
    basket = traded
    print(f"[psim] 운영(실거래) 바스켓 {len(basket)}종목 · 최대보유 {args.max_pos} · "
          f"종목당 {args.per_alloc*100:.0f}% · warmup {warmup}봉 · IS≤{is_end}")

    # 종목별 precompute 1회 (모든 config 공유)
    pres = {}
    for sym in basket:
        c = load_5m(sym, cache_dir)
        if len(c) >= warmup + 50:
            pres[sym] = SymbolPrecompute(c)
    print(f"[psim] precompute {len(pres)}종목")

    results = []
    for label, v in CONFIGS:
        all_trips = []
        for sym, pre in pres.items():
            all_trips.extend(walk_ts(pre, v, warmup))
        taken, skipped = portfolio(all_trips, max_pos=args.max_pos, per_alloc=args.per_alloc)
        full = metrics(taken)
        ins = metrics(taken, hi=is_end)
        oos = metrics(taken, lo=oos_lo)
        results.append((label, len(all_trips), skipped, taken, full, ins, oos))
        print(f"[psim] {label}: 신호 {len(all_trips)} / 체결 {full['n']} (슬롯부족 skip {skipped}) "
              f"· 수익 {full['ret']:+.1f}% · 승률 {full['win']:.1f}% · MDD {full['mdd']:.1f}%pt "
              f"· IS {ins['ret']:+.1f}% / OOS {oos['ret']:+.1f}%")

    _write(args.out, results, basket, len(pres), args, is_end, warmup)
    _chart(args.chart, results)
    print(f"[psim] 리포트 {args.out}")
    print(f"[psim] 차트 {args.chart}")
    return 0


def _write(path, results, basket, n_used, args, is_end, warmup):
    L = []
    L.append("# 포트폴리오 계좌 시뮬레이션 — 수정 전략(RSI) vs 현재 운영 (BAR-OPS-10)")
    L.append("")
    L.append(f"- 운영 사이징 모사: 단일 자본풀 · 최대 {args.max_pos}종목 동시보유 · "
             f"종목당 {args.per_alloc*100:.0f}%(예수금 80%÷10) 고정. 슬롯 가득차면 신규신호 skip.")
    L.append(f"- 데이터: data/ohlcv_cache_5m 실거래 {n_used}종목(order_audit∩캐시) · 6주(~04-23~06-02) · "
             f"warmup {warmup}봉 · IS≤{is_end}/OOS 이후 · 비용 왕복 {COST*100:.2f}%.")
    L.append("- 진입/청산 결정 = 검증된 SupertrendStrategy 게이트(ADX≥25+FLIP≥1.0 +선택적 RSI). "
             "체결=다음봉 open. **수익률 = 단리(초기자본 대비) %** (KRW 미출력).")
    L.append("")
    L.append("## 결과 (포트폴리오 단리 수익률)")
    L.append("")
    L.append("| 설정 | 신호 | 체결(슬롯skip) | 수익률% | 승률% | 거래당% | MDD%pt | IS% | OOS% |")
    L.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for label, n_sig, skipped, taken, full, ins, oos in results:
        L.append(f"| {label} | {n_sig} | {full['n']}({skipped}) | {full['ret']:+.1f} | "
                 f"{full['win']:.1f} | {full['avg']:+.2f} | {full['mdd']:.1f} | "
                 f"{ins['ret']:+.1f} | {oos['ret']:+.1f} |")
    L.append("")
    base = results[0][4]
    L.append("## 해석")
    for label, n_sig, skipped, taken, full, ins, oos in results[1:]:
        d = full["ret"] - base["ret"]
        L.append(f"- **{label}**: 베이스 대비 Δ수익 {d:+.1f}%pt, 체결 {full['n']} vs {results[0][4]['n']}, "
                 f"MDD {full['mdd']:.1f} vs {base['mdd']:.1f}, 승률 {full['win']:.1f}% vs {base['win']:.1f}%, "
                 f"OOS {oos['ret']:+.1f}% vs {results[0][6]['ret']:+.1f}%.")
    L.append("")
    L.append("> 동시보유 10종목·8% 고정배분 제약하의 단리 시뮬. sweep(풀링·전신호)과 달리 슬롯 경쟁이 "
             "반영돼 보수적. OOS(~1.5주)는 표본이 얇아 참고용 — 라이브 활성 전 dry_run 검증 필요.")
    L.append("")
    L.append(f"_바스켓: {', '.join(basket)}_")
    Path(path).write_text("\n".join(L), encoding="utf-8")


def _chart(path, results):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        for _f in ("AppleGothic", "Apple SD Gothic Neo", "NanumGothic", "Malgun Gothic"):
            try:
                matplotlib.rcParams["font.family"] = _f
                break
            except Exception:
                continue
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception as e:
        print(f"[psim] 차트 skip (matplotlib 없음: {e})")
        return
    fig, ax = plt.subplots(figsize=(11, 6))
    for label, n_sig, skipped, taken, full, ins, oos in results:
        times = full.get("times", [])
        curve = full.get("curve", [])
        if times and curve:
            ax.plot(times, curve, label=f"{label}  ({full['ret']:+.0f}%)", linewidth=1.6)
    ax.axhline(0, color="#888", linewidth=0.8)
    ax.set_title("포트폴리오 단리 수익률 곡선 — 수정 전략(RSI) vs 현재 운영\n"
                 "(실거래 유니버스, 최대 10종목·8% 균등, 6주 5분봉)", fontsize=11)
    ax.set_xlabel("청산 시각")
    ax.set_ylabel("누적 수익률 (%, 초기자본 대비 단리)")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())
