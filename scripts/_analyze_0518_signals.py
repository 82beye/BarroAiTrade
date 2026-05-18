"""5/18 picker 종목별 진입 시그널 + 080220 1분봉 청산 단계별 분석."""
from __future__ import annotations

import asyncio
import os
import sys
import warnings
from datetime import date, datetime, time as dtime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore", category=DeprecationWarning)

from pydantic import SecretStr

from backend.core.backtester.intraday_simulator import (
    IntradaySimulator, _build_strategies, _exit_plan_for_strategy,
)
from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.gateway.kiwoom_native_rank import KiwoomNativeLeaderPicker
from backend.models.exit_order import PositionState
from backend.models.market import MarketType
from backend.models.strategy import AnalysisContext

STRATEGIES = ["f_zone", "sf_zone", "gold_zone", "swing_38"]


async def main():
    oauth = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )
    picker = KiwoomNativeLeaderPicker(oauth=oauth, min_flu_rate=0.0, min_score=0.5)
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth)

    leaders = await picker.pick(top_n=10)
    strat_objs = _build_strategies(STRATEGIES)
    sim_helper = IntradaySimulator()

    # ─── Part 1. 종목별 진입 시그널 진단 ───────────────────────────────────────
    print("=" * 110)
    print("Part 1. 5/18 picker top 10 — 진입 시그널 단계별 진단")
    print("=" * 110)
    print(
        f"  {'#':<2} {'종목':<22} {'flu%':>7} "
        f"{'f_zone':<8} {'sf_zone':<8} {'gold':<8} {'swing':<8}  비고"
    )
    print("-" * 110)
    candles_by_sym: dict[str, list] = {}
    name_by_sym: dict[str, str] = {}
    for i, c in enumerate(leaders, 1):
        daily = await fetcher.fetch_daily(symbol=c.symbol)
        candles_by_sym[c.symbol] = daily
        name_by_sym[c.symbol] = c.name
        if len(daily) < 32:
            print(f"  {i:<2} {c.symbol} {c.name[:14]:<16} 데이터부족")
            continue
        # 5/17 종가 기준 일봉 윈도우 (마지막 봉 = 5/18 → 그 전까지 슬라이스)
        last_date = daily[-1].timestamp.date()
        # 신호일 = 5/17 (마지막 봉 직전), 진입일 = 5/18
        if last_date.isoformat() == "2026-05-18":
            window = daily[:-1]  # 5/17까지
        else:
            window = daily
        ctx = AnalysisContext(
            symbol=c.symbol, name=c.name, candles=window, market_type=MarketType.STOCK,
        )
        results = {}
        for sid, strat in zip(STRATEGIES, strat_objs):
            try:
                sig = strat.analyze(ctx)
                results[sid] = "✓" if sig else "·"
            except Exception:
                results[sid] = "ERR"
        flagged = "★" if "✓" in results.values() else ""
        print(
            f"  {i:<2} {c.symbol} {c.name[:14]:<16} {c.flu_rate:>+6.2f}% "
            f"{results.get('f_zone',' '):<8} {results.get('sf_zone',' '):<8} "
            f"{results.get('gold_zone',' '):<8} {results.get('swing_38',' '):<8}  {flagged}"
        )

    # ─── Part 2. 080220 제주반도체 5/18 1분봉 단계별 ──────────────────────────
    print()
    print("=" * 110)
    print("Part 2. 080220 제주반도체 5/18 1분봉 단계별 추적")
    print("=" * 110)
    minutes = await fetcher.fetch_minute_history(symbol="080220", target_business_days=2)
    day_min = [m for m in minutes if m.timestamp.date().isoformat() == "2026-05-18"]
    if not day_min:
        print("  5/18 1분봉 없음")
        return
    daily = candles_by_sym["080220"]
    window = daily[:-1] if daily[-1].timestamp.date().isoformat() == "2026-05-18" else daily

    # 진입가 = 5/18 첫 1분봉 시가
    entry_bar = day_min[0]
    entry_price = Decimal(str(entry_bar.open))
    plan = _exit_plan_for_strategy("swing_38", entry_price, window, f_zone_atr=False)
    pos = PositionState(
        symbol="080220", entry_price=entry_price, qty=Decimal("100"),
        initial_qty=Decimal("100"), entry_time=entry_bar.timestamp,
    )

    print(f"\n[진입] {entry_bar.timestamp.strftime('%Y-%m-%d %H:%M')} "
          f"@ {float(entry_price):>8,.0f}원 (시가)")
    print(f"  swing_38 신호 — 일봉 윈도우 마지막 {window[-1].timestamp.date()} 종가 {window[-1].close:,.0f}")
    print(f"  ExitPlan: TP {[float(t.price) for t in plan.take_profits]}, "
          f"SL={plan.stop_loss.fixed_pct}, trail_stages={'ON' if plan.trail_stages else 'OFF'}")

    print(f"\n[1분봉 단계별 추적] (peak·trail·발동 시점)")
    print(f"  {'시각':<8} {'O':>8} {'H':>8} {'L':>8} {'C':>8} "
          f"{'peak':>8} {'gain%':>7} {'trail_sl':>9} {'event':<14}")
    print("  " + "-" * 100)

    last_peak_print = None
    fire_count = 0
    for m in day_min:
        if m.timestamp <= entry_bar.timestamp:
            # 진입봉도 한 줄 출력
            print(
                f"  {m.timestamp.strftime('%H:%M'):<8} {m.open:>8,.0f} {m.high:>8,.0f} "
                f"{m.low:>8,.0f} {m.close:>8,.0f} "
                f"{'-':>8} {'-':>7} {'-':>9} ENTRY"
            )
            continue
        new_pos, orders = sim_helper._evaluate_intrabar(pos, plan, m)
        peak = new_pos.high_water_mark or entry_price
        gain_pct = (peak - entry_price) / entry_price * 100
        trail_sl = plan.trail_sl_for_peak(entry_price, peak)
        # peak 갱신 시각만 출력 (verbose 줄임)
        evt = ""
        if orders:
            for eo in orders:
                evt = f"{eo.reason.value}@{int(eo.target_price):,}"
                fire_count += 1
        should_print = (
            evt or
            (last_peak_print is None) or
            (peak > last_peak_print) or
            (m.timestamp.minute % 30 == 0)  # 매 30분
        )
        if should_print:
            print(
                f"  {m.timestamp.strftime('%H:%M'):<8} {m.open:>8,.0f} {m.high:>8,.0f} "
                f"{m.low:>8,.0f} {m.close:>8,.0f} "
                f"{float(peak):>8,.0f} {float(gain_pct):>+6.1f}% "
                f"{(f'{float(trail_sl):,.0f}' if trail_sl else '-'):>9} {evt:<14}"
            )
            last_peak_print = peak
        pos = new_pos
        if orders:
            break

    if fire_count == 0:
        print("\n  → 청산 발동 없음 (15:30 day_end 또는 14:50 time_exit)")

    print()
    print("=" * 110)
    print("Part 3. 080220 단계별 의미 분석")
    print("=" * 110)
    print(
        "1. 진입 — 5/17 종가 swing_38 신호 발생 → 5/18 09:00 시가 진입\n"
        "2. 1분봉마다 peak 갱신, peak 대비 trail_sl 계산 (5단계 ai-trade 패턴)\n"
        "   - +1.5% → trail -2.5%, +2% → -2.0%, +3% → -1.5%, +4% → -1.2%, +5% → -1.0%\n"
        "3. 청산 — 1분봉 low ≤ trail_sl 도달 시 즉시 발동\n"
        "4. ExitPolicy(운영 daemon) 와 차이:\n"
        "   - 운영: trailing_start_pct 3% / trailing_offset_pct 1.5% (단일 단계)\n"
        "   - 시뮬: 5단계 (peak +1.5% 부터 trail 활성)\n"
        "   → 시뮬이 더 일찍 trail 활성화. 운영보다 빠르게 청산 가능"
    )


if __name__ == "__main__":
    asyncio.run(main())
