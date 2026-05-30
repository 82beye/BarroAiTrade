"""OOS(out-of-sample) 검증 관문 — 고도화 Phase 3 #10.

배경(docs/02-design/features/2026-05-30-strategy-uplift.design.md §P7-1,
docs/04-report/features/2026-05-29-grid-backtest.md §7-3): 일봉 흑자 결론
(gold +1.76/f +3.42/sf +3.76%)은 '변동성 상위' 유니버스 선택편향·in-sample 의존.
본 관문은 **랜덤(비변동성 선택) 유니버스 + 3분할 청산(IntradaySimulator) + 실비용**으로
전략 엣지가 선택편향 없이 유지되는지 판정한다. 추가로 train/holdout 분할로 시간 OOS 확인.

판정(전략별): active종목≥15 & trades≥30 & avg_ret>0 & drop1 부호안정 & holdout avg_ret>0 → PASS.
★PASS 전 실거래 자본증액 금지.

사용:
  venv/bin/python scripts/_oos_validation.py --n 60 --seed 42 [--save]
"""
from __future__ import annotations

import argparse
import glob
import json
import random
import statistics
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

DAILY_CACHE = _REPO / "data" / "ohlcv_cache"
STRATEGIES = ["f_zone", "sf_zone", "gold_zone"]
COMMISSION_PCT = 0.015
TAX_PCT = 0.18

# 판정 기준 (grid-backtest §4-6)
MIN_ACTIVE_SYMBOLS = 15
MIN_TRADES = 30


# ─── 순수 로직 (테스트 대상) ─────────────────────────────────────────────

def roundtrip_returns(trades, strategy_id: str) -> list[float]:
    """sim.trades → 전략별 라운드트립 수익률%(비용 반영 net/진입notional)."""
    out, cur = [], None
    for t in trades:
        if t.strategy_id != strategy_id:
            continue
        if t.side == "buy":
            if cur and cur["notional"] > 0:
                out.append(cur["pnl"] / cur["notional"] * 100)
            cur = {"notional": float(t.price) * float(t.qty), "pnl": 0.0}
        elif t.side == "sell" and cur:
            cur["pnl"] += float(t.pnl)
    if cur and cur["notional"] > 0:
        out.append(cur["pnl"] / cur["notional"] * 100)
    return out


def drop1_sign_stable(per_symbol_rets: dict[str, list[float]]) -> bool:
    """최대 기여 종목 1개 제거 후에도 평균수익률 부호가 유지되면 True.

    per_symbol_rets: {symbol: [라운드트립 수익률%, ...]}.
    """
    all_rets = [r for rs in per_symbol_rets.values() for r in rs]
    if not all_rets:
        return False
    base_avg = statistics.fmean(all_rets)
    # 종목별 합 기여 — base_avg 부호 방향으로 가장 크게 기여하는 종목 제거
    contrib = {s: sum(rs) for s, rs in per_symbol_rets.items()}
    drop_sym = max(contrib, key=contrib.get) if base_avg >= 0 else min(contrib, key=contrib.get)
    remaining = [r for s, rs in per_symbol_rets.items() if s != drop_sym for r in rs]
    if not remaining:
        return False
    new_avg = statistics.fmean(remaining)
    return (base_avg >= 0) == (new_avg >= 0)


def verdict(active: int, trades: int, avg_ret: float, drop1_ok: bool,
            holdout_avg: float | None) -> tuple[str, list[str]]:
    """전략별 PASS/FAIL 판정 + 사유."""
    fails = []
    if active < MIN_ACTIVE_SYMBOLS:
        fails.append(f"active {active}<{MIN_ACTIVE_SYMBOLS}")
    if trades < MIN_TRADES:
        fails.append(f"trades {trades}<{MIN_TRADES}")
    if avg_ret <= 0:
        fails.append(f"avg_ret {avg_ret:+.3f}%≤0")
    if not drop1_ok:
        fails.append("drop1 부호반전(outlier 의존)")
    if holdout_avg is not None and holdout_avg <= 0:
        fails.append(f"holdout avg {holdout_avg:+.3f}%≤0")
    return ("PASS" if not fails else "FAIL"), fails


# ─── I/O (일봉 캐시 로딩 / 백테스트) ─────────────────────────────────────

def load_daily(symbol: str, cap: int = 600):
    from backend.models.market import OHLCV, MarketType
    try:
        d = json.load(open(DAILY_CACHE / f"{symbol}.json"))
        rows = d["data"] if isinstance(d, dict) else d
    except Exception:
        return []
    out = []
    for r in rows:
        ds = str(r.get("date") or "")
        try:
            ts = datetime.strptime(ds[:8], "%Y%m%d")
        except ValueError:
            continue
        out.append(OHLCV(symbol=symbol, timestamp=ts, open=float(r["open"]), high=float(r["high"]),
                         low=float(r["low"]), close=float(r["close"]), volume=float(r.get("volume") or 0),
                         market_type=MarketType.STOCK))
    out.sort(key=lambda c: c.timestamp)
    return out[-cap:]


def select_random_universe(n: int, seed: int, min_med_vol: float = 100_000) -> list[str]:
    """ohlcv_cache 에서 랜덤(비변동성) 유니버스 — 유동성 하한만 적용."""
    files = [Path(f).stem for f in glob.glob(str(DAILY_CACHE / "*.json"))]
    cands = [s for s in files if len(s) == 6 and s.isdigit()]
    random.Random(seed).shuffle(cands)
    picked = []
    for s in cands:
        c = load_daily(s, cap=200)
        if len(c) < 120:
            continue
        med_vol = statistics.median(x.volume for x in c[-80:])
        if med_vol < min_med_vol:
            continue
        picked.append(s)
        if len(picked) >= n:
            break
    return picked


def backtest_universe(symbols: list[str], holdout_frac: float = 0.4):
    from backend.core.backtester.intraday_simulator import IntradaySimulator
    sim = IntradaySimulator(position_value=Decimal("3000000"),
                            commission_pct=COMMISSION_PCT, tax_pct_on_sell=TAX_PCT)
    full = {s: {} for s in STRATEGIES}   # strategy → {symbol: [rets]}
    hold = {s: {} for s in STRATEGIES}
    for sym in symbols:
        cs = load_daily(sym)
        if len(cs) < 80:
            continue
        try:
            res = sim.run(cs, sym, strategies=STRATEGIES)
        except Exception:
            continue
        for sid in STRATEGIES:
            rr = roundtrip_returns(res.trades, sid)
            if rr:
                full[sid][sym] = rr
        # holdout: 최근 holdout_frac 구간만
        split = int(len(cs) * (1 - holdout_frac))
        ho = cs[split:]
        if len(ho) >= 80:
            try:
                resh = sim.run(ho, sym, strategies=STRATEGIES)
                for sid in STRATEGIES:
                    rr = roundtrip_returns(resh.trades, sid)
                    if rr:
                        hold[sid][sym] = rr
            except Exception:
                pass
    return full, hold


def summarize(per_symbol_rets: dict) -> dict:
    rets = [r for rs in per_symbol_rets.values() for r in rs]
    n = len(rets)
    return {
        "active": len(per_symbol_rets), "trades": n,
        "win_rate": (sum(1 for r in rets if r > 0) / n * 100) if n else 0.0,
        "avg_ret": (statistics.fmean(rets)) if n else 0.0,
        "sum_ret": sum(rets),
    }


def main():
    ap = argparse.ArgumentParser(description="OOS 검증 관문 (고도화 Phase 3 #10)")
    ap.add_argument("--n", type=int, default=60, help="랜덤 유니버스 종목 수")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    print(f"=== OOS 검증 관문 — 랜덤 유니버스 n={args.n} seed={args.seed} (일봉·3분할·실비용) ===")
    uni = select_random_universe(args.n, args.seed)
    print(f"선정 종목수: {len(uni)} (비변동성 랜덤, 유동성 하한)")
    full, hold = backtest_universe(uni)

    print(f"\n{'전략':10} {'active':>6} {'trades':>7} {'win%':>6} {'avg_ret%':>9} {'holdout%':>9} {'drop1':>6} {'판정'}")
    results = {}
    for sid in STRATEGIES:
        s = summarize(full[sid])
        h = summarize(hold[sid])
        d1 = drop1_sign_stable(full[sid])
        ho_avg = h["avg_ret"] if h["trades"] >= 10 else None
        v, fails = verdict(s["active"], s["trades"], s["avg_ret"], d1, ho_avg)
        results[sid] = {**s, "holdout_avg": h["avg_ret"], "drop1_stable": d1, "verdict": v, "fails": fails}
        ho_str = f"{h['avg_ret']:+.3f}" if ho_avg is not None else "n/a"
        print(f"{sid:10} {s['active']:>6} {s['trades']:>7} {s['win_rate']:>5.0f}% {s['avg_ret']:>+8.3f} {ho_str:>9} {'OK' if d1 else '반전':>6} {v}")
        if fails:
            print(f"           └ FAIL: {', '.join(fails)}")

    n_pass = sum(1 for r in results.values() if r["verdict"] == "PASS")
    print(f"\n관문 결과: {n_pass}/{len(STRATEGIES)} PASS")
    print("→ " + ("일부 전략 OOS 통과 — 단 단일 seed/단일 데이터소스 한계, 자본증액은 추가 seed·기간 확인 후."
                  if n_pass else "전 전략 OOS 미통과 — 일봉 흑자는 변동성 선택편향 산물일 가능성. 실거래 자본증액 금지."))

    if args.save:
        p = _REPO / "reports" / f"oos_validation_seed{args.seed}.json"
        p.parent.mkdir(exist_ok=True)
        json.dump({"n": args.n, "seed": args.seed, "universe": uni, "results": results}, open(p, "w"), ensure_ascii=False, indent=2)
        print(f"저장: {p}")


if __name__ == "__main__":
    main()
