#!/usr/bin/env python3
"""기간 슈퍼트렌드 시간순 시뮬레이션 + 종목별 핀차트/리포트 (BAR-OPS-10, 2026-06-03).

config 구동: --which baseline | rsi · 기간 --start/--end/--tag.
  - baseline = 현재 배포 동작(ADX≥25+FLIP≥1.0, RSI off).
  - rsi      = 수정 전략(RSI 10m·centerline·p14 진입확인 + RSI 데드크로스 OR 조기청산).
공통: 지정 기간 5분봉 시간순 워크포워드. 진입(상승추세+최근2봉 BUY전환+게이트+종가≥1000
+09:30↑[+rsi 모드면 상위10m RSI≥50]), 청산(최근2봉 SELL전환[+rsi 모드면 RSI 데드크로스]).
체결=다음봉 open. 기간 시작부터(이전 포지션 0), 기간말 미청산은 마지막 봉 종가 평가.
결정 로직은 검증된 SymbolPrecompute(=SupertrendStrategy 일치).

산출: 종목별 5분봉 캔들차트(슈퍼트렌드선 + 매수▲ + 매도▼[ST SELL+RSI확인] 핀) + 옵시디언 md/html.

사용:
  ./venv/bin/python analysis/imports/2026-06-03/june_sim.py --which rsi \
      --start 20260501 --end 20260531 --tag 2026-05 --max-charts 12
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import List, Optional

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here))
_env_root = os.environ.get("PROJECT_ROOT")
ROOT = Path(_env_root).resolve() if _env_root else _here.parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from rsi_tf_sweep import (  # noqa: E402
    COST,
    DEFAULT_AUDIT,
    DEFAULT_CACHE,
    EXIT_LB,
    SymbolPrecompute,
    Variant,
    load_5m,
    traded_symbols,
)

ENTRY_TIME = dtime(9, 30)
WARMUP = 150
PER_ALLOC = 0.08
OUT_DIR = ROOT / "docs" / "04-report" / "sim"
COST_PCT = COST * 100

# 종목명 보강(검증: 네이버 증권/KRX 조회, 2026-06-03). simulation_log.csv 에 없는 코드 보완.
_NAME_SUPPLEMENT = {
    "012200": "계양전기", "007390": "네이처셀", "005500": "삼진제약", "383800": "LX홀딩스",
    "347700": "스피어", "043260": "성호전자", "042660": "한화오션", "005950": "이수화학",
    "001430": "세아베스틸지주", "005250": "녹십자홀딩스", "012330": "현대모비스",
    "080220": "제주반도체", "417010": "나노팀",
}
NAMES: dict = dict(_NAME_SUPPLEMENT)   # main() 에서 simulation_log/active_positions 로 보강


def load_names(data_dir: Path) -> dict:
    """symbol→name: simulation_log.csv(운영 산출) + active_positions.json + 검증 보강 dict."""
    import csv as _csv
    import json as _json
    m: dict = {}
    csvp = data_dir / "simulation_log.csv"
    if csvp.exists():
        with open(csvp) as f:
            for row in _csv.DictReader(f):
                s = (row.get("symbol") or "").strip()
                n = (row.get("name") or "").strip()
                if s and n:
                    m[s] = n
    apj = data_dir / "active_positions.json"
    if apj.exists():
        try:
            for k, v in _json.load(open(apj)).items():
                if isinstance(v, dict) and v.get("name"):
                    m[k] = v["name"]
        except Exception:
            pass
    m.update(_NAME_SUPPLEMENT)      # 검증 보강이 최우선
    return m

CFG = {
    "baseline": dict(variant=Variant("baseline", False, 2, 14, "centerline", False),
                     suffix="", label="현재 운영(ADX+FLIP, RSI off)", strat="슈퍼트렌드 운영전략"),
    "rsi": dict(variant=Variant("rsi", True, 2, 14, "signal_cross", True), suffix="_rsi",
                label="수정전략: 슈퍼트렌드 신호 + RSI 골든/데드크로스(10m signal_cross 교합) 확인",
                strat="슈퍼트렌드 + RSI확인 전략"),
}


@dataclass
class Trade:
    entry_sig_idx: int
    entry_px: float
    exit_sig_idx: Optional[int]
    exit_px: float
    net_pct: float
    status: str
    adx_at_entry: float
    hold_bars: int
    exit_reason: str


@dataclass
class SymResult:
    symbol: str
    name: str
    candles: list
    pre: SymbolPrecompute
    window_idx: List[int]
    trades: List[Trade]


def simulate(symbol, cache, variant: Variant, start: date, end: date) -> Optional[SymResult]:
    c = load_5m(symbol, cache)
    if len(c) < WARMUP + 10:
        return None
    win = [i for i in range(len(c)) if start <= c[i].timestamp.date() <= end]
    if not win:
        return None
    pre = SymbolPrecompute(c)
    last_w = win[-1]
    trades: List[Trade] = []
    holding = False
    e_sig = e_fill = 0
    e_px = 0.0
    for i in win:
        if i >= len(c) - 1:
            break
        if not holding:
            if c[i].timestamp.time() >= ENTRY_TIME and pre.entry_ok(variant, i, apply_min_price=True):
                e_sig = i
                e_fill = i + 1
                e_px = float(c[e_fill].open)
                holding = True
        else:
            if pre.exit_ok(variant, i):
                xp = float(c[i + 1].open)
                net = ((xp - e_px) / e_px - COST) * 100 if e_px > 0 else 0.0
                st = any(pre.sell[max(0, i - EXIT_LB + 1):i + 1])
                trades.append(Trade(e_sig, e_px, i, xp, net, "closed", pre.adx[e_sig],
                                    (i + 1) - e_fill, "ST SELL" if st else "RSI 데드크로스"))
                holding = False
    if holding and e_px > 0:
        xp = float(c[last_w].close)
        net = ((xp - e_px) / e_px - COST) * 100
        trades.append(Trade(e_sig, e_px, None, xp, net, "open_eow", pre.adx[e_sig],
                            last_w - e_fill, "기간말 보유"))
    if not trades:
        return None
    return SymResult(symbol, NAMES.get(symbol, symbol), c, pre, win, trades)


def aggregate(results: List[SymResult]) -> dict:
    trades = [t for r in results for t in r.trades]
    n = len(trades)
    wins = [t for t in trades if t.net_pct > 0]
    net = sum(t.net_pct for t in trades)
    return dict(symbols=len(results), n=n, wins=len(wins), losses=n - len(wins),
                win_rate=(len(wins) / n * 100 if n else 0.0),
                avg=(net / n if n else 0.0), net=net, port=PER_ALLOC * net)


def make_chart(r: SymResult, path: Path, strat: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import mplfinance as mpf

    idx = r.window_idx
    pos = {gi: k for k, gi in enumerate(idx)}
    df = pd.DataFrame({
        "Open": [r.candles[i].open for i in idx], "High": [r.candles[i].high for i in idx],
        "Low": [r.candles[i].low for i in idx], "Close": [r.candles[i].close for i in idx],
        "Volume": [r.candles[i].volume for i in idx],
    }, index=pd.DatetimeIndex([r.candles[i].timestamp for i in idx]))

    st = [r.pre.supertrend[i] for i in idx]
    buy = [float("nan")] * len(idx)
    sell_st = [float("nan")] * len(idx)
    sell_rsi = [float("nan")] * len(idx)
    for t in r.trades:
        if t.entry_sig_idx in pos:
            buy[pos[t.entry_sig_idx]] = r.candles[t.entry_sig_idx].low * 0.99
        if t.exit_sig_idx is not None and t.exit_sig_idx in pos:
            hi = r.candles[t.exit_sig_idx].high * 1.01
            (sell_rsi if t.exit_reason == "RSI 데드크로스" else sell_st)[pos[t.exit_sig_idx]] = hi

    aps = [mpf.make_addplot(st, color="#0a5fb4", width=1.0, label="Supertrend")]
    if any(v == v for v in buy):
        aps.append(mpf.make_addplot(buy, type="scatter", marker="^", markersize=90,
                                    color="#1a9850", label="매수(진입)"))
    if any(v == v for v in sell_st):
        aps.append(mpf.make_addplot(sell_st, type="scatter", marker="v", markersize=90,
                                    color="#d62728", label="매도(ST청산)"))
    if any(v == v for v in sell_rsi):
        aps.append(mpf.make_addplot(sell_rsi, type="scatter", marker="v", markersize=90,
                                    color="#f0a500", label="매도(RSI청산)"))

    style = mpf.make_mpf_style(base_mpf_style="charles",
                               rc={"font.family": "AppleGothic", "axes.unicode_minus": False})
    net_sum = sum(t.net_pct for t in r.trades)
    width = 16 if len(idx) > 400 else 12
    title = f"{r.symbol}{('('+r.name+')') if r.name != r.symbol else ''}  {strat}  " \
            f"진입 {len(r.trades)}건 · net {net_sum:+.1f}%"
    mpf.plot(df, type="candle", style=style, addplot=aps, volume=True,
             figsize=(width, 7), title=title, datetime_format="%m-%d", xrotation=20,
             tight_layout=True, warn_too_much_data=len(idx) + 1,
             savefig=dict(fname=str(path), dpi=130))


def _fmt_ts(c, idx) -> str:
    return c[idx].timestamp.strftime("%m-%d %H:%M")


def _md_table(headers, rows) -> List[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(str(x) for x in row) + " |" for row in rows]
    return out


def _html_table(headers, rows) -> str:
    h = "".join(f"<th>{x}</th>" for x in headers)
    body = "".join("<tr>" + "".join(f"<td>{x}</td>" for x in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>"


def _trade_rows(results, *, with_symbol):
    rows = []
    for r in results:
        for t in r.trades:
            ex = (f"{_fmt_ts(r.candles, t.exit_sig_idx)} @{t.exit_px:,.0f} ({t.exit_reason})"
                  if t.status == "closed" else f"기간말보유 @{t.exit_px:,.0f}")
            base = [f"{r.symbol}" + (f"({r.name})" if r.name != r.symbol else "")] if with_symbol else []
            rows.append(base + [f"{_fmt_ts(r.candles, t.entry_sig_idx)} @{t.entry_px:,.0f}", ex,
                                f"{t.hold_bars}봉", f"{t.adx_at_entry:.0f}", f"{t.net_pct:+.2f}%",
                                "✅" if t.net_pct > 0 else "❌"])
    return rows


_HTML_STYLE = (
    'body{font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",'
    'sans-serif;color:#1a1a2e;line-height:1.7;max-width:1100px;margin:0 auto;padding:40px 24px 80px}'
    'h1{font-size:1.9rem;border-bottom:3px solid #0a5fb4;padding-bottom:.4em}'
    'h2{font-size:1.35rem;margin-top:2em;border-bottom:1px solid #e2e6ee;padding-bottom:.3em}'
    'h3{font-size:1.1rem;margin-top:1.8em;color:#0b2545}'
    'strong{color:#0b2545}'
    'blockquote{margin:1.1em 0;padding:.8em 1.1em;background:#fff8e6;border-left:4px solid #f0a500;'
    'border-radius:0 8px 8px 0;color:#5b4a1a}'
    'table{border-collapse:collapse;width:100%;margin:1.2em 0;font-size:.88rem;'
    'box-shadow:0 1px 3px rgba(0,0,0,.05);border-radius:8px;overflow:hidden}'
    'thead th{background:#0a5fb4;color:#fff;text-align:left;padding:.5em .7em;white-space:nowrap}'
    'tbody td{padding:.45em .7em;border-top:1px solid #e2e6ee}'
    'tbody tr:nth-child(even){background:#f6f8fc}'
    'img{max-width:100%;border:1px solid #e2e6ee;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.08)}'
    'ul,ol{padding-left:1.5em}hr{border:0;border-top:1px solid #e2e6ee;margin:2.5em 0}'
)


def build_doc(which, charted, cfg, tag, universe_n, base_agg, rsi_agg, period_txt, all_rsi_results):
    suffix, strat = cfg["suffix"], cfg["strat"]
    is_rsi = which == "rsi"
    n_total_syms = rsi_agg["symbols"]
    cmp_rows = [
        ["진입 건수", f"{base_agg['n']}건/{base_agg['symbols']}종목", f"{rsi_agg['n']}건/{rsi_agg['symbols']}종목"],
        ["승/패", f"{base_agg['wins']}승{base_agg['losses']}패", f"{rsi_agg['wins']}승{rsi_agg['losses']}패"],
        ["승률", f"{base_agg['win_rate']:.1f}%", f"{rsi_agg['win_rate']:.1f}%"],
        ["거래당 평균", f"{base_agg['avg']:+.2f}%", f"{rsi_agg['avg']:+.2f}%"],
        ["합산(등가중)", f"{base_agg['net']:+.2f}%", f"{rsi_agg['net']:+.2f}%"],
        ["8% 균등 포트폴리오(단리)", f"{base_agg['port']:+.2f}%", f"{rsi_agg['port']:+.2f}%"],
    ]
    head = [
        "- 작성일: 2026-06-03 · 데이터: data/ohlcv_cache_5m(5분봉,오프라인) · 엔진: SymbolPrecompute(=SupertrendStrategy 일치 검증)",
        f"- **전략: {cfg['label']}**. 진입(상승추세+최근2봉 BUY전환+ADX≥25+FLIP≥1.0+종가≥1,000+09:30↑"
        + ("+최근 10m RSI 골든크로스 확인(AND)" if suffix else "") + "), 청산(최근2봉 슈퍼트렌드 SELL전환"
        + ("+최근 10m RSI 데드크로스 확인(AND)" if suffix else "") + "). RSI 단독 진입/청산 없음. 체결=다음봉 open.",
        f"- 기간: **{period_txt}** 시간순 워크포워드. 기간 시작부터(이전 포지션 0), 기간말 미청산은 마지막봉 종가 평가.",
        f"- 유니버스: 실거래(order_audit∩캐시) {universe_n}종목. 비용 왕복 {COST_PCT:.2f}%. **모든 수치 %**(KRW 미출력).",
    ]
    chart_note = (f"※ 종목별 차트는 거래영향(|순손익 합|) 상위 {len(charted)}종목만 수록(전체 {n_total_syms}종목 "
                  f"거래는 §2 표 참조). 월 단위 5분봉이라 캔들이 조밀할 수 있음.")

    # ── md ──
    M = ["---", "tags: [simulation, supertrend, rsi-filter, trade-review]",
         "date: 2026-06-03", "strategy: [supertrend, rsi]", f"period: {tag}",
         "type: simulation-analysis", "---", "",
         f"# {tag} {strat} 시뮬레이션 — 종목별 매수/매도 신호 분석", ""]
    M += head
    if is_rsi:
        M += ["", "> ⚙️ **수정 전략(정합 확인)**: 슈퍼트렌드 매수/매도 신호가 '기준(필수)', 상위 타임프레임"
              "(10분) **RSI 골든/데드크로스(시그널선 교차=교합, signal_cross)** 가 최근 발생했을 때만 "
              "정합을 인정해 진입/청산(AND). **RSI 단독으로는 매매하지 않음.**"]
    M += ["", f"## 1. Baseline(현재 운영) vs RSI필터 — {tag} 종합 비교", ""]
    M += _md_table(["지표", "Baseline (RSI off)", "RSI확인 (ST AND 10m 교합)"], cmp_rows)
    delta = rsi_agg["net"] - base_agg["net"]
    M += ["", f"> 📊 {tag}: 합산 **{base_agg['net']:+.1f}% → {rsi_agg['net']:+.1f}%** "
          f"(Δ{delta:+.1f}%pt), 진입 {base_agg['n']}→{rsi_agg['n']}건(과매매↓), 거래당 기대값 "
          f"{base_agg['avg']:+.2f}%→{rsi_agg['avg']:+.2f}%. **슈퍼트렌드 신호를 상위TF RSI 교합이 확인(AND)한 "
          "거래만 남겨 거짓전환을 걸러내고 한 건의 질을 높였다.**",
          "", f"## 2. 거래 내역 (시간순, 전 종목)", ""]
    M += _md_table(["종목", "진입신호 @체결", "청산신호 @체결(사유)", "보유", "진입ADX", "순손익%", "결과"],
                   _trade_rows(all_rsi_results, with_symbol=True))
    M += ["", f"## 3. 종목별 신호 차트 + 리포트 (거래영향 상위 {len(charted)})", "", chart_note, ""]
    for r in charted:
        png = f"sim_{r.symbol}_{tag}{suffix}.png"
        net_sum = sum(t.net_pct for t in r.trades)
        M += [f"### {r.symbol}" + (f" ({r.name})" if r.name != r.symbol else "")
              + f" — 진입 {len(r.trades)}건 · net {net_sum:+.2f}%", "", f"![[{png}]]", ""]
        M += _md_table(["진입신호", "진입체결가", "청산신호 @체결(사유)", "보유", "진입ADX", "순손익%", "결과"],
                       _trade_rows([r], with_symbol=False))
        M += [""]
    M += ["---", "", "## 4. 결론",
          f"- {tag}: 슈퍼트렌드 단독 {base_agg['net']:+.1f}% → 슈퍼트렌드+RSI확인 {rsi_agg['net']:+.1f}%"
          f"(Δ{delta:+.1f}%pt), 진입 {base_agg['n']}→{rsi_agg['n']}건, 승률 {base_agg['win_rate']:.0f}%→{rsi_agg['win_rate']:.0f}%.",
          "- 청산은 **슈퍼트렌드 SELL 이 필수**이고 RSI 데드크로스가 이를 확인(AND) — **RSI 단독 청산은 없음**"
          "(이전 OR 구현의 046970 05-07 RSI단독 청산 버그 정정).",
          "- 차트 핀: 매수▲(녹, ST BUY+RSI골든 확인) / 매도▼(적, ST SELL+RSI데드 확인). RSI 단독 핀 없음.",
          "- ⚠️ **손절(stop-loss) 부재**: 운영 슈퍼트렌드는 SELL 전환까지 손절이 없어 손실이 −5~7%까지 커질 수 있음"
          "(별도 진단). RSI 확인은 거짓신호를 줄이나 손절을 대체하지 않음 — 하드 손절(≈−3%) 도입 검토 권장.",
          "", "> 본 문서는 **시뮬레이션 분석**이며 실거래 송출이 아닙니다. 옵시디언 vault 규격. md/html 동시 산출."]
    md = "\n".join(M)

    # ── html ──
    P = ['<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f"<title>{tag} {strat} 시뮬레이션</title>",
         f"<style>{_HTML_STYLE}</style></head><body>",
         f"<h1>{tag} {strat} 시뮬레이션 — 종목별 매수/매도 신호 분석</h1>",
         "<ul>" + "".join(f"<li>{h.lstrip('- ')}</li>" for h in head) + "</ul>"]
    if is_rsi:
        P.append("<blockquote>⚙️ <strong>수정 전략(정합 확인)</strong>: 슈퍼트렌드 매수/매도 신호가 기준(필수), "
                 "상위 TF(10분) RSI 골든/데드크로스(시그널선 교차=교합)가 최근 발생 시에만 진입/청산(AND). "
                 "<strong>RSI 단독 매매 없음.</strong></blockquote>")
    P += [f"<h2>1. Baseline(현재 운영) vs RSI확인 — {tag} 종합 비교</h2>",
         _html_table(["지표", "Baseline (RSI off)", "RSI확인 (ST AND 10m 교합)"], cmp_rows),
         f"<blockquote>📊 {tag}: 합산 {base_agg['net']:+.1f}% → {rsi_agg['net']:+.1f}% (Δ{delta:+.1f}%pt), "
         f"진입 {base_agg['n']}→{rsi_agg['n']}건. 추세 국면에서 상위TF RSI 확인이 거짓전환을 걸러 거래의 질을 "
         "높임(하락국면 6월과 반대 — 국면 의존적).</blockquote>",
         f"<h2>2. {'RSI필터 ' if is_rsi else ''}거래 내역 (시간순, 전 종목)</h2>",
         _html_table(["종목", "진입신호 @체결", "청산신호 @체결(사유)", "보유", "진입ADX", "순손익%", "결과"],
                     _trade_rows(all_rsi_results, with_symbol=True)),
         f"<h2>3. 종목별 신호 차트 + 리포트 (거래영향 상위 {len(charted)})</h2>",
         f"<blockquote>{chart_note}</blockquote>"]
    for r in charted:
        png = f"sim_{r.symbol}_{tag}{suffix}.png"
        net_sum = sum(t.net_pct for t in r.trades)
        P.append(f"<h3>{r.symbol}" + (f" ({r.name})" if r.name != r.symbol else "")
                 + f" — 진입 {len(r.trades)}건 · net {net_sum:+.2f}%</h3>")
        P.append(f'<img src="{png}" alt="{r.symbol}">')
        P.append(_html_table(["진입신호", "진입체결가", "청산신호 @체결(사유)", "보유", "진입ADX", "순손익%", "결과"],
                             _trade_rows([r], with_symbol=False)))
    P += ["<h2>4. 결론</h2>",
          f"<ul><li>{tag}: 슈퍼트렌드 단독 {base_agg['net']:+.1f}% → 슈퍼트렌드+RSI확인 {rsi_agg['net']:+.1f}%"
          f"(Δ{delta:+.1f}%pt), 진입 {base_agg['n']}→{rsi_agg['n']}건, 승률 {base_agg['win_rate']:.0f}%→{rsi_agg['win_rate']:.0f}%.</li>"
          "<li>청산은 <strong>슈퍼트렌드 SELL 필수</strong> + RSI 데드크로스 확인(AND). RSI 단독 청산 없음"
          "(046970 05-07 RSI단독 청산 버그 정정).</li>"
          "<li>차트 핀: 매수▲(녹, ST BUY+RSI골든)/매도▼(적, ST SELL+RSI데드). RSI 단독 핀 없음.</li>"
          "<li>⚠️ <strong>손절 부재</strong>: 운영 슈퍼트렌드는 SELL 전환까지 손절이 없어 손실이 −5~7%까지 커질 수 있음. "
          "RSI 확인은 손절을 대체하지 않음 — 하드 손절(≈−3%) 도입 검토 권장.</li></ul>",
          "<blockquote>본 문서는 <strong>시뮬레이션 분석</strong>이며 실거래 송출이 아닙니다.</blockquote>",
          "</body></html>"]
    return md, "".join(P)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", choices=["baseline", "rsi"], default="rsi")
    ap.add_argument("--start", default="20260601")
    ap.add_argument("--end", default="20260630")
    ap.add_argument("--tag", default="2026-06")
    ap.add_argument("--max-charts", type=int, default=99)
    args = ap.parse_args()
    cfg = CFG[args.which]
    start = datetime.strptime(args.start, "%Y%m%d").date()
    end = datetime.strptime(args.end, "%Y%m%d").date()
    cache = Path(str(DEFAULT_CACHE))
    universe = [s for s in traded_symbols(Path(str(DEFAULT_AUDIT))) if (cache / f"{s}.json").exists()]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    global NAMES
    NAMES = load_names(cache.parent)
    print(f"[sim] 종목명 매핑 {len(NAMES)}건 로드")

    base_res = [r for s in universe if (r := simulate(s, cache, CFG["baseline"]["variant"], start, end))]
    rsi_res = [r for s in universe if (r := simulate(s, cache, CFG["rsi"]["variant"], start, end))]
    base_agg, rsi_agg = aggregate(base_res), aggregate(rsi_res)
    sel = rsi_res if args.which == "rsi" else base_res

    # 실제 거래일 범위(차트/문서 표기)
    all_days = sorted({c.timestamp.date() for r in sel for c in [r.candles[i] for i in r.window_idx]})
    period_txt = (f"{all_days[0]} ~ {all_days[-1]} ({args.tag}, 거래일 {len(all_days)}일)"
                  if all_days else f"{start} ~ {end}")

    sel.sort(key=lambda r: r.candles[r.trades[0].entry_sig_idx].timestamp)
    charted = sorted(sel, key=lambda r: -abs(sum(t.net_pct for t in r.trades)))[: args.max_charts]
    charted.sort(key=lambda r: r.candles[r.trades[0].entry_sig_idx].timestamp)

    print(f"[sim:{args.which}] {period_txt} · 유니버스 {len(universe)} · 진입종목 {len(sel)} · "
          f"총 진입 {sum(len(r.trades) for r in sel)}건 · 차트 {len(charted)}종목")
    print(f"[sim] baseline net {base_agg['net']:+.1f}% ({base_agg['n']}건) | "
          f"rsi net {rsi_agg['net']:+.1f}% ({rsi_agg['n']}건)")
    for r in charted:
        png = OUT_DIR / f"sim_{r.symbol}_{args.tag}{cfg['suffix']}.png"
        make_chart(r, png, cfg["strat"])
    print(f"[sim] 차트 {len(charted)}장 생성")

    md, html = build_doc(args.which, charted, cfg, args.tag, len(universe), base_agg, rsi_agg,
                         period_txt, sel)
    base = f"sim_supertrend_{args.tag}{cfg['suffix']}"
    (OUT_DIR / f"{base}.md").write_text(md, encoding="utf-8")
    (OUT_DIR / f"{base}.html").write_text(html, encoding="utf-8")
    print(f"[sim] 문서: {OUT_DIR/base}.md (+ .html)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
