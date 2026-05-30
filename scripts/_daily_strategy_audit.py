"""일일 전략 audit — 고도화 Phase 0 ①평가 인프라 복원 + ②진입 정합성 가시화.

배경(docs/02-design/features/2026-05-30-strategy-uplift.design.md):
  - barro_trade.db trades 테이블 0행 + order_audit 전량 MKT → 모니터 KPI 항상 null.
  - 본 도구는 order_audit.csv(strategy_id 귀속) + 1분봉 체결가 추정으로
    전략별 실현손익/승률(§A)과 진입 고점매수율·sim-live 괴리(§B)를 산출한다.
  - 동작 미변경(분석 전용). 데몬/주문 hot-path 무관.

사용:
  venv/bin/python scripts/_daily_strategy_audit.py --date 2026-05-29 [--save]

⚠️ 체결가는 시장가(MKT) 실체결 부재로 1분봉 종가 근사(±오차). 방향·전략귀속·상대비교는 신뢰.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

KST = timezone(timedelta(hours=9))
COMMISSION_RATE = 0.00015   # 매수·매도 각 0.015%
TAX_RATE = 0.0018           # 매도 0.18%

# §6.3 알람 임계 (docs/04-report/features/2026-05-28-daytrading-strategies-analysis.md)
ALARM_GOLD_TRADES = 20
ALARM_GOLD_WINRATE = 0.30
ALARM_STRATEGY_CAPITAL_LOSS_PCT = -3.0


# ─── 순수 로직 (테스트 대상, I/O 없음) ──────────────────────────────────

def fifo_roundtrip_pnl(
    orders: list[dict],
    commission_rate: float = COMMISSION_RATE,
    tax_rate: float = TAX_RATE,
) -> dict:
    """시간순 주문(priced)을 FIFO 매칭해 실현손익 산출.

    orders: [{"side": "buy"|"sell", "qty": float, "price": float}] 시간 오름차순.
    반환: realized(순손익, 비용차감), buy_value, sell_value, matched_basis,
          n_buys, n_sells, wins(매도건 중 이익), sells(매도건수).
    """
    lots: list[list[float]] = []  # [[qty, price], ...] FIFO
    realized = 0.0
    buy_value = 0.0
    sell_value = 0.0
    matched_basis = 0.0
    n_buys = n_sells = wins = 0
    for o in orders:
        qty = float(o["qty"])
        px = float(o["price"])
        if qty <= 0 or px <= 0:
            continue
        if o["side"] == "buy":
            lots.append([qty, px])
            buy_value += qty * px
            n_buys += 1
        else:  # sell
            sval = qty * px
            sell_value += sval
            n_sells += 1
            rem = qty
            cost = 0.0
            while rem > 1e-9 and lots:
                lq, lp = lots[0]
                m = min(rem, lq)
                cost += m * lp
                rem -= m
                lq -= m
                if lq <= 1e-9:
                    lots.pop(0)
                else:
                    lots[0][0] = lq
            gross = sval - cost
            fees = (sval + cost) * commission_rate + sval * tax_rate
            net = gross - fees
            realized += net
            matched_basis += cost
            if net > 0:
                wins += 1
    return {
        "realized": realized, "buy_value": buy_value, "sell_value": sell_value,
        "matched_basis": matched_basis, "n_buys": n_buys, "n_sells": n_sells,
        "wins": wins, "sells": n_sells,
    }


def entry_position_pct(price: float, day_low: float, day_high: float) -> Optional[float]:
    """진입가의 일중 위치 (0=일중저점, 100=일중고점). 범위 0이면 None."""
    rng = day_high - day_low
    if rng <= 0:
        return None
    return max(0.0, min(100.0, (price - day_low) / rng * 100.0))


def strategy_alarms(per_strategy: dict) -> list[str]:
    """전략별 집계 dict → 알람 메시지 목록 (§6.3)."""
    out = []
    for s, d in per_strategy.items():
        sells = d.get("sells", 0)
        wr = (d["wins"] / sells) if sells else 0.0
        if s == "gold_zone" and sells >= ALARM_GOLD_TRADES and wr < ALARM_GOLD_WINRATE:
            out.append(f"⚠️ gold_zone 일 trade {sells}>{ALARM_GOLD_TRADES} & 승률 {wr*100:.0f}%<30% → 비활성 검토")
        basis = d.get("matched_basis", 0.0)
        if basis > 0:
            cap_pct = d["realized"] / basis * 100
            if cap_pct <= ALARM_STRATEGY_CAPITAL_LOSS_PCT:
                out.append(f"⚠️ {s} 자본가중 손익 {cap_pct:.1f}% ≤ -3% → 사후분석 권고")
    return out


# ─── I/O (1분봉 fetch / order_audit / simulation_log) ─────────────────

def load_orders(date_str: str) -> list[dict]:
    out = []
    path = _REPO / "data" / "order_audit.csv"
    for r in csv.DictReader(open(path)):
        if not r["ts"].startswith(date_str) or r["action"] != "ORDERED":
            continue
        out.append({
            "ts": datetime.fromisoformat(r["ts"]),
            "side": r["side"], "symbol": r["symbol"],
            "qty": float(r["qty"]), "sid": r.get("strategy_id", "") or "",
        })
    out.sort(key=lambda o: o["ts"])
    return out


def resolve_strategy_by_symbol(orders: list[dict]) -> dict:
    """종목별 전략 결정 — 비어있지 않은 strategy_id 우선(다수결)."""
    by_sym: dict[str, dict] = {}
    for o in orders:
        if o["sid"]:
            by_sym.setdefault(o["symbol"], {}).setdefault(o["sid"], 0)
            by_sym[o["symbol"]][o["sid"]] += 1
    return {s: max(c, key=c.get) for s, c in by_sym.items()}


def load_sim_predictions(date_str: str) -> dict:
    """simulation_log.csv 아침 예측 — (symbol, strategy) → pnl."""
    pred = {}
    path = _REPO / "data" / "simulation_log.csv"
    if not path.exists():
        return pred
    for r in csv.DictReader(open(path)):
        if not r["run_at"].startswith(date_str):
            continue
        try:
            pred[(r["symbol"], r["strategy"])] = float(r["pnl"])
        except (ValueError, KeyError):
            pass
    return pred


async def fetch_1m_prices(symbols: list[str]) -> dict:
    """종목별 {분(naive KST): close} + (low,high) 당일 범위는 호출측에서 date 필터."""
    for line in open(_REPO / ".env.local"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))
    from pydantic import SecretStr
    from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
    from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
    oauth = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )
    f = KiwoomNativeCandleFetcher(oauth=oauth)
    data = {}
    for s in symbols:
        try:
            cs = await f.fetch_minute(s, tic_scope="1")
        except Exception as e:
            print(f"  fetch FAIL {s}: {e}", file=sys.stderr)
            data[s] = []
            continue
        norm = []
        for c in cs:
            t = c.timestamp
            if t.tzinfo is not None:
                t = t.astimezone(KST).replace(tzinfo=None)
            norm.append((t, c.low, c.high, c.close))
        data[s] = norm
    return data


def price_at(bars: list, ts_utc: datetime) -> Optional[float]:
    kst = ts_utc.astimezone(KST).replace(tzinfo=None, second=0, microsecond=0)
    bymin = {b[0].replace(second=0, microsecond=0): b[3] for b in bars}
    for d in range(0, 6):
        for sg in (0, -1, 1):
            t = kst + timedelta(minutes=sg * d)
            if t in bymin:
                return bymin[t]
    return None


def day_range(bars: list, date_str: str) -> tuple:
    day = [b for b in bars if b[0].strftime("%Y-%m-%d") == date_str]
    if not day:
        return (0.0, 0.0)
    return (min(b[1] for b in day), max(b[2] for b in day))


async def run(date_str: str, save: bool):
    orders = load_orders(date_str)
    if not orders:
        print(f"{date_str}: ORDERED 주문 없음")
        return
    symbols = sorted({o["symbol"] for o in orders})
    strat_by_sym = resolve_strategy_by_symbol(orders)
    sim_pred = load_sim_predictions(date_str)
    print(f"=== 1분봉 fetch ({len(symbols)}종목, 체결가 추정용) ===", file=sys.stderr)
    prices = await fetch_1m_prices(symbols)

    # §A 전략별 실현손익
    per_sym = {}
    per_strat: dict[str, dict] = {}
    for sym in symbols:
        bars = prices.get(sym, [])
        ords = [o for o in orders if o["symbol"] == sym]
        priced = []
        for o in ords:
            px = price_at(bars, o["ts"])
            if px is not None:
                priced.append({"side": o["side"], "qty": o["qty"], "price": px})
        res = fifo_roundtrip_pnl(priced)
        strat = strat_by_sym.get(sym, "unknown")
        per_sym[sym] = {**res, "strategy": strat}
        d = per_strat.setdefault(strat, {"realized": 0.0, "matched_basis": 0.0, "wins": 0, "sells": 0, "syms": 0})
        d["realized"] += res["realized"]; d["matched_basis"] += res["matched_basis"]
        d["wins"] += res["wins"]; d["sells"] += res["sells"]; d["syms"] += 1

    print(f"\n=== §A 전략별 실현손익 KPI ({date_str}) ===")
    print(f"{'전략':10} {'종목':>4} {'매도건':>5} {'승':>3} {'승률':>6} {'실현(만)':>9} {'자본가중%':>9}")
    tot = 0.0
    for s, d in sorted(per_strat.items()):
        wr = d["wins"] / d["sells"] * 100 if d["sells"] else 0
        cap = d["realized"] / d["matched_basis"] * 100 if d["matched_basis"] > 0 else 0
        print(f"{s:10} {d['syms']:>4} {d['sells']:>5} {d['wins']:>3} {wr:>5.0f}% {d['realized']/1e4:>+9.1f} {cap:>+8.2f}%")
        tot += d["realized"]
    print(f"{'합계':10} {'':>4} {'':>5} {'':>3} {'':>6} {tot/1e4:>+9.1f}")
    alarms = strategy_alarms(per_strat)
    if alarms:
        print("\n[알람]"); [print("  " + a) for a in alarms]
    else:
        print("\n[알람] 없음")

    # §B 진입 고점매수율 + sim-live 괴리
    print(f"\n=== §B 진입 품질 (고점매수율 0=저점~100=고점) ===")
    print(f"{'종목':>7} {'전략':10} {'진입가':>8} {'일중위치':>8} {'평가'}")
    strat_pos = {}
    for sym in symbols:
        bars = prices.get(sym, [])
        lo, hi = day_range(bars, date_str)
        buys = [o for o in orders if o["symbol"] == sym and o["side"] == "buy"]
        if not buys or hi <= lo:
            continue
        # 평단(추정) 진입위치
        positions = []
        for o in buys:
            px = price_at(bars, o["ts"])
            if px is not None:
                p = entry_position_pct(px, lo, hi)
                if p is not None:
                    positions.append(p)
        if not positions:
            continue
        avg_pos = sum(positions) / len(positions)
        strat = strat_by_sym.get(sym, "unknown")
        first_px = price_at(bars, buys[0]["ts"])
        flag = ""
        if strat == "gold_zone" and avg_pos >= 60:
            flag = "⚠️ gold(바닥전략) 고점권 진입"
        strat_pos.setdefault(strat, []).append(avg_pos)
        print(f"{sym:>7} {strat:10} {first_px:>8,.0f} {avg_pos:>7.0f}% {flag}")
    print("\n[전략별 평균 진입위치]")
    for s, ps in sorted(strat_pos.items()):
        ap = sum(ps) / len(ps)
        bias = " ← 고점편향" if ap >= 55 else ""
        print(f"  {s:10}: {ap:.0f}%{bias}")

    print(f"\n=== sim-live 괴리 (아침 일봉시뮬 예측 vs 실현) ===")
    for sym in symbols:
        strat = strat_by_sym.get(sym, "unknown")
        pred = sim_pred.get((sym, strat))
        real = per_sym[sym]["realized"]
        if pred is None:
            continue
        flip = "✗ 부호반전" if (pred > 0) != (real > 0) and abs(real) > 1e4 else ""
        print(f"  {sym} {strat:10} 시뮬 {pred/1e4:>+7.1f}만 → 실현 {real/1e4:>+7.1f}만  {flip}")

    if save:
        out = {"date": date_str, "per_strategy": {s: {k: v for k, v in d.items() if k != "syms"} | {"syms": d["syms"]} for s, d in per_strat.items()},
               "per_symbol": {s: {k: v for k, v in d.items()} for s, d in per_sym.items()},
               "total_realized": tot, "alarms": alarms}
        p = _REPO / "reports" / f"strategy_audit_{date_str}.json"
        p.parent.mkdir(exist_ok=True)
        json.dump(out, open(p, "w"), ensure_ascii=False, indent=2)
        print(f"\n저장: {p}")


def main():
    ap = argparse.ArgumentParser(description="일일 전략 audit (고도화 Phase 0 ①②)")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--save", action="store_true", help="reports/strategy_audit_<date>.json 저장")
    args = ap.parse_args()
    asyncio.run(run(args.date, args.save))


if __name__ == "__main__":
    main()
