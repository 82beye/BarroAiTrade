#!/usr/bin/env python3
"""일자별 계좌 잔고 증감 추이 — 슈퍼트렌드+RSI확인, 8% 균등 포트폴리오 (BAR-OPS-10, 2026-06-03).

현재 수정 로직(슈퍼트렌드 매수/매도 신호 + 상위10m RSI 교합 확인 AND, RSI 단독 매매 없음)으로
2026-05-01 ~ 06-02 를 시간순 연속 진행했을 때 **계좌 잔고(초기자본 대비 %)의 일자별 증감**을 산출.

포트폴리오 모델(운영 사이징 모사):
  - 종목당 8% 균등배분(= 예수금 80% ÷ 10종목), 최대 10종목 동시보유. 슬롯 차면 신규 신호 skip.
  - 단리(초기자본 대비). 진입/청산 = 다음봉 open. 비용 왕복 0.21%.
  - 일별 계좌% = 100 + 실현손익누계 + 보유종목 평가손익(그날 종가 MTM).
모든 수치 % (KRW 미출력). 결정 로직은 검증된 SymbolPrecompute(=SupertrendStrategy 일치).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Dict, List

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here))
_env_root = os.environ.get("PROJECT_ROOT")
ROOT = Path(_env_root).resolve() if _env_root else _here.parents[2]
sys.path.insert(0, str(ROOT))

from rsi_tf_sweep import (  # noqa: E402
    COST, DEFAULT_AUDIT, DEFAULT_CACHE, SymbolPrecompute, Variant,
    load_5m, traded_symbols,
)

ENTRY_TIME = dtime(9, 30)
WARMUP = 150
PER_ALLOC = 0.08
MAX_POS = 10
BUY_COST = 0.00015
TRAIL_ATR = 0.0      # ATR 트레일링 청산 배수(0=비활성). --trail 로 설정(권장 4.0). 종가기준 샹들리에.
RSI = Variant("rsi", True, 2, 14, "signal_cross", True)   # 수정 로직: 교합 + AND
OUT_DIR = ROOT / "docs" / "04-report" / "sim"
NAMES = {"012200": "계양전기"}


@dataclass
class Trip:
    sym: str
    entry_ts: datetime
    entry_px: float
    exit_ts: datetime
    exit_px: float
    net_pct: float       # 청산 net% (왕복비용 차감)


def walk(pre: SymbolPrecompute, start: date, end: date) -> List[Trip]:
    c = pre.candles
    win = [i for i in range(len(c)) if start <= c[i].timestamp.date() <= end]
    if not win:
        return []
    last = win[-1]
    trips: List[Trip] = []
    holding = False
    e_fill = 0
    e_px = 0.0
    peakc = 0.0
    for i in win:
        if i >= len(c) - 1:
            break
        if not holding:
            if c[i].timestamp.time() >= ENTRY_TIME and pre.entry_ok(RSI, i, apply_min_price=True):
                e_fill = i + 1
                e_px = float(c[e_fill].open)
                peakc = float(c[e_fill].close)
                holding = True
        else:
            # ATR 트레일링(샹들리에, 종가기준, 우선) — 고점종가 −k×ATR 이탈 시 다음봉 시가 청산.
            peakc = max(peakc, float(c[i].close))
            if TRAIL_ATR > 0 and pre.atr[i] > 0 and float(c[i].close) <= peakc - TRAIL_ATR * pre.atr[i]:
                xp = float(c[i + 1].open)
                trips.append(Trip(c[0].symbol, c[e_fill].timestamp, e_px,
                                  c[i + 1].timestamp, xp, ((xp - e_px) / e_px - COST) * 100))
                holding = False
                continue
            if pre.exit_ok(RSI, i):
                xp = float(c[i + 1].open)
                trips.append(Trip(c[0].symbol, c[e_fill].timestamp, e_px,
                                  c[i + 1].timestamp, xp, ((xp - e_px) / e_px - COST) * 100))
                holding = False
    if holding and e_px > 0:
        xp = float(c[last].close)
        trips.append(Trip(c[0].symbol, c[e_fill].timestamp, e_px, c[last].timestamp, xp,
                          ((xp - e_px) / e_px - COST) * 100))
    return trips


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260501")
    ap.add_argument("--end", default="20260602")
    ap.add_argument("--max-symbols", type=int, default=0)
    ap.add_argument("--capital", type=float, default=38_000_000.0, help="초기 자본금(원)")
    ap.add_argument("--trail", type=float, default=0.0, help="ATR 트레일링 청산 배수(0=비활성, 권장 4.0)")
    args = ap.parse_args()
    cap = args.capital
    global TRAIL_ATR
    TRAIL_ATR = args.trail
    start = datetime.strptime(args.start, "%Y%m%d").date()
    end = datetime.strptime(args.end, "%Y%m%d").date()
    cache = Path(str(DEFAULT_CACHE))
    universe = [s for s in traded_symbols(Path(str(DEFAULT_AUDIT))) if (cache / f"{s}.json").exists()]
    if args.max_symbols:
        universe = universe[: args.max_symbols]

    # 종목별 라운드트립 + 일별 종가
    all_trips: List[Trip] = []
    dclose: Dict[str, Dict[date, float]] = {}
    trading_days = set()
    for s in universe:
        c = load_5m(s, cache)
        if len(c) < WARMUP + 10:
            continue
        pre = SymbolPrecompute(c)
        all_trips.extend(walk(pre, start, end))
        dm = {cd.timestamp.date(): float(cd.close) for cd in c if start <= cd.timestamp.date() <= end}
        dclose[s] = dm
        trading_days.update(dm.keys())
    days = sorted(trading_days)

    # 동시보유 10 제약 (진입시각 순 그리디) → taken
    all_trips.sort(key=lambda t: t.entry_ts)
    open_exits: List[datetime] = []
    taken: List[Trip] = []
    skipped = 0
    for t in all_trips:
        open_exits = [x for x in open_exits if x > t.entry_ts]
        if len(open_exits) >= MAX_POS:
            skipped += 1
            continue
        open_exits.append(t.exit_ts)
        taken.append(t)

    # 일별 계좌% = 100 + 실현누계 + 보유 평가손익(그날 종가 MTM). 금액 = % × 자본금/100 (단리·선형).
    print(f"[psim-daily] {start}~{end} · 자본금 {cap:,.0f}원 · 종목당 8%({cap*PER_ALLOC:,.0f}원)·최대{MAX_POS} · "
          f"체결 {len(taken)}건(skip {skipped})")
    print(f"{'일자':<12}{'당일증감(원)':>16}{'누적손익(원)':>16}{'계좌잔고(원)':>16}{'보유':>4}{'당일%':>8}{'누적%':>8}")
    rows = []
    prev_eq = 100.0
    for d in days:
        realized = sum(PER_ALLOC * t.net_pct for t in taken if t.exit_ts.date() <= d)
        unreal = 0.0
        held = 0
        for t in taken:
            if t.entry_ts.date() <= d < t.exit_ts.date():   # d 종료 시점 보유중
                px = dclose.get(t.sym, {}).get(d)
                if px:
                    unreal += PER_ALLOC * (((px - t.entry_px) / t.entry_px) - BUY_COST) * 100
                    held += 1
        eq = 100.0 + realized + unreal       # 계좌 % (초기=100)
        chg = eq - prev_eq                   # 당일 증감 %
        cum_pct = eq - 100.0
        chg_won = chg / 100.0 * cap
        cum_won = cum_pct / 100.0 * cap
        bal_won = cap + cum_won
        rows.append((d, chg, cum_pct, held, chg_won, cum_won, bal_won))
        mark = " ←6월" if d.month == 6 and (days.index(d) == 0 or days[days.index(d) - 1].month == 5) else ""
        print(f"{str(d):<12}{chg_won:>+15,.0f}원{cum_won:>+15,.0f}원{bal_won:>15,.0f}원{held:>4}"
              f"{chg:>+7.2f}%{cum_pct:>+7.2f}%{mark}")
        prev_eq = eq
    final_pct = rows[-1][2] if rows else 0.0
    final_won = rows[-1][5] if rows else 0.0
    print(f"[psim-daily] 최종(단리): {final_pct:+.2f}% · 손익 {final_won:+,.0f}원 · "
          f"잔고 {cap+final_won:,.0f}원 · 거래 {len(taken)}건")
    _chart(rows, start, end, final_pct, final_won, cap, len(taken))
    _write_report(rows, start, end, cap, final_pct, final_won, len(taken), skipped)
    return 0


def _write_report(rows, start, end, cap, final_pct, final_won, n, skipped):
    """일자별 표(일자|당일증감|당일증감액|누적|누적금액|보유) md/html 리포트."""
    _sfx = f"_trail{TRAIL_ATR:g}" if TRAIL_ATR > 0 else ""
    _tt = f"+트레일{TRAIL_ATR:g}×ATR" if TRAIL_ATR > 0 else ""
    hdr = ["일자", "당일증감", "당일증감액", "누적", "누적금액", "보유"]

    def _row(r):
        d, chg, cum, held, chg_won, cum_won, bal_won = r
        m6 = " (6월)" if d.month == 6 else ""
        return [f"{d}{m6}", f"{chg:+.2f}%", f"{chg_won:+,.0f}원",
                f"{cum:+.2f}%", f"{cum_won:+,.0f}원", str(held)]
    body = [_row(r) for r in rows]
    intro = [
        ("- 전략: 슈퍼트렌드 신호 + RSI 교합 확인(AND)"
         + (f" + ATR 트레일링 청산({TRAIL_ATR:g}×ATR, 종가기준 샹들리에)" if TRAIL_ATR > 0
            else ". 손절/트레일 미적용")
         + (". RSI 단독 매매 없음." if TRAIL_ATR > 0 else "")),
        f"- 포트폴리오: 종목당 {PER_ALLOC*100:.0f}% 균등(={cap*PER_ALLOC:,.0f}원)·최대 {MAX_POS}종목·단리. "
        f"진입/청산=다음봉 open, 비용 왕복 {COST*100:.2f}%.",
        f"- 자본금: **{cap:,.0f}원** · 기간: {start}~{end} · 체결 {n}건(슬롯skip {skipped}).",
        f"- 계좌% = 100 + 실현손익누계 + 보유종목 평가손익(그날 종가 MTM). 금액 = % × 자본금(선형).",
    ]
    summ = (f"최종 계좌잔고 **{cap+final_won:,.0f}원** · 누적손익 **{final_won:+,.0f}원 ({final_pct:+.2f}%)**")

    # md
    M = ["---", "tags: [simulation, supertrend, rsi-filter, portfolio, daily]", "date: 2026-06-03",
         f"period: {start}/{end}", "type: simulation-analysis", "---", "",
         f"# 일자별 계좌 잔고 증감 — 슈퍼트렌드+RSI확인{_tt} · {PER_ALLOC*100:.0f}% 균등 포트폴리오", ""]
    M += intro + ["", f"> 💰 {summ}", "", f"![[daily_portfolio_krw_2026-05_06{_sfx}.png]]", "",
                  "## 일자별 손익", ""]
    M.append("| " + " | ".join(hdr) + " |")
    M.append("|" + "|".join(["---"] * len(hdr)) + "|")
    M += ["| " + " | ".join(x) + " |" for x in body]
    M += ["", "> 본 문서는 **시뮬레이션 분석**이며 실거래 송출이 아닙니다. 단리·8% 균등 기준"
          + (f"(ATR 트레일링 {TRAIL_ATR:g}×, 종가기준·다음봉 시가체결)." if TRAIL_ATR > 0 else "(손절/트레일 미적용).")]
    (OUT_DIR / f"daily_portfolio_2026-05_06{_sfx}.md").write_text("\n".join(M), encoding="utf-8")

    # html
    style = ('body{font-family:-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;'
             'color:#1a1a2e;line-height:1.7;max-width:900px;margin:0 auto;padding:40px 24px 80px}'
             'h1{font-size:1.7rem;border-bottom:3px solid #0a5fb4;padding-bottom:.4em}'
             'h2{font-size:1.3rem;margin-top:1.8em;border-bottom:1px solid #e2e6ee;padding-bottom:.3em}'
             'blockquote{margin:1.1em 0;padding:.8em 1.1em;background:#eef6ff;border-left:4px solid #0a5fb4;'
             'border-radius:0 8px 8px 0}strong{color:#0b2545}'
             'table{border-collapse:collapse;width:100%;margin:1em 0;font-size:.9rem}'
             'thead th{background:#0a5fb4;color:#fff;padding:.5em .7em;text-align:right}'
             'thead th:first-child{text-align:left}tbody td{padding:.45em .7em;border-top:1px solid #e2e6ee;text-align:right}'
             'tbody td:first-child{text-align:left}tbody tr:nth-child(even){background:#f6f8fc}'
             'img{max-width:100%;border:1px solid #e2e6ee;border-radius:10px}ul{padding-left:1.4em}')
    def _td(x, neg_color=True):
        c = ""
        if neg_color and ("-" in x and "원" in x or x.startswith("-")):
            c = ' style="color:#d62728"'
        elif neg_color and x.startswith("+"):
            c = ' style="color:#1a7d34"'
        return f"<td{c}>{x}</td>"
    rows_html = ""
    for x in body:
        rows_html += "<tr><td>" + x[0] + "</td>" + "".join(_td(v) for v in x[1:]) + "</tr>"
    H = ['<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f"<title>일자별 계좌 잔고 증감 {start}~{end}</title><style>{style}</style></head><body>",
         f"<h1>일자별 계좌 잔고 증감 — 슈퍼트렌드+RSI확인{_tt} · {PER_ALLOC*100:.0f}% 균등 포트폴리오</h1>",
         "<ul>" + "".join(f"<li>{s.lstrip('- ')}</li>" for s in intro) + "</ul>",
         f"<blockquote>💰 {summ}</blockquote>",
         f'<img src="daily_portfolio_krw_2026-05_06{_sfx}.png" alt="daily">',
         "<h2>일자별 손익</h2>",
         "<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in hdr) + "</tr></thead><tbody>",
         rows_html, "</tbody></table>",
         "<blockquote>본 문서는 시뮬레이션 분석이며 실거래 송출이 아닙니다.</blockquote></body></html>"]
    (OUT_DIR / f"daily_portfolio_2026-05_06{_sfx}.html").write_text("".join(H), encoding="utf-8")
    print(f"[psim-daily] 리포트 {OUT_DIR/('daily_portfolio_2026-05_06'+_sfx)}.md (+ .html)")


def _chart(rows, start, end, final_pct, final_won, cap, n):
    try:
        import matplotlib
        matplotlib.use("Agg")
        for _f in ("AppleGothic", "Apple SD Gothic Neo", "NanumGothic"):
            try:
                matplotlib.rcParams["font.family"] = _f
                break
            except Exception:
                continue
        matplotlib.rcParams["axes.unicode_minus"] = False
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter
    except Exception as e:
        print(f"[psim-daily] 차트 skip ({e})")
        return
    xs = [r[0] for r in rows]
    bal = [r[6] for r in rows]        # 계좌잔고(원)
    daily_won = [r[4] for r in rows]  # 당일증감(원)
    man = FuncFormatter(lambda v, _: f"{v/1e4:,.0f}만")   # 원 → 만원 표기
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), height_ratios=[2, 1], sharex=True)
    ax1.plot(xs, bal, "-o", color="#0a5fb4", ms=3, lw=1.6, label="계좌 잔고(원)")
    ax1.axhline(cap, color="#888", lw=0.9, ls=":", label=f"초기 {cap/1e4:,.0f}만원")
    for i in range(1, len(xs)):
        if xs[i].month == 6 and xs[i - 1].month == 5:
            for ax in (ax1, ax2):
                ax.axvline(xs[i], color="#d62728", ls="--", lw=1, alpha=0.7)
            ax1.text(xs[i], max(bal), " 6월", color="#d62728", fontsize=9, va="top")
    _tt = f"+트레일{TRAIL_ATR:g}×ATR" if TRAIL_ATR > 0 else ""
    ax1.set_title(f"일자별 계좌 잔고 추이 — 슈퍼트렌드+RSI확인{_tt} · 8% 균등(최대10종목) · {start}~{end}\n"
                  f"초기 {cap/1e4:,.0f}만원 → 최종 {(cap+final_won)/1e4:,.0f}만원 "
                  f"(손익 {final_won:+,.0f}원, {final_pct:+.2f}%, 체결 {n}건)", fontsize=11)
    ax1.set_ylabel("계좌 잔고")
    ax1.yaxis.set_major_formatter(man)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="upper left", fontsize=9)
    colors = ["#1a9850" if v >= 0 else "#d62728" for v in daily_won]
    ax2.bar(xs, daily_won, color=colors, width=0.7)
    ax2.axhline(0, color="#888", lw=0.8)
    ax2.set_ylabel("당일 증감")
    ax2.yaxis.set_major_formatter(man)
    ax2.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    _sfx = f"_trail{TRAIL_ATR:g}" if TRAIL_ATR > 0 else ""
    p = OUT_DIR / f"daily_portfolio_krw_2026-05_06{_sfx}.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    print(f"[psim-daily] 차트 {p}")


if __name__ == "__main__":
    raise SystemExit(main())
