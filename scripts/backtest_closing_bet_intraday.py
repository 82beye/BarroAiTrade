"""
종베 분봉 게이트 ablation 백테스트 — thetrading-uplift Phase B2.

일봉 신고가·장대양봉 신호일에 그날 5분봉을 ctx.intraday_candles 로 주입해
**분봉 자금유입 + 존(골드존) 진입가** 게이트의 per-trade 엣지 기여를 측정한다.

변형: baseline(게이트 OFF) / +money_flow / +zone / +both.
청산: 신호일 종가 진입 → 익일부터 일봉 TP/SL(tp1 +2.7%/50%, tp2 +4.5%, sl -3%, max_hold 3).
비용: gross 산출 후 시나리오 수동차감(모델 0.55% / 실측 0.90%).

⚠️ 한계: 5분봉 캐시는 최근 ~45일만 → 신호 표본이 짧은 윈도우에 한정(일봉 OOS가 주 게이트).

사용: python scripts/backtest_closing_bet_intraday.py [TOPN] [MAX_DAILY]
"""
from __future__ import annotations

import glob
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.strategy.closing_bet import (  # noqa: E402
    ClosingBetParams, ClosingBetStrategy,
)
from backend.models.market import MarketType, OHLCV  # noqa: E402
from backend.models.strategy import AnalysisContext  # noqa: E402

_DATA = "/Users/beye/workspace/BarroAiTrade/data"
DAILY = _DATA + "/ohlcv_cache"
MIN5 = _DATA + "/ohlcv_cache_5m"

COST_MODEL = 0.55
COST_REAL = 0.90

# 청산 파라미터 (종베 의도)
TP1, TP1Q, TP2, SL, MAXHOLD = 0.027, 0.5, 0.045, -0.03, 3


def _load_daily(path: str, max_bars: int) -> list[OHLCV]:
    sym = Path(path).stem
    out = []
    for r in json.load(open(path))["data"][-max_bars:]:
        try:
            out.append(OHLCV(symbol=sym, timestamp=datetime.strptime(str(r["date"]), "%Y%m%d"),
                             open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
                             close=float(r["close"]), volume=float(r["volume"]),
                             market_type=MarketType.STOCK))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _load_5m_by_date(sym: str) -> dict:
    """5분봉을 날짜별로 그룹핑. {date: [OHLCV...]}"""
    path = f"{MIN5}/{sym}.json"
    if not Path(path).exists():
        return {}
    by_date: dict = {}
    for r in json.load(open(path)).get("data", []):
        try:
            ts = datetime.strptime(str(r["datetime"]), "%Y%m%d%H%M%S")
            c = OHLCV(symbol=sym, timestamp=ts, open=float(r["open"]), high=float(r["high"]),
                      low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"]),
                      market_type=MarketType.STOCK)
            by_date.setdefault(ts.date(), []).append(c)
        except (KeyError, ValueError, TypeError):
            continue
    return by_date


def _simulate_exit(daily: list[OHLCV], entry_idx: int) -> float:
    """진입(entry_idx 종가) 후 익일부터 일봉 TP/SL/max_hold. gross pnl% 반환."""
    entry = daily[entry_idx].close
    if entry <= 0:
        return 0.0
    tp1p, tp2p, slp = entry * (1 + TP1), entry * (1 + TP2), entry * (1 + SL)
    filled1 = False
    for j in range(entry_idx + 1, min(entry_idx + 1 + MAXHOLD, len(daily))):
        bar = daily[j]
        # TP 우선(레포 백테스터 일관) → SL
        if not filled1 and bar.high >= tp1p:
            filled1 = True
            if bar.high >= tp2p:
                return (TP1 * TP1Q + TP2 * (1 - TP1Q)) * 100
        elif filled1 and bar.high >= tp2p:
            return (TP1 * TP1Q + TP2 * (1 - TP1Q)) * 100
        if bar.low <= slp:
            if filled1:
                return (TP1 * TP1Q + SL * (1 - TP1Q)) * 100
            return SL * 100
        if j == min(entry_idx + MAXHOLD, len(daily) - 1):
            ex = (bar.close - entry) / entry
            if filled1:
                return (TP1 * TP1Q + ex * (1 - TP1Q)) * 100
            return ex * 100
    return 0.0


def _params(money_flow: bool, zone: bool) -> ClosingBetParams:
    return ClosingBetParams(require_eod_window=False, require_new_high=True,
                            min_atr_pct=0.035, require_money_flow=money_flow, require_zone=zone)


def run(top_n: int, max_daily: int) -> None:
    # 유니버스: 거래대금 상위 + 5분봉 캐시 존재
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
    print(f"[종베 분봉 ablation] 5분봉 보유 거래대금상위 {len(universe)}종목 …")

    variants = {"baseline": (False, False), "+money_flow": (True, False),
                "+zone": (False, True), "+both": (True, True)}
    strats = {k: ClosingBetStrategy(_params(*v)) for k, v in variants.items()}
    pnls: dict = {k: [] for k in variants}
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
                continue                       # 신호일 5분봉 없으면 게이트 평가 불가
            n_days += 1
            ctx = AnalysisContext(symbol=sym, name=sym, candles=daily[:i + 1],
                                  market_type=MarketType.STOCK, intraday_candles=intraday)
            for k, strat in strats.items():
                if strat._analyze_v2(ctx) is not None:
                    pnls[k].append(_simulate_exit(daily, i))

    print(f"  평가 stock-day(5분봉 보유): {n_days}\n")
    print(f"{'변형':14} {'트립':>6} {'승률%':>6} {'gross%':>8} "
          f"{'net_0.55':>9} {'net_0.90':>9}")
    results = {}
    for k in variants:
        arr = pnls[k]
        if not arr:
            print(f"{k:14} {0:>6}  (신호 없음)")
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

    out = Path(__file__).resolve().parents[1] / \
        "docs/04-report/features/2026-06-18-closing-bet-intraday-ablation.json"
    out.write_text(json.dumps({"eval_stock_days": n_days, "variants": results},
                              ensure_ascii=False, indent=2))
    print(f"\n결과 저장: {out}")


if __name__ == "__main__":
    top = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    md = int(sys.argv[2]) if len(sys.argv) > 2 else 250
    run(top, md)
