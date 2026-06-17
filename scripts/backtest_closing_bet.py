"""
종가베팅(종베) ClosingBetStrategy 일봉 스캐폴드 백테스트 — thetrading-uplift Increment 1.

⚠️ 한계(정직 고지):
- 종베는 본래 오버나잇(15:00 종가 진입 → 익일 9~10시 슈팅) + intraday 의존(분봉 자금유입·
  존 진입가)이다. 본 백테스트는 **일봉 스캐폴드**만 검증한다:
    · 진입창(15:00~15:20)은 백테스터가 ctx.timestamp=now() 라 재현 불가 → require_eod_window=False.
    · 분봉 자금유입·존 진입가·거래대금 rank hard-cut은 기본 OFF(intraday/leader 메타 부재).
  따라서 측정되는 엣지 = "**신고가 돌파 5% 장대양봉 → 수일 내 +TP/-SL**" 의 일봉 근사.
- StrategyBacktester 는 전략 exit_plan 이 아니라 ExitParams 로 청산 → 종베 의도값을 명시 주입
  (tp1 +2.7%/50%, tp2 +4.5%, sl -3%, max_hold 3봉=D1~D3).
- 진입 ≈ 신호(장대양봉) 캔들 종가. 비용은 0으로 gross 산출 후 시나리오별 수동 차감.

사용: python scripts/backtest_closing_bet.py [TOPN] [MAX_BARS]
"""
from __future__ import annotations

import glob
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.strategy.backtester import (  # noqa: E402
    BacktestConfig, ExitParams, StrategyBacktester,
)
from backend.core.strategy.closing_bet import (  # noqa: E402
    ClosingBetParams, ClosingBetStrategy,
)
from backend.models.market import MarketType, OHLCV  # noqa: E402

CACHE = "/Users/beye/workspace/BarroAiTrade/data/ohlcv_cache"

# 비용 시나리오 (왕복 총비용률, %) — 설계 §4.2 비용모델 발견.
COST_MODEL = 0.55     # 현행 모델(편도 0.175%×2 + 매도세 0.20%)
COST_REAL = 0.90      # 실측(편도 0.35%×2 + 0.20%) — fill_audit 186건 역산
COST_PREF = 0.40      # 우대요율 가정(참고)

# 종베 청산 의도값 (ExitParams)
EXIT = ExitParams(
    take_profit_1_pct=0.027, take_profit_1_ratio=0.5,
    take_profit_2_pct=0.045, stop_loss_pct=-0.03, max_hold_candles=3,
)
CONFIG = BacktestConfig(commission_pct=0.0, slippage_pct=0.0, min_signal_score=4.0)


def load_candles(path: str, max_bars: int) -> list[OHLCV]:
    sym = Path(path).stem
    d = json.load(open(path))["data"]
    out: list[OHLCV] = []
    for r in d[-max_bars:]:
        try:
            out.append(OHLCV(
                symbol=sym,
                timestamp=datetime.strptime(str(r["date"]), "%Y%m%d"),
                open=float(r["open"]), high=float(r["high"]),
                low=float(r["low"]), close=float(r["close"]),
                volume=float(r["volume"]), market_type=MarketType.STOCK,
            ))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def build_universe(top_n: int, max_bars: int) -> list[str]:
    """거래대금(close*volume) 평균 상위 top_n 종목 = 주도주 풀 근사."""
    scored: list[tuple[float, str]] = []
    for f in glob.glob(CACHE + "/*.json"):
        try:
            d = json.load(open(f))["data"]
        except Exception:
            continue
        if len(d) < 80:
            continue
        recent = d[-120:]
        try:
            atv = sum(float(x["close"]) * float(x["volume"]) for x in recent) / len(recent)
        except (KeyError, ValueError, TypeError):
            continue
        scored.append((atv, f))
    scored.sort(reverse=True)
    return [f for _, f in scored[:top_n]]


def run_variant(label: str, params: ClosingBetParams, universe: list[str],
                max_bars: int) -> dict:
    strat = ClosingBetStrategy(params)
    trades: list[dict] = []
    symbols_with_trades = 0
    period_start, period_end = None, None
    for f in universe:
        candles = load_candles(f, max_bars)
        if len(candles) < 60:
            continue
        if period_start is None or candles[0].timestamp < period_start:
            period_start = candles[0].timestamp
        if period_end is None or candles[-1].timestamp > period_end:
            period_end = candles[-1].timestamp
        bt = StrategyBacktester(strat, CONFIG, EXIT)
        try:
            rep = bt.run(Path(f).stem, candles, MarketType.STOCK, Path(f).stem)
        except ValueError:
            continue
        if rep.trades:
            symbols_with_trades += 1
        for t in rep.trades:
            if t.exit_time is None:
                continue
            trades.append({
                "symbol": rep.symbol, "pnl_pct": t.pnl_pct * 100,
                "exit_reason": t.exit_reason, "hold": t.hold_candles,
                "score": t.entry_signal_score,
            })
    return _aggregate(label, trades, symbols_with_trades, len(universe),
                      period_start, period_end)


def _aggregate(label, trades, sym_traded, universe_n, p_start, p_end) -> dict:
    n = len(trades)
    if n == 0:
        return {"label": label, "trades": 0}
    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_exp = statistics.mean(pnls)
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    return {
        "label": label,
        "period": (f"{p_start:%Y-%m-%d} ~ {p_end:%Y-%m-%d}" if p_start else "?"),
        "universe": universe_n, "symbols_with_trades": sym_traded,
        "trades": n,
        "win_rate": round(len(wins) / n * 100, 1),
        "gross_expectancy_pct": round(gross_exp, 3),
        "avg_win_pct": round(statistics.mean(wins), 2) if wins else 0.0,
        "avg_loss_pct": round(statistics.mean(losses), 2) if losses else 0.0,
        "median_pct": round(statistics.median(pnls), 3),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else float("inf"),
        "avg_hold": round(statistics.mean(t["hold"] for t in trades), 2),
        "avg_score": round(statistics.mean(t["score"] for t in trades), 2),
        "exit_reasons": reasons,
        # 비용 시나리오별 net 기대값(왕복비용 1회 차감)
        "net_expectancy_model_0.55": round(gross_exp - COST_MODEL, 3),
        "net_expectancy_real_0.90": round(gross_exp - COST_REAL, 3),
        "net_expectancy_pref_0.40": round(gross_exp - COST_PREF, 3),
    }


def main() -> None:
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    max_bars = int(sys.argv[2]) if len(sys.argv) > 2 else 400
    print(f"[종베 백테스트] universe top {top_n} (거래대금), 최근 {max_bars}봉 …")
    universe = build_universe(top_n, max_bars)
    print(f"  유니버스 확정: {len(universe)}종목")

    variants = [
        ("종베 full (신고가+장대양봉)", ClosingBetParams(require_eod_window=False)),
        ("ablation: 장대양봉만 (신고가 OFF)",
         ClosingBetParams(require_eod_window=False, require_new_high=False)),
        ("ablation: +ATR필터 0.035",
         ClosingBetParams(require_eod_window=False, min_atr_pct=0.035)),
    ]
    results = []
    for label, params in variants:
        r = run_variant(label, params, universe, max_bars)
        results.append(r)
        print(f"\n=== {label} ===")
        print(json.dumps(r, ensure_ascii=False, indent=2))

    out = Path(__file__).resolve().parents[1] / \
        "docs/04-report/features/2026-06-17-closing-bet-backtest.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n결과 저장: {out}")


if __name__ == "__main__":
    main()
