"""주도주 — 마지막 봉(당일) 종가 시점 진입 신호 진단.

simulate_leaders.py 와 동일하게 키움 ranking 으로 당일 주도주를 선정하고,
각 종목 일봉의 **마지막 봉(=당일)** 시점에서 5전략이 진입 신호를 내는지 진단한다.
IntradaySimulator._build_strategies 재사용 → 백테스트와 동일 파라미터
(f_zone min_atr_pct=0.035, sf_zone 필터 OFF, scalping provider auto-load).

"오늘 X 전략만 매매됐다" 류 관찰을, 실제 전략 신호 발화 여부와 대조한다.

사용:
    set -a; . ./.env.local; set +a
    venv/bin/python scripts/diagnose_signals.py
    venv/bin/python scripts/diagnose_signals.py --top 15 --min-score 0.6
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore", category=DeprecationWarning)

from pydantic import SecretStr

from backend.core.backtester.intraday_simulator import _build_strategies
from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.gateway.kiwoom_native_rank import KiwoomNativeLeaderPicker
from backend.models.market import MarketType
from backend.models.strategy import AnalysisContext

STRATEGY_IDS = ["f_zone", "sf_zone", "gold_zone", "swing_38", "scalping_consensus"]


async def main() -> None:
    ap = argparse.ArgumentParser(description="주도주 당일 종가 시점 진입 신호 진단")
    ap.add_argument("--top", type=int, default=10, help="주도주 top N (기본 10)")
    ap.add_argument("--min-flu", type=float, default=1.0, help="최소 등락률 %% (기본 1.0)")
    ap.add_argument("--min-score", type=float, default=0.5, help="최소 점수 (기본 0.5)")
    args = ap.parse_args()

    oauth = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )
    picker = KiwoomNativeLeaderPicker(
        oauth=oauth, min_flu_rate=args.min_flu, min_score=args.min_score,
    )
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth)

    leaders = await picker.pick(top_n=args.top)
    strategies = _build_strategies(STRATEGY_IDS)

    print("=" * 84)
    print(f"주도주 {len(leaders)}종목 — 마지막 봉(당일) 종가 시점 진입 신호 진단")
    print("(IntradaySimulator._build_strategies 동일 파라미터)")
    print("=" * 84)

    fire = {sid: 0 for sid in STRATEGY_IDS}
    for c in leaders:
        candles = await fetcher.fetch_daily(symbol=c.symbol)
        if len(candles) < 61:
            print(f"\n● {c.symbol} {c.name}  캔들 부족 ({len(candles)}봉) — 스킵")
            continue
        last, prev = candles[-1], candles[-2]
        chg = (last.close - prev.close) / prev.close * 100 if prev.close else 0.0
        print(
            f"\n● {c.symbol} {c.name}  {last.timestamp:%Y-%m-%d} "
            f"close={last.close:,.0f} ({chg:+.2f}%)  flu%={c.flu_rate:+.1f} "
            f"score={c.score:.3f}  [{len(candles)}봉]"
        )
        ctx = AnalysisContext(
            symbol=c.symbol, name=c.name, candles=candles, market_type=MarketType.STOCK
        )
        for strat, sid in zip(strategies, STRATEGY_IDS):
            try:
                sig = strat.analyze(ctx)
            except Exception as e:  # noqa: BLE001
                print(f"   {sid:<20} ⚠ {type(e).__name__}: {e}")
                continue
            if sig:
                fire[sid] += 1
                print(f"   {sid:<20} ✅ score={sig.score}  type={sig.signal_type}")
                print(f"   {'':<22}{sig.reason}")
            else:
                print(f"   {sid:<20} —  (미발화)")

    print("\n" + "=" * 84)
    print("발화 집계:  " + "   ".join(f"{sid}={fire[sid]}" for sid in STRATEGY_IDS))
    print("=" * 84)


if __name__ == "__main__":
    asyncio.run(main())
