"""
종가베팅(종베) 페이퍼 스캐너 — Phase D 1단계 (실주문 없음·제로 리스크).

목적: 라이브 통합/실자본 투입 전에, **실제 종베 신호를 EOD에 수집**해 종가진입 종베가
정말 엣지가 있는지 측정한다. (검증 caveat: OOS PASS는 '익일시초 진입' 변형이었고
종가진입 ablation은 브레이크이븐 → 실신호로 직접 확인이 필요.)

동작: 15:00~15:20(KST)에 주도주 선정 → 각 종목 일봉+5분봉으로 종베 분석(검증된 게이트:
money_flow ON·zone OFF·주도주컷 ON) → 신호 종목을 CSV에 기록(진입가=종가, 손절=0.618).
**주문은 전혀 내지 않는다.** 다음날 아침 결과는 별도 집계(또는 _daily_strategy_audit).

모드:
- 라이브(기본): 키움 picker/fetcher 사용 — 운영 머신에서 실행.
    python scripts/closing_bet_paper_scan.py --top 10 [--force]
- 캐시(테스트): 로컬 일봉/5분봉 캐시로 로직 검증 — 개발 머신.
    python scripts/closing_bet_paper_scan.py --from-cache --symbols 005930,000660 --force

출력: data/closing_bet_paper.csv (append).
"""
from __future__ import annotations

import argparse
import asyncio
import csv as _csv
import json
import os
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.strategy.closing_bet import (  # noqa: E402
    ClosingBetParams, ClosingBetStrategy,
)
from backend.models.market import MarketType, OHLCV  # noqa: E402
from backend.models.strategy import AnalysisContext  # noqa: E402

_KST = timezone(timedelta(hours=9))
_MAIN_DATA = Path("/Users/beye/workspace/BarroAiTrade/data")
WINDOW_START, WINDOW_END = dtime(15, 0), dtime(15, 20)

# 검증된 게이트 설정 — money_flow ON, zone OFF(악화 확인), 주도주컷 ON, 진입창은 스캐너가 관리.
# 2026-06-22 — 이격도 게이트(disparity_yellow, 5일선 +14.25%) env 토글. default OFF(현행 byte-identical).
#   사용자 dry-run 선택: BARRO_CB_DISPARITY_YELLOW=1 → ON(종베 net +0.107%→+0.405% 개선, 진입 빈도↓).
_CB_DISPARITY = os.environ.get("BARRO_CB_DISPARITY_YELLOW", "0").strip().lower() in ("1", "true", "yes", "on")
PARAMS = ClosingBetParams(
    require_eod_window=False, require_money_flow=True, require_zone=False,
    require_leader_meta=False, min_atr_pct=0.035, max_hold_days=3,
    require_disparity_yellow=_CB_DISPARITY, disparity_yellow_threshold=0.1425,
)


def _now_kst() -> datetime:
    return datetime.now(_KST)


def _load_cache(path: Path, parse_5m: bool) -> list[OHLCV]:
    if not path.exists():
        return []
    sym = path.stem
    out: list[OHLCV] = []
    for r in json.load(open(path)).get("data", []):
        try:
            ts = (datetime.strptime(str(r["datetime"]), "%Y%m%d%H%M%S") if parse_5m
                  else datetime.strptime(str(r["date"]), "%Y%m%d"))
            out.append(OHLCV(symbol=sym, timestamp=ts, open=float(r["open"]),
                             high=float(r["high"]), low=float(r["low"]), close=float(r["close"]),
                             volume=float(r["volume"]), market_type=MarketType.STOCK))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _evaluate(strat, symbol, name, daily, m5, leader_meta) -> dict | None:
    """종베 분석 → 신호면 paper 기록 dict, 아니면 None."""
    if len(daily) < PARAMS.min_candles:
        return None
    ctx = AnalysisContext(symbol=symbol, name=name, candles=daily, market_type=MarketType.STOCK,
                          intraday_candles=m5 or None, theme_context=leader_meta)
    sig = strat._analyze_v2(ctx)
    if sig is None:
        return None
    return {
        "ts": _now_kst().isoformat(timespec="seconds"), "symbol": symbol, "name": name,
        "entry_close": sig.price, "score": sig.score, "flow_grade": sig.metadata.get("flow_grade"),
        "stop_fib_price": sig.metadata.get("stop_fib_price"), "reason": sig.reason,
    }


def _write(rows: list[dict], out: Path) -> None:
    if not rows:
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    new = not out.exists()
    with out.open("a", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if new:
            w.writeheader()
        w.writerows(rows)


def run_from_cache(symbols: list[str], out: Path) -> int:
    strat = ClosingBetStrategy(PARAMS)
    rows = []
    for sym in symbols:
        daily = _load_cache(_MAIN_DATA / "ohlcv_cache" / f"{sym}.json", parse_5m=False)
        m5_all = _load_cache(_MAIN_DATA / "ohlcv_cache_5m" / f"{sym}.json", parse_5m=True)
        if not daily:
            print(f"  {sym}: 일봉 캐시 없음 — skip")
            continue
        last_day = daily[-1].timestamp.date()
        m5 = [c for c in m5_all if c.timestamp.date() == last_day]
        # 캐시 모드 leader 메타(근사): 거래대금=종가×거래량, rank=1, 신고가는 전략이 일봉으로 판정.
        meta = {"rank_trade_value": 1, "trade_value": daily[-1].close * daily[-1].volume}
        r = _evaluate(strat, sym, sym, daily, m5, meta)
        tag = "SIGNAL" if r else "no-signal"
        print(f"  {sym}: {tag}" + (f" score={r['score']} flow={r['flow_grade']}" if r else ""))
        if r:
            rows.append(r)
    _write(rows, out)
    print(f"\n종베 신호 {len(rows)}건 → {out}")
    return len(rows)


async def run_live(top_n: int, out: Path) -> int:
    # 운영 머신 전용 — 키움 인증 필요. lazy import(개발 머신 import 실패 회피).
    from backend.core.auth.kiwoom_native_oauth import KiwoomNativeOAuth  # type: ignore
    from backend.core.gateway.kiwoom_native_rank import KiwoomNativeLeaderPicker
    from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher

    oauth = KiwoomNativeOAuth.from_env()
    picker = KiwoomNativeLeaderPicker(oauth=oauth, min_flu_rate=1.0)
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth)
    strat = ClosingBetStrategy(PARAMS)

    leaders = await picker.pick(top_n=top_n)
    rows = []
    for lc in leaders:
        try:
            daily = await fetcher.fetch_daily(symbol=lc.symbol)
            m5 = await fetcher.fetch_minute(symbol=lc.symbol, tic_scope="5")
        except Exception as exc:  # noqa: BLE001
            print(f"  {lc.symbol}: fetch 실패 {exc}")
            continue
        # LeaderCandidate 엔 거래량 필드가 없어 trade_value 절대액은 None(선정컷 미사용).
        meta = {"rank_trade_value": lc.rank_trade_value, "trade_value": lc.trade_value}
        r = _evaluate(strat, lc.symbol, lc.name, daily, m5, meta)
        if r:
            r["rank_trade_value"] = lc.rank_trade_value
            rows.append(r)
            print(f"  [SIGNAL] {lc.symbol} {lc.name} score={r['score']} flow={r['flow_grade']}")
    _write(rows, out)
    print(f"\n종베 페이퍼 신호 {len(rows)}건 → {out} (실주문 없음)")
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="종베 페이퍼 스캐너 (실주문 없음)")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--from-cache", action="store_true", help="로컬 캐시 모드(테스트)")
    ap.add_argument("--symbols", default="", help="캐시 모드 종목 csv")
    ap.add_argument("--force", action="store_true", help="15:00~15:20 창 무시")
    ap.add_argument("--out", default=str(_MAIN_DATA / "closing_bet_paper.csv"))
    args = ap.parse_args()

    if not args.force and not (WINDOW_START <= _now_kst().time() <= WINDOW_END):
        print(f"진입창(15:00~15:20) 밖 — skip (현재 {_now_kst().time():%H:%M}). --force 로 무시.")
        return

    out = Path(args.out)
    if args.from_cache:
        syms = [s.strip() for s in args.symbols.split(",") if s.strip()]
        run_from_cache(syms, out)
    else:
        asyncio.run(run_live(args.top, out))


if __name__ == "__main__":
    main()
