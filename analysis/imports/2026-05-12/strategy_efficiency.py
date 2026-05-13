#!/usr/bin/env python3
"""
2026-05-12 임포트 로그 — 전략별 효율 분석

입력:
  logs/imports/2026-05-12/simulation_log.csv   (50 entries, 4 runs)
  logs/imports/2026-05-12/order_audit.csv      (21 rows)

출력:
  analysis/imports/2026-05-12/REPORT.md
  stdout 요약
"""

from __future__ import annotations

import sys
from pathlib import Path
from io import StringIO

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
IMPORT_DIR = ROOT / "logs" / "imports" / "2026-05-12"
OUT_DIR = ROOT / "analysis" / "imports" / "2026-05-12"
SIM_CSV = IMPORT_DIR / "simulation_log.csv"
AUDIT_CSV = IMPORT_DIR / "order_audit.csv"
REPORT_MD = OUT_DIR / "REPORT.md"

STRATEGIES = ["f_zone", "sf_zone", "gold_zone", "swing_38", "scalping_consensus"]


def load_sim() -> pd.DataFrame:
    df = pd.read_csv(SIM_CSV)
    df["run_at"] = pd.to_datetime(df["run_at"])
    return df


def load_audit() -> pd.DataFrame:
    df = pd.read_csv(AUDIT_CSV)
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def dedupe_sim(df: pd.DataFrame) -> pd.DataFrame:
    """동일 (symbol, strategy)가 여러 run에 반복 → 가장 최신 run만 남김.

    같은 600 캔들에 동일 백테스트가 반복돼 PnL이 (대체로) 동일하므로
    단순 합산은 4중 카운팅이 됨. last run = freshest snapshot.
    """
    return (
        df.sort_values("run_at")
        .drop_duplicates(subset=["symbol", "strategy"], keep="last")
        .reset_index(drop=True)
    )


def per_strategy_summary(sim_u: pd.DataFrame) -> pd.DataFrame:
    """전략별 효율 (unique symbol 기준)."""
    rows = []
    for strat in STRATEGIES:
        sub = sim_u[sim_u["strategy"] == strat]
        n = len(sub)
        active = (sub["trades"] > 0).sum()
        total_trades = int(sub["trades"].sum())
        total_pnl = float(sub["pnl"].sum())
        # win_rate는 trade가 있는 row만 평균
        wr_active = sub.loc[sub["trades"] > 0, "win_rate"]
        mean_wr = float(wr_active.mean()) if len(wr_active) else 0.0
        pnl_per_trade = (total_pnl / total_trades) if total_trades else 0.0
        rows.append(
            {
                "strategy": strat,
                "n_symbols": n,
                "active": int(active),
                "active%": (active / n * 100) if n else 0.0,
                "total_trades": total_trades,
                "mean_win_rate": mean_wr,
                "total_pnl": total_pnl,
                "pnl_per_trade": pnl_per_trade,
            }
        )
    return pd.DataFrame(rows)


def per_symbol_swing38(sim_u: pd.DataFrame) -> pd.DataFrame:
    return (
        sim_u[sim_u["strategy"] == "swing_38"]
        .assign(pnl_per_trade=lambda d: d["pnl"] / d["trades"].replace(0, pd.NA))
        .sort_values("pnl", ascending=False)
        .reset_index(drop=True)
        [
            [
                "symbol",
                "name",
                "candle_count",
                "trades",
                "win_rate",
                "pnl",
                "pnl_per_trade",
                "score",
                "flu_rate",
            ]
        ]
    )


def score_vs_pnl(sim_u: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """score / flu_rate 와 PnL/trades 의 상관 (swing_38 한정)."""
    s = sim_u[sim_u["strategy"] == "swing_38"].copy()
    table = s[["symbol", "name", "score", "flu_rate", "trades", "win_rate", "pnl"]].sort_values(
        "score", ascending=False
    )
    corr = {}
    if len(s) >= 2:
        corr["score_vs_pnl"] = float(s["score"].corr(s["pnl"]))
        corr["score_vs_trades"] = float(s["score"].corr(s["trades"]))
        corr["score_vs_winrate"] = float(s["score"].corr(s["win_rate"]))
        corr["flu_vs_pnl"] = float(s["flu_rate"].corr(s["pnl"]))
        corr["flu_vs_trades"] = float(s["flu_rate"].corr(s["trades"]))
    return table.reset_index(drop=True), corr


def sim_vs_real(sim_u: pd.DataFrame, audit: pd.DataFrame) -> pd.DataFrame:
    """ORDERED된 종목과 시뮬 결과 매칭 (swing_38 기준)."""
    ordered = audit[audit["action"] == "ORDERED"]
    buy_summary = (
        ordered[ordered["side"] == "buy"]
        .groupby("symbol")
        .agg(buy_orders=("order_no", "count"), buy_qty_total=("qty", "sum"))
        .reset_index()
    )
    sell_summary = (
        ordered[ordered["side"] == "sell"]
        .groupby("symbol")
        .agg(sell_orders=("order_no", "count"), sell_qty_total=("qty", "sum"))
        .reset_index()
    )
    sw = sim_u[sim_u["strategy"] == "swing_38"][
        ["symbol", "name", "trades", "win_rate", "pnl", "score"]
    ].rename(
        columns={
            "trades": "sim_trades",
            "win_rate": "sim_winrate",
            "pnl": "sim_pnl",
            "score": "sim_score",
        }
    )
    sw["symbol"] = sw["symbol"].astype(str)
    buy_summary["symbol"] = buy_summary["symbol"].astype(str)
    sell_summary["symbol"] = sell_summary["symbol"].astype(str)

    merged = sw.merge(buy_summary, on="symbol", how="outer").merge(
        sell_summary, on="symbol", how="outer"
    )
    merged["ordered"] = merged["buy_orders"].fillna(0) > 0
    merged["sim_available"] = merged["sim_trades"].notna()
    return merged.sort_values(["ordered", "sim_pnl"], ascending=[False, False]).reset_index(
        drop=True
    )


def blocked_summary(audit: pd.DataFrame) -> pd.DataFrame:
    return audit[audit["action"] == "BLOCKED"][["ts", "symbol", "qty", "reason"]].reset_index(
        drop=True
    )


def fmt_money(v: float | int) -> str:
    if pd.isna(v):
        return "—"
    return f"{int(round(v)):+,}"


def fmt_pct(v: float) -> str:
    if pd.isna(v):
        return "—"
    return f"{v * 100:.1f}%"


def render_strategy_table(df: pd.DataFrame) -> str:
    lines = [
        "| 전략 | n_symbols | active | active% | total_trades | mean_win_rate | total_pnl | pnl/trade |",
        "|------|----------:|-------:|--------:|-------------:|--------------:|----------:|----------:|",
    ]
    for _, r in df.iterrows():
        lines.append(
            f"| {r['strategy']} | {int(r['n_symbols'])} | {int(r['active'])} | "
            f"{r['active%']:.0f}% | {int(r['total_trades'])} | "
            f"{fmt_pct(r['mean_win_rate'])} | {fmt_money(r['total_pnl'])} | "
            f"{fmt_money(r['pnl_per_trade'])} |"
        )
    return "\n".join(lines)


def render_swing_table(df: pd.DataFrame) -> str:
    lines = [
        "| symbol | name | candles | trades | win_rate | pnl | pnl/trade | score | flu_rate |",
        "|--------|------|--------:|-------:|---------:|----:|----------:|------:|---------:|",
    ]
    for _, r in df.iterrows():
        lines.append(
            f"| {r['symbol']} | {r['name']} | {int(r['candle_count'])} | "
            f"{int(r['trades'])} | {fmt_pct(r['win_rate'])} | {fmt_money(r['pnl'])} | "
            f"{fmt_money(r['pnl_per_trade'])} | {r['score']:.3f} | "
            f"{r['flu_rate']:+.2f}% |"
        )
    return "\n".join(lines)


def render_score_table(df: pd.DataFrame) -> str:
    lines = [
        "| symbol | name | score | flu_rate | trades | win_rate | pnl |",
        "|--------|------|------:|---------:|-------:|---------:|----:|",
    ]
    for _, r in df.iterrows():
        lines.append(
            f"| {r['symbol']} | {r['name']} | {r['score']:.3f} | "
            f"{r['flu_rate']:+.2f}% | {int(r['trades'])} | "
            f"{fmt_pct(r['win_rate'])} | {fmt_money(r['pnl'])} |"
        )
    return "\n".join(lines)


def render_real_table(df: pd.DataFrame) -> str:
    lines = [
        "| symbol | name | ordered | sim? | sim_trades | sim_pnl | sim_winrate | buy_orders | sell_orders | note |",
        "|--------|------|:-------:|:----:|-----------:|--------:|------------:|-----------:|------------:|------|",
    ]
    for _, r in df.iterrows():
        ordered_mark = "✅" if r["ordered"] else "—"
        sim_mark = "✅" if r["sim_available"] else "❌"
        note_parts = []
        if r["ordered"] and not r["sim_available"]:
            note_parts.append("sim 없이 ORDERED")
        if r["sim_available"] and (r["sim_trades"] or 0) > 0 and not r["ordered"]:
            note_parts.append("sim only")
        note = "; ".join(note_parts) if note_parts else ""
        lines.append(
            f"| {r['symbol']} | {r['name'] if not pd.isna(r['name']) else '—'} | "
            f"{ordered_mark} | {sim_mark} | "
            f"{'' if pd.isna(r['sim_trades']) else int(r['sim_trades'])} | "
            f"{fmt_money(r['sim_pnl'])} | "
            f"{'' if pd.isna(r['sim_winrate']) else fmt_pct(r['sim_winrate'])} | "
            f"{'' if pd.isna(r['buy_orders']) else int(r['buy_orders'])} | "
            f"{'' if pd.isna(r['sell_orders']) else int(r['sell_orders'])} | {note} |"
        )
    return "\n".join(lines)


def render_blocked_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_BLOCKED 항목 없음._"
    lines = [
        "| ts | symbol | qty | reason |",
        "|----|--------|----:|--------|",
    ]
    for _, r in df.iterrows():
        reason = (r["reason"] or "")[:80].replace("\n", " ")
        lines.append(f"| {r['ts']} | {r['symbol']} | {int(r['qty'])} | {reason} |")
    return "\n".join(lines)


def build_report(
    sim_raw: pd.DataFrame,
    sim_u: pd.DataFrame,
    strat_tbl: pd.DataFrame,
    swing_tbl: pd.DataFrame,
    score_tbl: pd.DataFrame,
    corr: dict,
    real_tbl: pd.DataFrame,
    blocked_tbl: pd.DataFrame,
) -> str:
    out = StringIO()
    out.write("# 전략별 효율 분석 — 2026-05-12\n\n")
    out.write(
        f"_입력: `logs/imports/2026-05-12/simulation_log.csv` ({len(sim_raw)} rows, "
        f"{sim_raw['run_at'].nunique()} runs) + `order_audit.csv`._  \n"
        f"_unique `(symbol, strategy)` pairs (last run 기준): {len(sim_u)}_\n\n"
    )

    out.write("## A. 전략별 효율\n\n")
    out.write(render_strategy_table(strat_tbl))
    out.write("\n\n")
    active_strats = strat_tbl[strat_tbl["active"] > 0]["strategy"].tolist()
    inactive_strats = strat_tbl[strat_tbl["active"] == 0]["strategy"].tolist()
    out.write(
        f"- **활성 전략**: {', '.join(active_strats) if active_strats else '없음'}\n"
        f"- **0건 전략**: {', '.join(inactive_strats) if inactive_strats else '없음'} — 단일 거래일 표본이므로 "
        f"전략 실패로 단정할 수 없음. 다일 누적 후 재평가 필요.\n\n"
    )

    out.write("## B. swing_38 종목별 효율\n\n")
    out.write(render_swing_table(swing_tbl))
    out.write("\n\n")
    if not swing_tbl.empty:
        best = swing_tbl.iloc[0]
        worst = swing_tbl.iloc[-1]
        out.write(
            f"- 최고 PnL: **{best['symbol']} {best['name']}** ({fmt_money(best['pnl'])}, "
            f"trades={int(best['trades'])}, win_rate={fmt_pct(best['win_rate'])})\n"
            f"- 최저 PnL: **{worst['symbol']} {worst['name']}** ({fmt_money(worst['pnl'])}, "
            f"trades={int(worst['trades'])}, win_rate={fmt_pct(worst['win_rate'])})\n"
            f"- trade 빈도와 PnL이 비례하지 않음 → trade/거래당 수익(pnl/trade)이 종목 선별 핵심 지표\n\n"
        )

    out.write("## C. 종목 선정 점수 vs 결과 (swing_38)\n\n")
    out.write(render_score_table(score_tbl))
    out.write("\n\n")
    if corr:
        out.write("**상관계수 (Pearson, N={}):**\n\n".format(len(score_tbl)))
        for k, v in corr.items():
            out.write(f"- `{k}` = {v:+.3f}\n")
        out.write("\n")
        sp = corr.get("score_vs_pnl", 0)
        fp = corr.get("flu_vs_pnl", 0)
        out.write(
            f"- score(3-factor) ↔ PnL: {sp:+.2f} → "
            f"{'양의 신호' if sp > 0.3 else '약/무관' if abs(sp) <= 0.3 else '음의 신호'} "
            f"(N=5 표본은 유의성 약함)\n"
            f"- flu_rate(등락률) ↔ PnL: {fp:+.2f} → "
            f"{'고등락 종목이 PnL 큰 경향' if fp > 0.3 else '약/무관' if abs(fp) <= 0.3 else '역상관'}\n\n"
        )

    out.write("## D. 시뮬 vs 실거래 매칭\n\n")
    out.write(render_real_table(real_tbl))
    out.write("\n\n")
    only_real = real_tbl[real_tbl["ordered"] & ~real_tbl["sim_available"]]
    if not only_real.empty:
        out.write("**⚠️ sim 없이 ORDERED된 종목:**\n\n")
        for _, r in only_real.iterrows():
            out.write(
                f"- `{r['symbol']}` — buy_orders={int(r['buy_orders'] or 0)} — "
                f"시뮬 미실행 상태에서 매수됨 (캔들 부족 등). 진입 정책 점검 필요.\n"
            )
        out.write("\n")
    weak_sim_ordered = real_tbl[
        real_tbl["ordered"] & real_tbl["sim_available"] & (real_tbl["sim_pnl"] < 100_000)
    ]
    if not weak_sim_ordered.empty:
        out.write("**⚠️ 시뮬 PnL 약한데도 ORDERED:**\n\n")
        for _, r in weak_sim_ordered.iterrows():
            out.write(
                f"- `{r['symbol']} {r['name']}` — sim_pnl={fmt_money(r['sim_pnl'])}, "
                f"sim_trades={int(r['sim_trades'])} → 진입 임계값 검토\n"
            )
        out.write("\n")

    out.write("## E. BLOCKED 주문 (참고)\n\n")
    out.write(render_blocked_table(blocked_tbl))
    out.write("\n\n")

    out.write("## Caveats\n\n")
    out.write(
        "- 단일 거래일(2026-05-12) 표본 — 통계적 유의성 약함\n"
        "- 동일 (symbol, strategy)가 4 runs 반복 → 가장 최신 run으로 dedup (단순 합산은 4중 카운팅)\n"
        "- swing_38 외 4개 전략 0 trade — '전략 실패'인지 '그날 시장 미스매치'인지 미확정\n"
        "- mockapi 키움 429 rate limit으로 066570 일부 fetch 실패 이력\n"
        "- 캔들 부족(439960 코스모로보틱스: 2<31) 종목이 실거래 진입됨 → 사전 필터 강화 검토\n"
        "- 실거래 PnL은 본 분석 미포함 (체결가 데이터 없음, 시뮬 PnL만 비교)\n"
    )
    return out.getvalue()


def main() -> int:
    if not SIM_CSV.exists():
        print(f"[ERR] 입력 없음: {SIM_CSV}", file=sys.stderr)
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    sim_raw = load_sim()
    audit = load_audit()
    sim_u = dedupe_sim(sim_raw)

    strat_tbl = per_strategy_summary(sim_u)
    swing_tbl = per_symbol_swing38(sim_u)
    score_tbl, corr = score_vs_pnl(sim_u)
    real_tbl = sim_vs_real(sim_u, audit)
    blocked_tbl = blocked_summary(audit)

    print("=" * 60)
    print(f"입력: {len(sim_raw)} rows / {sim_raw['run_at'].nunique()} runs")
    print(f"dedup 후 unique (symbol, strategy) pairs: {len(sim_u)}")
    print("=" * 60)
    print("\n[A] 전략별 효율")
    print(strat_tbl.to_string(index=False))
    print("\n[B] swing_38 종목별")
    print(swing_tbl.to_string(index=False))
    print("\n[C] score vs pnl 상관계수")
    for k, v in corr.items():
        print(f"  {k:24s} = {v:+.3f}")
    print("\n[D] 시뮬 vs 실거래")
    print(real_tbl.to_string(index=False))
    print("\n[E] BLOCKED")
    print(blocked_tbl.to_string(index=False) if not blocked_tbl.empty else "  (없음)")

    report = build_report(
        sim_raw, sim_u, strat_tbl, swing_tbl, score_tbl, corr, real_tbl, blocked_tbl
    )
    REPORT_MD.write_text(report, encoding="utf-8")
    print("\n" + "=" * 60)
    print(f"리포트 저장: {REPORT_MD.relative_to(ROOT)}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
