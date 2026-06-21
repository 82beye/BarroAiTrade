#!/usr/bin/env python3
"""TP/SL 구간 진단 — gross vs net(비용 차감) + regime_exit(국면 적응) 효과 측정.

**관측성 전용**: config 를 바꾸지 않고 읽기·계산만. trap_guard/regime_exit 고도화의
임계 결정(측정 후 HITL)을 위한 측정 인프라. 재사용:
- `STRATEGY_EXIT_PROFILES`·`resolve_policy`·`ExitPolicy` (holding_evaluator)
- `RegimeExitConfig.apply` (regime_exit) — 국면별 SL/TP 조정 효과
- `trading_costs` (COMMISSION_RATE·TAX_RATE_SELL) — net 변환
- `_daily_strategy_audit.fifo_roundtrip_pnl` — 실현 net (선택, --date)

핵심 진단:
1. 라이브 TP/SL 임계가 **전부 gross** → 비용(왕복 ~0.55~0.90%) 차감 후 net 잠식. 타이트한
   분할익절(gold +2%)일수록 잠식 비율 큼.
2. 비용 가정 2종 병행: 현행 모델(편도 0.175%·왕복 0.55%) vs 6/17 실측 재분석(편도 0.350%·
   왕복 ~0.90%, fill_audit 186건). 어느 쪽이든 net 갭을 정량화.
3. regime_exit 활성 시 국면별 SL/TP 조정폭(현재 default-OFF=무조정).

사용:
    python scripts/_tpsl_zone_diagnostic.py
    python scripts/_tpsl_zone_diagnostic.py --date 2026-06-16   # 실현 net 맥락 추가
    python scripts/_tpsl_zone_diagnostic.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from backend.core.risk.holding_evaluator import ExitPolicy, resolve_policy  # noqa: E402
from backend.core.risk.regime_exit import RegimeExitConfig  # noqa: E402
from backend.core.backtester.market_regime import MarketRegime  # noqa: E402
from backend.core.trading_costs import COMMISSION_RATE, TAX_RATE_SELL  # noqa: E402

_DATA = Path(os.environ.get("BARRO_DATA_DIR", "/Users/beye/workspace/BarroAiTrade/data"))

STRATEGIES = ["f_zone", "sf_zone", "gold_zone", "swing_38", "closing_bet"]

# 비용 가정(왕복 %) — round_trip = 편도수수료×2 + 매도세.
# 정정전: 종전 default(2배 과소). 실측: 2026-06-21 정정 후 = 현행 COMMISSION_RATE(fill_audit 298행).
COST_MODELS = {
    "정정전": (0.00175 * 2 + 0.002) * 100,                                   # 0.55%
    "실측": (float(COMMISSION_RATE) * 2 + float(TAX_RATE_SELL)) * 100,        # 0.90% (정정 후)
}

# regime_exit 예시 배수 (측정용 — 활성화 시 후보, 현재 default-OFF). (d) HITL 전 illustrative.
EXAMPLE_REX = RegimeExitConfig(
    enabled=True,
    sideways_sl_mult=0.75, sideways_tp_mult=1.0,   # SIDEWAYS: SL 타이트(-4→-3)
    bull_tp_mult=1.3, bull_sl_mult=1.0,            # BULL: TP 확장(+5→+6.5)
    bearish_sl_mult=1.25,                          # BEARISH: SL 완화
)


def profile_policy(strategy: str) -> ExitPolicy:
    """전략별 라이브 청산 프로파일(STRATEGY_EXIT_PROFILES 진실원천)."""
    return resolve_policy(ExitPolicy(), strategy)


def net_after_cost(gross_pct: float, round_trip_pct: float) -> float:
    """gross 수익률(%)에서 왕복 비용(%)을 차감한 net 수익률(%)."""
    return gross_pct - round_trip_pct


def gross_net_table() -> list[dict]:
    """전략별 TP/SL/partial 의 gross vs net(2 비용가정) 표."""
    rows = []
    for s in STRATEGIES:
        p = profile_policy(s)
        tp = float(p.take_profit_pct)
        sl = float(p.stop_loss_pct)
        ptp = float(p.partial_tp_pct)
        row = {"strategy": s, "gross_tp": tp, "gross_sl": sl, "gross_partial_tp": ptp}
        for name, rt in COST_MODELS.items():
            row[f"net_tp[{name}]"] = round(net_after_cost(tp, rt), 3)
            row[f"net_partial[{name}]"] = round(net_after_cost(ptp, rt), 3)
            # SL 은 손실이므로 비용이 더 깊게(net 손실 확대)
            row[f"net_sl[{name}]"] = round(sl - rt, 3)
            # 분할익절 비용 잠식 비율(%): 작은 TP 일수록 큼
            row[f"partial_erosion%[{name}]"] = round(rt / ptp * 100, 1) if ptp > 0 else None
        rows.append(row)
    return rows


def regime_effect_table(rex: RegimeExitConfig = EXAMPLE_REX) -> list[dict]:
    """국면별 regime_exit 적용 시 SL/TP 조정폭(현재 default-OFF=무조정)."""
    rows = []
    for s in STRATEGIES:
        base = profile_policy(s)
        row = {"strategy": s, "base_sl": float(base.stop_loss_pct), "base_tp": float(base.take_profit_pct)}
        for reg in (MarketRegime.SIDEWAYS, MarketRegime.BULL, MarketRegime.BEARISH):
            adj = rex.apply(base, reg)
            row[f"{reg.value}_sl"] = float(adj.stop_loss_pct)
            row[f"{reg.value}_tp"] = float(adj.take_profit_pct)
        rows.append(row)
    return rows


def realized_context(date_str: str) -> dict | None:
    """해당일 strategy_audit JSON(있으면) 의 per_strategy 실현 net — 진실원천 인용."""
    for base in (_DATA.parent / "reports", _ROOT / "reports"):
        p = base / f"strategy_audit_{date_str}.json"
        if p.exists():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                return {"source": str(p), "total_realized": d.get("total_realized"),
                        "per_strategy": {k: v.get("realized") for k, v in d.get("per_strategy", {}).items()}}
            except Exception:
                return None
    return None


def _fmt(v):
    return f"{v:+.2f}" if isinstance(v, (int, float)) else str(v)


def main() -> int:
    ap = argparse.ArgumentParser(description="TP/SL 구간 진단(gross vs net + regime 효과)")
    ap.add_argument("--date", default=None, help="실현 net 맥락 추가(strategy_audit_<date>.json)")
    ap.add_argument("--json", default=None, help="JSON 저장 경로")
    args = ap.parse_args()

    gn = gross_net_table()
    rg = regime_effect_table()
    rc = realized_context(args.date) if args.date else None

    print("=" * 78)
    print("TP/SL 구간 진단 — gross vs net (비용 차감) [관측성 전용·config 무변경]")
    print("=" * 78)
    print(f"비용 가정: 정정전 {COST_MODELS['정정전']:.2f}% / 실측(정정후) {COST_MODELS['실측']:.2f}% (왕복)")
    print(f"\n{'전략':10}{'grossTP':>8}{'netTP(0.55)':>12}{'netTP(0.90)':>12}"
          f"{'grossSL':>8}{'netSL(0.90)':>12}{'분할TP':>7}{'잠식%(0.90)':>11}")
    for r in gn:
        print(f"{r['strategy']:10}{r['gross_tp']:8.1f}{r['net_tp[정정전]']:12.2f}{r['net_tp[실측]']:12.2f}"
              f"{r['gross_sl']:8.1f}{r['net_sl[실측]']:12.2f}{r['gross_partial_tp']:7.1f}"
              f"{r['partial_erosion%[실측]'] or 0:11.1f}")

    print(f"\n{'─'*78}\nregime_exit 효과(예시배수 — 현재 default-OFF=무조정, 활성화는 (d) HITL)")
    print(f"{'전략':10}{'baseSL':>7}{'SIDE_SL':>8}{'baseTP':>7}{'BULL_TP':>8}{'BEAR_SL':>8}")
    for r in rg:
        print(f"{r['strategy']:10}{r['base_sl']:7.1f}{r['sideways_sl']:8.2f}"
              f"{r['base_tp']:7.1f}{r['bull_tp']:8.2f}{r['bearish_sl']:8.2f}")

    if rc:
        print(f"\n{'─'*78}\n실현 맥락({args.date}, 진실원천 {Path(rc['source']).name}):")
        print(f"  total_realized: {rc['total_realized']}")
        for k, v in (rc["per_strategy"] or {}).items():
            print(f"  {k:12}{v:>14,.0f}" if isinstance(v, (int, float)) else f"  {k}: {v}")
    elif args.date:
        print(f"\n(주의) strategy_audit_{args.date}.json 없음 — `_daily_strategy_audit.py --date {args.date} --save` 선행")

    if args.json:
        out = {"cost_models": COST_MODELS, "gross_net": gn, "regime_effect": rg, "realized": rc}
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 저장: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
