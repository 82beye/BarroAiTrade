#!/usr/bin/env python3
"""swing_38 OOS 검증 — 기존 _oos_validation 기계 재사용(진실원천, 재구현 0).

swing_38 은 현재 활성 중인 실전 다일 스윙 전략(임펄스→Fib0.382→반등, max_hold 20).
본 스크립트는 _oos_validation.py 의 로더·시뮬레이터·게이트를 그대로 import 하고
STRATEGIES 만 ["swing_38"] 로 오버라이드해, 실제 Swing38Strategy(IntradaySimulator
._build_strategies 가 require_daily_candles=True·max_hold_days=20 으로 인스턴스화) +
브로커 실측 비용 + 동일 OOS 게이트(active≥15·trades≥30·avg_ret>0·drop1 부호안정·
holdout avg>0)로 멀티 seed 검증한다.

사용: python scripts/oos_validation_swing38.py [--n 120] [--seeds 42,7,123,2024,99]
"""
from __future__ import annotations

import argparse
import importlib
import os
import statistics
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "scripts"))
oos = importlib.import_module("_oos_validation")

# worktree 의 data/ 는 비어 있음(gitignore) → 메인레포 일봉 캐시로 오버라이드.
from pathlib import Path
_MAIN_CACHE = Path("/Users/beye/workspace/BarroAiTrade/data/ohlcv_cache")
if _MAIN_CACHE.exists():
    oos.DAILY_CACHE = _MAIN_CACHE

SID = "swing_38"


def run_seed(n: int, seed: int):
    oos.STRATEGIES = [SID]  # 기계가 참조하는 전략 목록 오버라이드
    uni = oos.select_random_universe(n, seed)
    full, hold = oos.backtest_universe(uni)
    s = oos.summarize(full[SID])
    h = oos.summarize(hold[SID])
    d1 = oos.drop1_sign_stable(full[SID])
    v, fails = oos.verdict(s["active"], s["trades"], s["avg_ret"], d1, h["avg_ret"])
    return {"seed": seed, "universe": len(uni), **s, "holdout_avg": h["avg_ret"],
            "holdout_trades": h["trades"], "drop1": d1, "verdict": v, "fails": fails}


def main():
    ap = argparse.ArgumentParser(description="swing_38 OOS 검증 (멀티 seed)")
    ap.add_argument("--n", type=int, default=120, help="seed별 랜덤 유니버스 종목 수")
    ap.add_argument("--seeds", default="42,7,123,2024,99")
    args = ap.parse_args()
    seeds = [int(x) for x in args.seeds.split(",")]

    print(f"swing_38 OOS — n={args.n}/seed, seeds={seeds}")
    print(f"게이트: active≥{oos.MIN_ACTIVE_SYMBOLS} & trades≥{oos.MIN_TRADES} & avg_ret>0 & drop1 안정 & holdout>0")
    print(f"{'seed':>6}{'uni':>5}{'active':>7}{'trades':>7}{'win%':>7}{'avg_ret%':>9}{'holdout%':>9}{'drop1':>6}  판정/사유")
    rows = []
    for sd in seeds:
        r = run_seed(args.n, sd)
        rows.append(r)
        print(f"{r['seed']:>6}{r['universe']:>5}{r['active']:>7}{r['trades']:>7}"
              f"{r['win_rate']:>7.1f}{r['avg_ret']:>9.3f}{r['holdout_avg']:>9.3f}"
              f"{str(r['drop1']):>6}  {r['verdict']}"
              + (f" ({'; '.join(r['fails'])})" if r['fails'] else ""))

    passes = sum(1 for r in rows if r["verdict"] == "PASS")
    _av = [r["avg_ret"] for r in rows if r["trades"] > 0]
    _ho = [r["holdout_avg"] for r in rows if r["holdout_trades"] > 0]
    avg_all = statistics.fmean(_av) if _av else 0.0
    ho_all = statistics.fmean(_ho) if _ho else 0.0
    print(f"\n종합: {passes}/{len(rows)} seed PASS | 전체 avg_ret 평균 {avg_all:+.3f}% | holdout 평균 {ho_all:+.3f}%")
    print("판정: 다수 seed PASS + avg/holdout 양수 → 견고. 일부 PASS/혼조 → 조건부. ⚠ 비용=브로커 실측(보수).")


if __name__ == "__main__":
    main()
