"""승률 최적화 — 고도화: 기대값 양수 + 최소거래 유지하며 달성 가능한 최대 승률 탐색.

전문 트레이더 원칙: 승률 단독 최적화는 위험(좁은TP/넓은SL=80%승·기대값음수=파산).
본 도구는 (진입 선별 min_score) × (청산 설정: 3분할 TP/SL/breakeven/trailing) 격자를
랜덤 OOS 유니버스(일봉, 실비용)에 적용해 win%·expectancy·trades 를 산출하고,
'기대값>0 & trades>=30' 제약 하 최대 승률 셀을 찾는다.

효율: 진입신호(analyze)는 (종목,전략)당 1회 수집 후 캐시 → score·exit 격자는 사후 스윕.

사용: venv/bin/python scripts/_winrate_optimizer.py --n 50 --seed 42 [--strategy gold_zone]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from scripts._oos_validation import select_random_universe, load_daily

COMMISSION = 0.00015
TAX = 0.0018
STRATEGIES = ["f_zone", "sf_zone", "gold_zone"]
MIN_TRADES = 30

# ── 청산 프리셋 (이름: dict(tp=[%], q=[비중], sl=%, be=bool(TP1 후 본전), trail=(start%,off%)|None, max_hold)) ──
EXIT_PRESETS = {
    "wide_3tier":  dict(tp=[3.0, 5.0, 7.0], q=[0.33, 0.33, 0.34], sl=-1.5, be=False, trail=(3.0, 1.5), max_hold=5),
    "early_lock":  dict(tp=[1.5, 3.0, 5.0], q=[0.30, 0.35, 0.35], sl=-1.5, be=True,  trail=(3.0, 1.5), max_hold=5),
    "tight_be":    dict(tp=[1.0, 2.0, 3.0], q=[0.40, 0.30, 0.30], sl=-2.0, be=True,  trail=None,        max_hold=5),
    "scalp_be":    dict(tp=[0.7, 1.5, 2.5], q=[0.50, 0.25, 0.25], sl=-2.0, be=True,  trail=None,        max_hold=3),
    "wide_sl_trap":dict(tp=[1.0, 2.0, 3.0], q=[0.40, 0.30, 0.30], sl=-5.0, be=True,  trail=None,        max_hold=8),
    # round2 — 승률 천장 탐색 (조기 익절 + 본전 + 타이트 SL 변형)
    "micro_tight": dict(tp=[0.5, 1.0, 2.0], q=[0.60, 0.20, 0.20], sl=-1.0, be=True,  trail=None,        max_hold=3),
    "micro_mid":   dict(tp=[0.5, 1.5, 3.0], q=[0.50, 0.25, 0.25], sl=-1.5, be=True,  trail=None,        max_hold=4),
    "lock_trail":  dict(tp=[1.0, 2.5, 5.0], q=[0.40, 0.30, 0.30], sl=-1.5, be=True,  trail=(1.5, 1.0),  max_hold=6),
    "be_fast":     dict(tp=[1.0, 2.0, 4.0], q=[0.50, 0.25, 0.25], sl=-1.5, be=True,  trail=(2.0, 1.0),  max_hold=5),
    "tp1_heavy_be":dict(tp=[1.5, 3.0, 5.0], q=[0.60, 0.20, 0.20], sl=-1.5, be=True,  trail=(2.0, 1.0),  max_hold=6),
}
MIN_SCORES = [0.0, 4.0, 5.0, 6.0, 7.0, 8.0]


def _strategy(strategy_id, min_atr=0.035):
    from backend.core.strategy.f_zone import FZoneStrategy, FZoneParams
    from backend.core.strategy.sf_zone import SFZoneStrategy
    from backend.core.strategy.gold_zone import GoldZoneStrategy, GoldZoneParams
    if strategy_id == "f_zone":
        return FZoneStrategy(FZoneParams(min_atr_pct=min_atr))
    if strategy_id == "sf_zone":
        return SFZoneStrategy(FZoneParams(min_atr_pct=min_atr))
    return GoldZoneStrategy(GoldZoneParams(min_atr_pct=min_atr))


def collect_entries(symbol, strategy_id, candles, warmup=60):
    """롤링 analyze → [(idx, score)] (1회 수집). idx=신호 캔들."""
    from backend.models.strategy import AnalysisContext
    from backend.models.market import MarketType
    strat = _strategy(strategy_id)
    params = getattr(strat, "params", None) or getattr(getattr(strat, "_inner", None), "params", None)
    mc = max(warmup, getattr(params, "min_candles", 60) if params else 60)
    out = []
    for i in range(mc, len(candles)):
        w = candles[max(0, i + 1 - 200): i + 1]
        ctx = AnalysisContext(symbol=symbol, name=symbol, candles=w, market_type=MarketType.STOCK)
        try:
            sig = strat.analyze(ctx)
        except Exception:
            sig = None
        if sig is not None:
            out.append((i, float(sig.score)))
    return out


def simulate_exit(candles, entry_idx, cfg):
    """파라미터화 3분할 청산 → round-trip net 수익률%. 1포지션."""
    ei = entry_idx + 1
    if ei >= len(candles):
        return None
    entry = candles[ei].open
    if entry <= 0:
        return None
    tps = [entry * (1 + t / 100) for t in cfg["tp"]]
    qs = list(cfg["q"])
    sl_price = entry * (1 + cfg["sl"] / 100)
    be = cfg["be"]
    trail = cfg["trail"]
    filled = [False] * len(tps)
    remaining = 1.0
    net = 0.0
    peak = entry
    be_active = False

    def _sell(frac, price):
        nonlocal net
        r = price / entry
        gross = (r - 1) * frac
        cost = (COMMISSION * (1 + r) + TAX * r) * frac
        net += gross - cost

    for j in range(ei + 1, min(len(candles), ei + 1 + cfg["max_hold"])):
        c = candles[j]
        peak = max(peak, c.high)
        # breakeven: TP1 체결 후 SL을 본전으로
        cur_sl = entry if (be and be_active) else sl_price
        # SL 우선(보수)
        if c.low <= cur_sl and remaining > 0:
            _sell(remaining, cur_sl); remaining = 0.0; break
        # trailing
        if trail and remaining > 0 and peak >= entry * (1 + trail[0] / 100):
            trail_price = peak * (1 - trail[1] / 100)
            if c.low <= trail_price:
                _sell(remaining, max(trail_price, cur_sl)); remaining = 0.0; break
        # TP 체결
        for k in range(len(tps)):
            if not filled[k] and c.high >= tps[k] and remaining > 0:
                q = min(qs[k], remaining)
                _sell(q, tps[k]); remaining -= q; filled[k] = True
                if k == 0:
                    be_active = True
        if remaining <= 1e-9:
            break
    if remaining > 1e-9:  # max_hold 도달 → 종가 청산
        last = candles[min(len(candles) - 1, ei + cfg["max_hold"])]
        _sell(remaining, last.close)
    return net * 100


def evaluate(entries_by_symstrat, candles_by_sym, strategy_id, min_score, cfg):
    """(전략, min_score, cfg) → win%·avg_ret·trades."""
    rets = []
    for (sym, sid), sigs in entries_by_symstrat.items():
        if sid != strategy_id:
            continue
        cs = candles_by_sym[sym]
        busy = -1
        for (idx, score) in sigs:
            if score < min_score or idx <= busy:
                continue
            r = simulate_exit(cs, idx, cfg)
            if r is None:
                continue
            rets.append(r)
            busy = idx + cfg["max_hold"]  # 1포지션: 보유 중 신호 skip
    n = len(rets)
    if n == 0:
        return {"win": 0.0, "avg": 0.0, "trades": 0}
    return {
        "win": sum(1 for r in rets if r > 0) / n * 100,
        "avg": sum(rets) / n,
        "trades": n,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--strategy", default=None, help="단일 전략만 (생략 시 전체)")
    args = ap.parse_args()

    print(f"=== 승률 최적화 — 랜덤 유니버스 n={args.n} seed={args.seed} (일봉·실비용) ===")
    uni = select_random_universe(args.n, args.seed)
    candles_by_sym = {s: load_daily(s) for s in uni}
    candles_by_sym = {s: c for s, c in candles_by_sym.items() if len(c) >= 120}
    targets = [args.strategy] if args.strategy else STRATEGIES

    print(f"진입신호 수집 ({len(candles_by_sym)}종목 × {len(targets)}전략)...", file=sys.stderr)
    entries = {}
    for sym, cs in candles_by_sym.items():
        for sid in targets:
            entries[(sym, sid)] = collect_entries(sym, sid, cs)

    print(f"\n{'전략':10} {'min_score':>9} {'청산프리셋':>14} {'trades':>7} {'win%':>6} {'avg_ret%':>9} {'평가'}")
    best = {}
    for sid in targets:
        for ms in MIN_SCORES:
            for pname, cfg in EXIT_PRESETS.items():
                r = evaluate(entries, candles_by_sym, sid, ms, cfg)
                if r["trades"] < 5:
                    continue
                ok = r["avg"] > 0 and r["trades"] >= MIN_TRADES
                flag = ""
                if pname == "wide_sl_trap" and r["win"] >= 70 and r["avg"] <= 0:
                    flag = "← 승률↑ 기대값음수(함정)"
                if ok and (sid not in best or r["win"] > best[sid]["win"]):
                    best[sid] = {"min_score": ms, "preset": pname, **r}
                # 출력은 의미있는 셀만 (win>=55 또는 trades 충분)
                if r["win"] >= 55 or r["trades"] >= MIN_TRADES:
                    mark = "✓" if ok else ""
                    print(f"{sid:10} {ms:>9} {pname:>14} {r['trades']:>7} {r['win']:>5.0f}% {r['avg']:>+8.3f} {mark}{flag}")

    print("\n=== 전략별 최적(기대값>0 & trades>=30, 승률 최대) ===")
    for sid in targets:
        if sid in best:
            b = best[sid]
            gap = 80 - b["win"]
            print(f"  {sid:10}: min_score={b['min_score']} {b['preset']} → "
                  f"win {b['win']:.0f}% / avg {b['avg']:+.3f}% / {b['trades']}거래  (목표 80%까지 {gap:+.0f}%p)")
        else:
            print(f"  {sid:10}: 기대값>0 & trades>=30 셀 없음")


if __name__ == "__main__":
    main()
