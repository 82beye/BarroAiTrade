"""종베 백테스트 — 삼박자(D-R44) shadow 비교축 측정.

기존 ablation(backtest_closing_bet_intraday)의 헬퍼/유니버스/청산을 재사용하되,
**baseline closing_bet 신호 거래**를 closing_bet_filters 의 삼박자 게이트로 분할해
per-trade 엣지 기여를 측정한다(전략 로직 불변·관측 전용 shadow).

비교 버킷:
- baseline_all       : baseline 종베 신호 전체(대조군)
- triple_pass / fail : 삼박자(D-R44: 엔벨 상단돌파 ∧ 이격 노란불 ∧ 거래대금≥1000억) 충족/미충족
- env_pass / disp_pass: 개별 요인(D-R42/43) 단독 충족 진단

판정: triple_pass 의 net 이 baseline_all·triple_fail 보다 유의하게 높으면 필터가 엣지 기여.
(종베 ablation 교훈: 표본 축소가 per-trade 개선을 초과하면 net 악화 — 그 여부를 측정)

사용: python scripts/backtest_closing_bet_triple_shadow.py [TOPN] [MAX_DAILY]
"""
from __future__ import annotations

import glob
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.strategy.closing_bet import ClosingBetStrategy  # noqa: E402
from backend.core.strategy.closing_bet_filters import (  # noqa: E402
    disparity_yellow,
    envelope_upper_break,
    triple_factor_buy,
)
from backend.models.market import MarketType  # noqa: E402
from backend.models.strategy import AnalysisContext  # noqa: E402
from scripts.backtest_closing_bet_intraday import (  # noqa: E402
    COST_MODEL, COST_REAL, DAILY, MIN5,
    _load_5m_by_date, _load_daily, _params, _simulate_exit,
)

VALUE_FLOOR_WON = 1.0e11  # D-R44 거래대금 하한 1000억


def run(top_n: int, max_daily: int) -> None:
    scored = []
    for f in glob.glob(DAILY + "/*.json"):
        sym = Path(f).stem
        if not Path(f"{MIN5}/{sym}.json").exists():
            continue
        try:
            d = json.load(open(f))["data"]
        except Exception:
            continue
        if len(d) < 80:
            continue
        atv = sum(float(x["close"]) * float(x["volume"]) for x in d[-120:]) / min(120, len(d))
        scored.append((atv, f, sym))
    scored.sort(reverse=True)
    universe = scored[:top_n]
    print(f"[종베 삼박자 shadow] 5분봉 보유 거래대금상위 {len(universe)}종목 …")

    baseline = ClosingBetStrategy(_params(False, False))
    buckets: dict = {k: [] for k in
                     ("baseline_all", "triple_pass", "triple_fail", "env_pass", "disp_pass")}
    n_days = 0

    for _, f, sym in universe:
        daily = _load_daily(f, max_daily)
        if len(daily) < 70:
            continue
        m5 = _load_5m_by_date(sym)
        if not m5:
            continue
        for i in range(60, len(daily)):
            date = daily[i].timestamp.date()
            intraday = m5.get(date)
            if not intraday:
                continue
            n_days += 1
            ctx = AnalysisContext(symbol=sym, name=sym, candles=daily[:i + 1],
                                  market_type=MarketType.STOCK, intraday_candles=intraday)
            if baseline._analyze_v2(ctx) is None:
                continue
            pnl = _simulate_exit(daily, i)
            sub = daily[:i + 1]
            day_value = daily[i].close * daily[i].volume
            triple = triple_factor_buy(sub, day_value, value_floor_won=VALUE_FLOOR_WON)
            buckets["baseline_all"].append(pnl)
            buckets["triple_pass" if triple else "triple_fail"].append(pnl)
            if envelope_upper_break(sub):
                buckets["env_pass"].append(pnl)
            if disparity_yellow(sub):
                buckets["disp_pass"].append(pnl)

    print(f"  평가 stock-day(5분봉 보유): {n_days}\n")
    hdr = f"{'버킷':14} {'트립':>6} {'승률%':>6} {'gross%':>8} {'net_0.55':>9} {'net_0.90':>9}"
    print(hdr)
    results = {}
    for k in buckets:
        arr = buckets[k]
        if not arr:
            print(f"{k:14} {0:>6}  (해당 없음)")
            results[k] = {"trades": 0}
            continue
        g = statistics.mean(arr)
        wr = sum(1 for x in arr if x > 0) / len(arr) * 100
        print(f"{k:14} {len(arr):>6} {wr:>6.1f} {g:>8.3f} "
              f"{g - COST_MODEL:>9.3f} {g - COST_REAL:>9.3f}")
        results[k] = {"trades": len(arr), "win_rate": round(wr, 1),
                      "gross_exp_pct": round(g, 3),
                      "net_model_0.55": round(g - COST_MODEL, 3),
                      "net_real_0.90": round(g - COST_REAL, 3)}

    # 판정: 삼박자 필터의 net@0.90 기여
    ba, tp = results.get("baseline_all", {}), results.get("triple_pass", {})
    verdict = "표본부족"
    if ba.get("trades") and tp.get("trades"):
        delta = tp["net_real_0.90"] - ba["net_real_0.90"]
        verdict = (f"삼박자 net@0.90 {tp['net_real_0.90']:+.3f}% vs baseline "
                   f"{ba['net_real_0.90']:+.3f}% (Δ{delta:+.3f}%p, 트립 "
                   f"{ba['trades']}→{tp['trades']}) → "
                   + ("엣지 기여(net↑)" if delta > 0 else "기여 없음(net 미개선)"))
    print(f"\n[판정] {verdict}")

    out = Path(__file__).resolve().parents[1] / \
        "docs/04-report/features/2026-06-21-closing-bet-triple-shadow.json"
    out.write_text(json.dumps({"eval_stock_days": n_days, "value_floor_won": VALUE_FLOOR_WON,
                               "buckets": results, "verdict": verdict},
                              ensure_ascii=False, indent=2))
    print(f"결과 저장: {out}")


if __name__ == "__main__":
    top = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    md = int(sys.argv[2]) if len(sys.argv) > 2 else 250
    run(top, md)
