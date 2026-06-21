#!/usr/bin/env python3
"""매매시그널 결정 audit — 특정일 후보의 게이트 체인 결정을 재현·집계.

**관측성 전용**: config 무변경. 후보가 왜 선정/탈락하는지(어느 게이트)를 집계하는 공백을
메움 — trap_guard 임계 결정(측정 후 HITL)의 측정 기반. 데몬 게이트 체인을 캐시 데이터로
replay 한다(SKIP-GAP / SKIP-TRAP). 재사용:
- `simulation_log.csv`(당일 데몬 sim 후보·score·flu) + `ohlcv_cache(_5m)`(캔들)
- `trap_guard.evaluate_trap_guard` — 트랩 판정(임계 인자화)
- 데몬 갭가드 로직(`_ZONE_MAX_FLU`·`_GAP_GUARD_STRATEGIES`) 재현

핵심: 차단된 후보가 실제로 페이드했는지 **forward-return**(09:30 추격→EOD, 고점추격→EOD)로
정당성 검증. 일봉캐시가 D 미포함이면 5분봉에서 D 일봉 합성.

사용:
    python scripts/_signal_decision_audit.py --date 2026-06-19
    python scripts/_signal_decision_audit.py --date 2026-06-19 --movers 10   # 캐시서 고flu 무버 보강
    python scripts/_signal_decision_audit.py --date 2026-06-19 --json out.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from backend.models.market import OHLCV, MarketType  # noqa: E402
from backend.core.strategy.trap_guard import TrapGuardConfig, evaluate_trap_guard  # noqa: E402
from backend.core.strategy.indicators import atr_pct  # noqa: E402

_DATA = Path(os.environ.get("BARRO_DATA_DIR", "/Users/beye/workspace/BarroAiTrade/data"))
_KST = timezone(timedelta(hours=9))

# 데몬 갭가드 재현(intraday_buy_daemon.py 기본값)
_ZONE_MAX_FLU = 15.0
_GAP_GUARD_STRATEGIES = {"gold_zone", "f_zone"}


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("data")
    except Exception:
        return None


def _to_daily(rows) -> list[OHLCV]:
    out = []
    for c in rows:
        ts = datetime.strptime(c["date"], "%Y%m%d").replace(tzinfo=_KST)
        out.append(OHLCV(symbol="X", timestamp=ts, open=float(c["open"]), high=float(c["high"]),
                         low=float(c["low"]), close=float(c["close"]), volume=float(c["volume"]),
                         market_type=MarketType.STOCK))
    return out


def _day5(sym: str, ymd: str):
    m5 = _load(_DATA / "ohlcv_cache_5m" / f"{sym}.json")
    if not m5:
        return None
    return [b for b in m5 if b.get("date") == ymd] or None


def build_metrics(sym: str, ymd: str) -> dict | None:
    """종목의 D 일봉 시리즈 + 6/19류 지표(flu·gap·wick·atr·일중위치·forward-return)."""
    daily = _load(_DATA / "ohlcv_cache" / f"{sym}.json")
    day5 = _day5(sym, ymd)
    if not daily or not day5 or len(day5) < 3:
        return None
    o = float(day5[0]["open"]); c = float(day5[-1]["close"])
    hi = max(float(b["high"]) for b in day5); lo = min(float(b["low"]) for b in day5)
    candles = _to_daily(daily)
    bar = OHLCV(symbol=sym, timestamp=datetime.strptime(ymd, "%Y%m%d").replace(tzinfo=_KST),
                open=o, high=hi, low=lo, close=c, volume=sum(float(b["volume"]) for b in day5),
                market_type=MarketType.STOCK)
    if candles and candles[-1].timestamp.date() == bar.timestamp.date():
        candles[-1] = bar
    else:
        candles.append(bar)
    prev_close = float(daily[-1]["close"]) if daily[-1]["date"] != ymd else float(daily[-2]["close"])
    flu = (c - prev_close) / prev_close * 100 if prev_close else 0.0
    gap = (o - prev_close) / prev_close * 100 if prev_close else 0.0
    body = c - o
    wick = (hi - c) / body if body > 0 else 0.0
    av = atr_pct(candles, 14) * 100
    # 일중위치(0=저,100=고): 09:30 추격가가 당일 range 어디인가
    c0930 = next((b for b in day5 if b["time"] == "093000"), day5[0])
    e930 = float(c0930["close"])
    pos = (e930 - lo) / (hi - lo) * 100 if hi > lo else 0.0
    # forward-return
    eod = c
    r930 = (eod - e930) / e930 * 100 if e930 else 0.0
    rhigh = (eod - hi) / hi * 100 if hi else 0.0
    return dict(symbol=sym, flu=flu, gap=gap, wick=wick, atr=av, pos930=pos,
                fwd_930=r930, fwd_high=rhigh, candles=candles, prev_close=prev_close, close=c)


def gate_replay(m: dict, strategy: str, trap: TrapGuardConfig) -> tuple[str, str]:
    """데몬 게이트 체인 재현 → (verdict, reason). PASS/SKIP-GAP/SKIP-TRAP."""
    if strategy in _GAP_GUARD_STRATEGIES and m["flu"] >= _ZONE_MAX_FLU:
        return ("SKIP-GAP", f"flu {m['flu']:.1f}% ≥ {_ZONE_MAX_FLU}%")
    if trap.any_enabled():
        blocked, reason = evaluate_trap_guard(m["candles"], trap, flu_rate=m["flu"])
        if blocked:
            return ("SKIP-TRAP", reason)
    return ("PASS", "ok")


def load_sim_candidates(ymd_dash: str) -> list[dict]:
    """simulation_log.csv 의 당일 후보(symbol·name·strategy·score·flu)."""
    p = _DATA / "simulation_log.csv"
    out = []
    if not p.exists():
        return out
    with p.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("run_at", "").startswith(ymd_dash):
                out.append({"symbol": r["symbol"], "name": r.get("name", r["symbol"]),
                            "strategy": r["strategy"], "score": float(r.get("score") or 0),
                            "flu_rate": float(r.get("flu_rate") or 0)})
    return out


def reconstruct_movers(ymd: str, top: int) -> list[str]:
    """5분봉 캐시서 D 등락률 상위 무버 top-N(트랩 후보 보강)."""
    rows = []
    for fp in (_DATA / "ohlcv_cache_5m").glob("*.json"):
        sym = fp.stem
        day5 = _day5(sym, ymd)
        if not day5 or len(day5) < 5:
            continue
        daily = _load(_DATA / "ohlcv_cache" / f"{sym}.json")
        if not daily:
            continue
        pc = float(daily[-1]["close"]) if daily[-1]["date"] != ymd else float(daily[-2]["close"])
        if pc <= 0:
            continue
        flu = (float(day5[-1]["close"]) - pc) / pc * 100
        rows.append((sym, flu))
    rows.sort(key=lambda x: -x[1])
    return [s for s, _ in rows[:top]]


def main() -> int:
    ap = argparse.ArgumentParser(description="매매시그널 결정 audit(게이트 replay + forward-return)")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--movers", type=int, default=0, help="캐시서 고flu 무버 top-N 보강")
    ap.add_argument("--trap-k", type=float, default=2.5)
    ap.add_argument("--trap-wick", type=float, default=1.0)
    ap.add_argument("--trap-gap-mult", type=float, default=3.0)
    ap.add_argument("--trap-gap-abs", type=float, default=15.0)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    ymd = args.date.replace("-", "")

    trap = TrapGuardConfig(over_ext_k_atr=args.trap_k, upper_wick_max=args.trap_wick,
                           gap_atr_mult=args.trap_gap_mult, gap_abs_max_pct=args.trap_gap_abs)

    sim = load_sim_candidates(args.date)
    cand = {c["symbol"]: c for c in sim}
    for s in (reconstruct_movers(ymd, args.movers) if args.movers else []):
        cand.setdefault(s, {"symbol": s, "name": s, "strategy": "f_zone", "score": 0.0, "flu_rate": 0.0})

    results = []
    for sym, c in cand.items():
        m = build_metrics(sym, ymd)
        if not m:
            continue
        verdict, reason = gate_replay(m, c["strategy"], trap)
        results.append({**{k: m[k] for k in ("symbol", "flu", "gap", "wick", "atr", "pos930", "fwd_930", "fwd_high")},
                        "name": c["name"], "strategy": c["strategy"], "score": c["score"],
                        "verdict": verdict, "reason": reason})

    print("=" * 92)
    print(f"매매시그널 결정 audit — {args.date} [관측성·게이트 replay, config 무변경]")
    print(f"트랩 임계(측정용): over_ext k={args.trap_k} wick={args.trap_wick} gap_mult={args.trap_gap_mult} abs={args.trap_gap_abs}%")
    print("=" * 92)
    print(f"{'종목':8}{'전략':9}{'score':>6}{'flu%':>7}{'gap%':>7}{'wick':>6}{'pos930':>7}"
          f"{'판정':12}{'고점→EOD':>9}  사유")
    for r in sorted(results, key=lambda x: x["verdict"]):
        print(f"{r['symbol']:8}{r['strategy']:9}{r['score']:6.2f}{r['flu']:7.1f}{r['gap']:7.1f}"
              f"{r['wick']:6.2f}{r['pos930']:6.0f}%  {r['verdict']:11}{r['fwd_high']:8.1f}%  {r['reason']}")

    # 집계
    from collections import Counter
    vc = Counter(r["verdict"] for r in results)
    blocked = [r for r in results if r["verdict"].startswith("SKIP")]
    passed = [r for r in results if r["verdict"] == "PASS"]
    print(f"\n{'─'*92}")
    print(f"후보 {len(results)} | " + " ".join(f"{k}={v}" for k, v in vc.items()))
    if blocked:
        avg_h = sum(r["fwd_high"] for r in blocked) / len(blocked)
        avg_9 = sum(r["fwd_930"] for r in blocked) / len(blocked)
        print(f"차단군({len(blocked)}) forward-return: 고점추격→EOD 평균 {avg_h:+.1f}% / 09:30추격→EOD {avg_9:+.1f}%")
    if passed:
        avg_h = sum(r["fwd_high"] for r in passed) / len(passed)
        print(f"통과군({len(passed)}) forward-return: 고점추격→EOD 평균 {avg_h:+.1f}%")
    print("→ 차단군 고점추격 손실이 통과군보다 크면 트랩가드 차단 정당(개미꼬시기 포착).")

    if args.json:
        for r in results:
            r.pop("candles", None)
        Path(args.json).write_text(json.dumps({"date": args.date, "trap": trap.__dict__, "results": results},
                                              ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 저장: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
