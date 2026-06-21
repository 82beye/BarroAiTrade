"""이격도 노란불 게이트(D-R43) robustness 검증 — seed·기간·임계.

이격도 게이트는 일봉(5MA) 기반이라 5분봉 캐시(~45일) 제약이 불필요 → **전체 일봉
250봉 윈도우**로 baseline 종베 신호를 수집하고, 이격도로 분할해 견고성을 4축 검증한다.

신호당 레코드(pnl, disparity, sym, date)를 1회 수집 후 in-memory 재집계:
  A. 전체(장기간)            : baseline vs gate(@0.1425)
  B. 임계 sweep              : 0.10~0.18 → 절벽형 최적 거부(±스텝 부호 유지)
  C. 다중 seed 서브샘플      : 5개 랜덤 유니버스 → gate>baseline 부호 안정
  D. 기간 early/late 분할    : 시간 OOS(두 구간 모두 gate>baseline)

baseline 신호 = ClosingBetParams(require_new_high, min_atr_pct=0.035), 일봉 전용.
비용 net@0.90 = 평균 gross - 0.90.

사용: python scripts/backtest_closing_bet_disparity_robustness.py [POOL] [MAX_DAILY]
"""
from __future__ import annotations

import glob
import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.strategy.closing_bet import ClosingBetParams, ClosingBetStrategy  # noqa: E402
from backend.core.strategy.closing_bet_filters import disparity_5ma  # noqa: E402
from backend.models.market import MarketType  # noqa: E402
from backend.models.strategy import AnalysisContext  # noqa: E402
from scripts.backtest_closing_bet_intraday import (  # noqa: E402
    COST_REAL, DAILY, _load_daily, _simulate_exit,
)

THR = 0.1425


def _agg(records: list[tuple]) -> dict:
    """records: [(pnl, disparity, sym, date)]. → trips/win/gross/net@0.90."""
    if not records:
        return {"trips": 0}
    arr = [r[0] for r in records]
    g = statistics.mean(arr)
    wr = sum(1 for x in arr if x > 0) / len(arr) * 100
    return {"trips": len(arr), "win": round(wr, 1),
            "gross": round(g, 3), "net90": round(g - COST_REAL, 3)}


def _gate(records: list[tuple], thr: float) -> list[tuple]:
    return [r for r in records if r[1] is not None and r[1] >= thr]


def run(pool_n: int, max_daily: int) -> None:
    # 유니버스 풀: 거래대금 상위(5분봉 불필요 — 일봉 전용)
    scored = []
    for f in glob.glob(DAILY + "/*.json"):
        try:
            d = json.load(open(f))["data"]
        except Exception:
            continue
        if len(d) < 80:
            continue
        atv = sum(float(x["close"]) * float(x["volume"]) for x in d[-120:]) / min(120, len(d))
        scored.append((atv, f, Path(f).stem))
    scored.sort(reverse=True)
    pool = scored[:pool_n]
    print(f"[이격도 게이트 robustness] 일봉 전용 거래대금상위 {len(pool)}종목, 윈도우 {max_daily}봉")

    baseline = ClosingBetStrategy(ClosingBetParams(require_eod_window=False,
                                                   require_new_high=True, min_atr_pct=0.035))
    # 신호 1회 수집
    records: list[tuple] = []  # (pnl, disparity, sym, date)
    n_days = 0
    for _, f, sym in pool:
        daily = _load_daily(f, max_daily)
        if len(daily) < 70:
            continue
        for i in range(60, len(daily)):
            n_days += 1
            ctx = AnalysisContext(symbol=sym, name=sym, candles=daily[:i + 1],
                                  market_type=MarketType.STOCK)
            if baseline._analyze_v2(ctx) is None:
                continue
            pnl = _simulate_exit(daily, i)
            disp = disparity_5ma(daily[:i + 1])
            records.append((pnl, disp, sym, daily[i].timestamp.date()))

    print(f"  평가 stock-day {n_days}, baseline 신호 {len(records)}건\n")
    out: dict = {"eval_stock_days": n_days, "baseline_signals": len(records)}

    # A. 전체
    base_all = _agg(records)
    gate_all = _agg(_gate(records, THR))
    print("[A] 전체(장기간)")
    print(f"  baseline       {base_all}")
    print(f"  +gate@{THR}   {gate_all}")
    out["A_full"] = {"baseline": base_all, "gate": gate_all}

    # B. 임계 sweep
    print("\n[B] 임계 sweep (net@0.90)")
    sweep = {}
    for thr in (0.10, 0.1225, 0.1425, 0.1625, 0.18):
        a = _agg(_gate(records, thr))
        sweep[str(thr)] = a
        print(f"  thr={thr:<6} trips={a.get('trips',0):>4} win={a.get('win','-')!s:>5} net90={a.get('net90','-')}")
    out["B_threshold_sweep"] = sweep

    # C. 다중 seed 서브샘플 (유니버스 50%)
    print("\n[C] 다중 seed 서브샘플 (종목 50%)")
    syms = sorted({r[2] for r in records})
    seed_rows = {}
    for seed in (1, 2, 3, 4, 5):
        rng = random.Random(seed)
        keep = set(rng.sample(syms, max(1, len(syms) // 2)))
        sub = [r for r in records if r[2] in keep]
        b, gt = _agg(sub), _agg(_gate(sub, THR))
        delta = (gt.get("net90", 0) - b.get("net90", 0)) if gt.get("trips") else None
        seed_rows[str(seed)] = {"baseline": b, "gate": gt, "delta_net90": delta}
        print(f"  seed={seed} base net90={b.get('net90','-')!s:>7} gate net90={gt.get('net90','-')!s:>7} Δ={delta:+.3f}" if delta is not None else f"  seed={seed} (표본부족)")
    out["C_seeds"] = seed_rows

    # D. 기간 early/late 분할 (전체 신호 date 중앙값 기준)
    print("\n[D] 기간 early/late 분할 (시간 OOS)")
    dates = sorted(r[3] for r in records)
    split = dates[len(dates) // 2] if dates else None
    early = [r for r in records if r[3] < split]
    late = [r for r in records if r[3] >= split]
    per = {}
    for tag, rs in (("early", early), ("late", late)):
        b, gt = _agg(rs), _agg(_gate(rs, THR))
        per[tag] = {"baseline": b, "gate": gt,
                    "delta_net90": (gt.get("net90", 0) - b.get("net90", 0)) if gt.get("trips") else None}
        print(f"  {tag:5} (~{split}) base net90={b.get('net90','-')!s:>7} gate net90={gt.get('net90','-')!s:>7}")
    out["D_period"] = {"split_date": str(split), **per}

    # 판정
    seed_ok = all(v["delta_net90"] is not None and v["delta_net90"] > 0 for v in seed_rows.values())
    period_ok = all(per[t]["delta_net90"] is not None and per[t]["delta_net90"] > 0 for t in ("early", "late"))
    sweep_ok = all(v.get("net90", -9) > 0 for k, v in sweep.items() if v.get("trips", 0) >= 30)
    verdict = (f"seeds {'OK' if seed_ok else 'X'} · period {'OK' if period_ok else 'X'} · "
               f"sweep(net>0) {'OK' if sweep_ok else 'X'} → "
               + ("ROBUST" if (seed_ok and period_ok and sweep_ok) else "주의(일부 미충족)"))
    print(f"\n[판정] {verdict}")
    out["verdict"] = verdict

    p = Path(__file__).resolve().parents[1] / \
        "docs/04-report/features/2026-06-21-disparity-gate-robustness.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"결과 저장: {p}")


if __name__ == "__main__":
    pn = int(sys.argv[1]) if len(sys.argv) > 1 else 800
    md = int(sys.argv[2]) if len(sys.argv) > 2 else 250
    run(pn, md)
