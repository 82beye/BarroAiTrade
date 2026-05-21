"""Phase 1 — 누적 전략별 성과 추적.

strategy_ledger.csv (daily pipeline 산출) → 일자·전략별 net·승률·평균pnl 집계 후
strategy_perf.csv 재작성 + 콘솔 누적 표.

사용:
    python scripts/_strategy_perf_track.py
    python scripts/_strategy_perf_track.py --graph
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

LEDGER_PATH = _REPO_ROOT / "analysis" / "strategy_ledger.csv"
PERF_PATH = _REPO_ROOT / "analysis" / "strategy_perf.csv"
GRAPH_PATH = _REPO_ROOT / "analysis" / "strategy_perf.png"
PERF_HEADER = ["date", "strategy", "net", "win_rate", "avg_pnl", "n_symbols"]
_CLOSED = ("익절", "손실")


# ─── 순수 로직 (테스트 대상) ──────────────────────────────────────────────


def _to_int(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _fmt_signed(v) -> str:
    return f"{int(v):+,}"


def load_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def aggregate(rows: list[dict]) -> list[dict]:
    """ledger 행 → (date, strategy) 그룹별 net·승률·평균pnl·종목수."""
    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r.get("date", ""), r.get("strategy", ""))
        g = groups.setdefault(key, {"nets": [], "results": []})
        g["nets"].append(_to_int(r.get("net")))
        g["results"].append(r.get("result", ""))

    out: list[dict] = []
    for (date, strat), g in sorted(groups.items()):
        nets = g["nets"]
        closed = [n for n, res in zip(nets, g["results"]) if res in _CLOSED]
        wins = sum(1 for n in closed if n > 0)
        out.append({
            "date": date,
            "strategy": strat,
            "net": sum(nets),
            "win_rate": round(wins / len(closed), 4) if closed else 0.0,
            "avg_pnl": round(sum(nets) / len(nets)) if nets else 0,
            "n_symbols": len(nets),
        })
    return out


def write_perf(path: Path, agg_rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PERF_HEADER)
        w.writeheader()
        for r in agg_rows:
            w.writerow({k: r[k] for k in PERF_HEADER})


def render_cumulative(rows: list[dict]) -> str:
    """ledger 전체 → 전략별 누적 net·승률·종목수 표."""
    by: dict[str, dict] = {}
    for r in rows:
        s = by.setdefault(r.get("strategy", "unknown"),
                          {"net": 0, "n": 0, "wins": 0, "closed": 0})
        s["net"] += _to_int(r.get("net"))
        s["n"] += 1
        if r.get("result") in _CLOSED:
            s["closed"] += 1
            if r.get("result") == "익절":
                s["wins"] += 1

    lines = [f"{'전략':<18} {'누적net':>14} {'승률':>8} {'종목수':>7}", "─" * 50]
    for sid, s in sorted(by.items(), key=lambda kv: kv[1]["net"]):
        wr = s["wins"] / s["closed"] if s["closed"] else 0.0
        lines.append(
            f"{sid:<18} {_fmt_signed(s['net']):>14} {wr:>7.1%} {s['n']:>7}"
        )
    return "\n".join(lines)


# ─── 그래프 (옵션) ────────────────────────────────────────────────────────


def plot_cumulative(agg_rows: list[dict], png_path: Path) -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib 미설치 — 그래프 생략 (pip install matplotlib)")
        return False

    dates = sorted({a["date"] for a in agg_rows})
    strategies = sorted({a["strategy"] for a in agg_rows})
    fig, ax = plt.subplots(figsize=(10, 6))
    for sid in strategies:
        cum, running = [], 0
        for d in dates:
            running += sum(a["net"] for a in agg_rows
                           if a["date"] == d and a["strategy"] == sid)
            cum.append(running)
        ax.plot(dates, cum, marker="o", label=sid)
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.set_title("전략별 누적 net")
    ax.set_xlabel("date")
    ax.set_ylabel("cumulative net (KRW)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(png_path)
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="누적 전략별 성과 추적 (Phase 1)")
    ap.add_argument("--ledger", default=str(LEDGER_PATH), help="입력 ledger CSV")
    ap.add_argument("--out", default=str(PERF_PATH), help="출력 perf CSV")
    ap.add_argument("--graph", action="store_true", help="누적 net 그래프 저장")
    args = ap.parse_args()

    rows = load_ledger(Path(args.ledger))
    if not rows:
        print(f"ledger 비어있음: {args.ledger}")
        return

    agg_rows = aggregate(rows)
    write_perf(Path(args.out), agg_rows)
    print(f"perf 갱신: {args.out} ({len(agg_rows)} 행)\n")
    print(render_cumulative(rows))

    if args.graph and plot_cumulative(agg_rows, GRAPH_PATH):
        print(f"\n그래프 저장: {GRAPH_PATH}")


if __name__ == "__main__":
    main()
