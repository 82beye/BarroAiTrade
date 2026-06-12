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
# [BAR-OPS-39] 브로커 실측 비용(trading_costs 단일 진실원천) — 추정 경로 전용.
#   fill_audit.csv(ka10073 실측)가 있으면 그것이 우선(--source auto).
from backend.core.trading_costs import COMMISSION_RATE as _CR39, TAX_RATE_SELL as _TR39
COMMISSION_RATE = float(_CR39)   # 매수·매도 각 (실측 0.175%)
TAX_RATE = float(_TR39)          # 매도 (실측 0.20%)

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
    # [BAR-OPS-38 P2] 원가 미매칭(이월 청산) 분리 — 종전엔 lots 소진 후 잔여 매도분의
    #   cost 가 0 으로 잡혀 매도대금 전액이 이익으로 과대계상됐다(이월 청산일 KPI 왜곡,
    #   2026-06-10 매매복기 P2 — 6/10 아침 이월 7건이 전부 해당). 당일 원가가 있는
    #   매칭분만 realized 에 넣고, 미매칭분은 carry_* 버킷으로 분리 보고한다.
    carry_sell_value = 0.0
    carry_sells = 0
    sell_details: list[dict] = []   # 시간대 버킷용 — {"ts", "net", "carry"}
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
            rem = qty
            cost = 0.0
            matched_qty = 0.0
            while rem > 1e-9 and lots:
                lq, lp = lots[0]
                m = min(rem, lq)
                cost += m * lp
                matched_qty += m
                rem -= m
                lq -= m
                if lq <= 1e-9:
                    lots.pop(0)
                else:
                    lots[0][0] = lq
            if rem > 1e-9:
                carry_sell_value += rem * px
                carry_sells += 1
                sell_details.append({"ts": o.get("ts"), "net": rem * px, "carry": True})
            if matched_qty <= 1e-9:
                continue  # 전량 이월 청산 — 당일 실현 KPI 에서 제외(carry 버킷 보고)
            sval_m = matched_qty * px
            n_sells += 1
            gross = sval_m - cost
            fees = (sval_m + cost) * commission_rate + sval_m * tax_rate
            net = gross - fees
            realized += net
            matched_basis += cost
            if net > 0:
                wins += 1
            sell_details.append({"ts": o.get("ts"), "net": net, "carry": False})
    return {
        "realized": realized, "buy_value": buy_value, "sell_value": sell_value,
        "matched_basis": matched_basis, "n_buys": n_buys, "n_sells": n_sells,
        "wins": wins, "sells": n_sells,
        "carry_sell_value": carry_sell_value, "carry_sells": carry_sells,
        "sell_details": sell_details,
    }


def match_fills_to_sells(fills: list, sell_orders: list) -> tuple:
    """[BAR-OPS-39] fill_audit(브로커 실측) 행을 매도주문에 매핑 — 종목별 시간순 수량합 매칭.

    6/11 매매복기 원장과 동일한 규약(독립 검증 9종목 잔여 0 확인). fills 는 파일 순서
    보존(ka10073 반환 순서 = 체결 순서), sell_orders 는 ts 오름차순 [{ts,symbol,qty,sid}].
    반환: (trips, warnings). trip = {symbol,name,qty,buy_px,sell_px,net,ret_pct,
    strategy,sell_ts,comm,tax}. 매도주문 단위 집계(체결 분할 행 합산).
    """
    from collections import defaultdict as _dd
    by_sym = _dd(list)
    for f in fills:
        by_sym[f["symbol"]].append(f)
    trips, warnings = [], []
    for sym, frows in by_sym.items():
        sym_sells = [s for s in sell_orders if s["symbol"] == sym]
        if len(sym_sells) > 1:
            # [리뷰 반영] ka10073 반환 순서 보장 없음 — 동일 종목 다중 매도주문은 수량합
            #   매칭이 교차 오염될 수 있어 명시 경고(수량 동일 시 무탐지 한계).
            warnings.append(f"{sym} 다중 매도주문 {len(sym_sells)}건 — 순서 가정 매칭(검증 권장)")
        i = 0
        for so in sym_sells:
            grp, q = [], 0.0
            while i < len(frows) and q < so["qty"]:
                grp.append(frows[i]); q += float(frows[i]["qty"]); i += 1
            if not grp:
                continue
            if abs(q - so["qty"]) > 1e-9:
                warnings.append(f"{sym} 매도주문 {so['qty']:.0f}주 ↔ fill 합 {q:.0f}주 불일치")
            tq = sum(float(g["qty"]) for g in grp)
            buy_avg = sum(float(g["qty"]) * float(g["buy_price"]) for g in grp) / tq
            sell_avg = sum(float(g["qty"]) * float(g["sell_price"]) for g in grp) / tq
            trips.append({
                "symbol": sym, "name": grp[0].get("name", ""), "qty": tq,
                "buy_px": buy_avg, "sell_px": sell_avg,
                "net": sum(float(g["pnl"]) for g in grp),
                "ret_pct": (sell_avg / buy_avg - 1) * 100 if buy_avg > 0 else 0.0,
                "strategy": so.get("sid") or "unknown", "sell_ts": so["ts"],
                "comm": sum(float(g.get("commission", 0)) for g in grp),
                "tax": sum(float(g.get("tax", 0)) for g in grp),
            })
        if i < len(frows):
            warnings.append(f"{sym} fill {len(frows)-i}행 미매칭(매도주문 부재)")
    trips.sort(key=lambda t: t["sell_ts"])
    return trips, warnings


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
    path = _REPO / "data" / "order_audit.csv"
    rows = list(csv.DictReader(open(path)))
    # [BAR-OPS-38 P2] UNFILLED 행 반영 — SYNC 가 '접수됐으나 미체결'로 판정한 매수의
    #   원 ORDERED 행을 제외(6/10 475150 상한가 잠김 49주: ORDERED 만 보면 체결로 오인).
    #   1순위 order_no 매칭, order_no 없으면 (종목, 수량) 보수 매칭.
    unfilled_nos: set[str] = set()
    unfilled_sq: set[tuple] = set()
    for r in rows:
        if r["ts"].startswith(date_str) and r.get("action") == "UNFILLED":
            ono = (r.get("order_no") or "").strip()
            if ono:
                unfilled_nos.add(ono)
            else:
                unfilled_sq.add((r["symbol"], r["qty"]))
    out = []
    for r in rows:
        if not r["ts"].startswith(date_str) or r["action"] != "ORDERED":
            continue
        ono = (r.get("order_no") or "").strip()
        if ono and ono in unfilled_nos:
            continue
        if r["side"] == "buy" and (r["symbol"], r["qty"]) in unfilled_sq:
            unfilled_sq.discard((r["symbol"], r["qty"]))  # 1회만 상쇄
            continue
        out.append({
            "ts": datetime.fromisoformat(r["ts"]),
            "side": r["side"], "symbol": r["symbol"],
            "qty": float(r["qty"]), "sid": r.get("strategy_id", "") or "",
        })
    out.sort(key=lambda o: o["ts"])
    return out


def load_fill_audit(date_str: str) -> list:
    """[BAR-OPS-39] data/fill_audit.csv(EOD ka10073 백필, 브로커 실측)에서 해당 일자 행 로드.

    컬럼: date,symbol,name,qty,buy_price,sell_price,pnl,pnl_rate,commission,tax.
    pnl 은 수수료·세금 차감 net. 파일/일자 부재 시 [] (추정 경로 폴백).
    """
    path = _REPO / "data" / "fill_audit.csv"
    if not path.exists():
        return []
    key = date_str.replace("-", "")
    out = []
    try:
        for r in csv.DictReader(open(path, encoding="utf-8")):
            if r.get("date") == key:
                out.append(r)
    except Exception:
        return []
    return out


def load_symbol_names(symbols) -> dict[str, str]:
    """[BAR-OPS-38 P2] 종목명 로컬 폴백 — 영문코드 신형우선주(0193T0 등) 포함 표시용.

    소스 우선순위: refined_signals.json → active_positions.json(+히스토리 최신분)
    → simulation_log.csv. 전부 로컬 파일(네트워크 없음). 미해결 심볼은 코드 그대로 표시.
    """
    names: dict[str, str] = {}
    want = set(symbols)

    def _take(sym: str, name: str) -> None:
        if sym in want and name and sym not in names:
            names[sym] = name

    try:
        data = json.load(open(_REPO / "data" / "refined_signals.json", encoding="utf-8"))
        for s in data.get("signals", []):
            _take(s.get("symbol", ""), s.get("name", ""))
    except Exception:
        pass
    try:
        ap = json.load(open(_REPO / "data" / "active_positions.json", encoding="utf-8"))
        for sym, p in ap.items():
            _take(sym, (p or {}).get("name", ""))
    except Exception:
        pass
    try:
        hist = sorted((_REPO / "data" / "_active_positions_history").glob("*.json"))[-200:]
        for fp in reversed(hist):
            if want <= set(names):
                break
            try:
                for sym, p in json.load(open(fp, encoding="utf-8")).items():
                    _take(sym, (p or {}).get("name", ""))
            except Exception:
                continue
    except Exception:
        pass
    try:
        for r in csv.DictReader(open(_REPO / "data" / "simulation_log.csv", encoding="utf-8")):
            _take(r.get("symbol", ""), r.get("name", ""))
    except Exception:
        pass
    return names


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


def prev_day_close(bars: list, date_str: str) -> Optional[float]:
    """[BAR-OPS-39] 직전 거래일 마지막 종가 — 진입 갭(전일종가比) 측정용."""
    prior = [b for b in bars if b[0].strftime("%Y-%m-%d") < date_str]
    return float(prior[-1][3]) if prior else None


async def run(date_str: str, save: bool, source: str = "auto"):
    orders = load_orders(date_str)
    if not orders:
        print(f"{date_str}: ORDERED 주문 없음")
        return
    symbols = sorted({o["symbol"] for o in orders})
    strat_by_sym = resolve_strategy_by_symbol(orders)
    sim_pred = load_sim_predictions(date_str)
    # [BAR-OPS-39] 실측(ka10073 백필) 우선 모드 — auto: fill_audit 행이 있으면 실측,
    #   없으면 1분봉 추정 폴백. 추정은 비용·슬리피지 낙관 편향(6/11 캘리브레이션:
    #   괴리의 85%가 비용 모델)이 있어 실측이 항상 우선이다.
    fills = load_fill_audit(date_str) if source in ("auto", "fill") else []
    use_fill = bool(fills) if source == "auto" else (source == "fill")
    if source == "fill" and not fills:
        print(f"{date_str}: fill_audit 행 없음 — --source fill 실패(estimate 로 재시도 가능)")
        return
    print(f"=== 1분봉 fetch ({len(symbols)}종목, 체결가 추정용) ===", file=sys.stderr)
    prices = await fetch_1m_prices(symbols)
    names = load_symbol_names(symbols)   # [BAR-OPS-38 P2] 표시용 종목명 로컬 폴백

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
                priced.append({"side": o["side"], "qty": o["qty"], "price": px,
                               "ts": o["ts"]})  # [BAR-OPS-38 P2] 시간대 버킷용
        res = fifo_roundtrip_pnl(priced)
        strat = strat_by_sym.get(sym, "unknown")
        per_sym[sym] = {**res, "strategy": strat}
        d = per_strat.setdefault(strat, {"realized": 0.0, "matched_basis": 0.0, "wins": 0, "sells": 0, "syms": 0})
        d["realized"] += res["realized"]; d["matched_basis"] += res["matched_basis"]
        d["wins"] += res["wins"]; d["sells"] += res["sells"]; d["syms"] += 1

    est_tot = sum(d["realized"] for d in per_strat.values())

    # [BAR-OPS-39] 실측 모드 — fill_audit 트립을 매도주문에 매핑해 §A 를 실측으로 대체.
    fill_trips: list = []
    if use_fill:
        sell_orders = [o for o in orders if o["side"] == "sell"]
        fill_trips, fwarns = match_fills_to_sells(fills, sell_orders)
        for w in fwarns:
            print(f"  ⚠️ [FILL-MATCH] {w}")
        per_strat = {}
        per_sym_fill: dict = {}
        for t in fill_trips:
            d = per_strat.setdefault(t["strategy"], {"realized": 0.0, "matched_basis": 0.0,
                                                     "wins": 0, "sells": 0, "syms": 0})
            d["realized"] += t["net"]
            d["matched_basis"] += t["qty"] * t["buy_px"]
            d["sells"] += 1
            if t["net"] > 0:
                d["wins"] += 1
            ps = per_sym_fill.setdefault(t["symbol"], {"realized": 0.0, "strategy": t["strategy"]})
            ps["realized"] += t["net"]
        # 전략별 종목수 산출
        _strat_syms: dict = {}
        for t in fill_trips:
            _strat_syms.setdefault(t["strategy"], set()).add(t["symbol"])
        for s2, d in per_strat.items():
            d["syms"] = len(_strat_syms.get(s2, set()))
        # sim-live 절에서 실측 realized 사용
        for sym, ps in per_sym_fill.items():
            if sym in per_sym:
                per_sym[sym]["realized"] = ps["realized"]

    _label = "실측(ka10073 체결 백필)" if use_fill else "1분봉 종가 추정"
    print(f"\n=== §A 전략별 실현손익 KPI ({date_str}) — {_label} ===")
    print(f"{'전략':10} {'종목':>4} {'매도건':>5} {'승':>3} {'승률':>6} {'실현(만)':>9} {'자본가중%':>9}")
    tot = 0.0
    for s, d in sorted(per_strat.items()):
        wr = d["wins"] / d["sells"] * 100 if d["sells"] else 0
        cap = d["realized"] / d["matched_basis"] * 100 if d["matched_basis"] > 0 else 0
        print(f"{s:10} {d['syms']:>4} {d['sells']:>5} {d['wins']:>3} {wr:>5.0f}% {d['realized']/1e4:>+9.1f} {cap:>+8.2f}%")
        tot += d["realized"]
    print(f"{'합계':10} {'':>4} {'':>5} {'':>3} {'':>6} {tot/1e4:>+9.1f}")
    if use_fill:
        _carry_note = ""
        _cn = sum(d.get("carry_sells", 0) for d in per_sym.values())
        if _cn:
            _carry_note = f" ※이월 매도 {_cn}건 포함 — 추정은 이월 제외라 커버리지 차이 섞임"
        print(f"  [추정-실측 괴리] 1분봉 추정 {est_tot/1e4:+.1f}만 vs 실측 {tot/1e4:+.1f}만 "
              f"→ 추정 편향 {(est_tot-tot)/1e4:+.1f}만 (비용·슬리피지 근사 오차 추적용){_carry_note}")
    alarms = strategy_alarms(per_strat)
    if alarms:
        print("\n[알람]"); [print("  " + a) for a in alarms]
    else:
        print("\n[알람] 없음")

    # [BAR-OPS-38 P2] 이월 청산(원가 미매칭) 분리 보고 — 2026-06-10 매매복기 P2.
    #   전일 이전 매수분의 당일 매도는 당일 원가가 없어 §A 실현 KPI 에서 제외된다.
    #   [BAR-OPS-39] 실측 모드에선 불필요 — ka10073 이 이월분도 실제 매수원가로 반환.
    carry_v = sum(d.get("carry_sell_value", 0.0) for d in per_sym.values())
    carry_n = sum(d.get("carry_sells", 0) for d in per_sym.values())
    if carry_n and not use_fill:
        print(f"\n[이월 청산] {carry_n}건, 매도대금 {carry_v/1e4:,.1f}만원 — 전일 이전 매수분(당일 원가 부재)")
        print("  → §A 실현에서 제외됨. 정확 손익은 data/fill_audit.csv(ka10073 체결 백필)/매매복기 원장 참조.")
        for sym, d in sorted(per_sym.items()):
            if d.get("carry_sells"):
                print(f"    {sym} {names.get(sym, ''):<10.10} ({d.get('strategy', '?')}): {d['carry_sells']}건 {d['carry_sell_value']/1e4:,.1f}만")

    # [BAR-OPS-38 P2] 시간대별 실현 버킷 (KST) — 09시 집중 손실 패턴 추적.
    #   [BAR-OPS-39] 실측 모드는 fill 트립(매도주문 시각) 기준.
    from collections import defaultdict as _dd
    buckets: dict = _dd(lambda: [0.0, 0])
    if use_fill:
        for t in fill_trips:
            hh = t["sell_ts"].astimezone(KST).strftime("%H")
            buckets[hh][0] += t["net"]
            buckets[hh][1] += 1
    else:
        for d in per_sym.values():
            for sd in d.get("sell_details", []):
                if sd.get("carry") or sd.get("ts") is None:
                    continue
                hh = sd["ts"].astimezone(KST).strftime("%H")
                buckets[hh][0] += sd["net"]
                buckets[hh][1] += 1
    if buckets:
        print("\n[시간대별 실현 (KST)]")
        for hh in sorted(buckets):
            v, n = buckets[hh]
            print(f"  {hh}시: {n}건 {v/1e4:+9.1f}만")

    # §B 진입 고점매수율 + sim-live 괴리
    print(f"\n=== §B 진입 품질 (고점매수율 0=저점~100=고점, 갭=전일종가比) ===")
    print(f"{'종목':>7} {'종목명':<10} {'전략':10} {'진입가':>8} {'갭%':>6} {'일중위치':>8} {'평가'}")
    strat_pos = {}
    gap_records = []   # [BAR-OPS-39] 갭 가드 임계 측정용 (전략, 갭, 실현)
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
        # [BAR-OPS-39] 진입 갭(전일종가比) — 갭 가드 임계(BARRO_ZONE_MAX_FLU) 데이터 기반
        #   조정용. 6/11: 13.1~13.5% 진입 3건 전패(-353K)가 임계 15% 바로 아래로 통과.
        pdc = prev_day_close(bars, date_str)
        gap_pct = (first_px / pdc - 1) * 100 if (pdc and first_px) else None
        gap_s = f"{gap_pct:+5.1f}" if gap_pct is not None else "    ?"
        if gap_pct is not None:
            gap_records.append({"symbol": sym, "strategy": strat, "gap_pct": round(gap_pct, 2),
                                "realized": per_sym.get(sym, {}).get("realized", 0.0)})
        flag = ""
        if strat == "gold_zone" and avg_pos >= 60:
            flag = "⚠️ gold(바닥전략) 고점권 진입"
        if gap_pct is not None and gap_pct >= 10 and strat in ("gold_zone", "f_zone"):
            flag += " ⚠️ 갭상승 10%+ 진입"
        strat_pos.setdefault(strat, []).append(avg_pos)
        print(f"{sym:>7} {names.get(sym, ''):<10.10} {strat:10} {first_px:>8,.0f} {gap_s} {avg_pos:>7.0f}% {flag}")
    print("\n[전략별 평균 진입위치]")
    for s, ps in sorted(strat_pos.items()):
        ap = sum(ps) / len(ps)
        bias = " ← 고점편향" if ap >= 55 else ""
        print(f"  {s:10}: {ap:.0f}%{bias}")

    # [BAR-OPS-39] §C 청산 품질 (실측 모드 전용) — 손절 슬립 + 익절 후 잔여 상승(run-up).
    #   슬립: 의도 손절선(전략 프로파일 stop_loss_pct) 대비 실제 체결 수익률의 초과 하락
    #         (6/11: gold -4% 기준 대비 -4.3~-6.2% 체결, 평균 -1.3%p — 평가주기+시장가 비용).
    #   run-up: 매도 후 당일 고가까지의 미실현 상승분 — 조기 익절 비용(러너 shadow 측정).
    if use_fill and fill_trips:
        from backend.core.risk.holding_evaluator import STRATEGY_EXIT_PROFILES
        print(f"\n=== §C 청산 품질 (실측) — 손절 슬립 / 익절 후 run-up ===")
        slip_rows, runup_rows = [], []
        for t in fill_trips:
            ret = t["ret_pct"]
            strat_key = (t["strategy"] or "").replace("_v1", "").replace("_v2", "")
            if strat_key.startswith("supertrend"):
                # [리뷰 반영] st 는 EXIT_PROFILES 에 없음 — 의도 손절선 = 하드손절(운영 -6%)
                sl = float(os.environ.get("SUPERTREND_AUTO_HARD_STOP", "-6.0"))
            else:
                sl = float(STRATEGY_EXIT_PROFILES.get(strat_key, {}).get("stop_loss_pct", -4.0))
            if ret <= sl + 0.5:   # 손절성 청산(기준 부근 이하)
                slip_rows.append((t, sl, ret - sl))
            bars = prices.get(t["symbol"], [])
            sell_kst = t["sell_ts"].astimezone(KST).replace(tzinfo=None)
            post = [b for b in bars
                    if b[0].strftime("%Y-%m-%d") == date_str and b[0] >= sell_kst]
            if post and t["sell_px"] > 0:
                post_high = max(b[2] for b in post)
                runup = (post_high / t["sell_px"] - 1) * 100
                runup_rows.append((t, runup))
        if slip_rows:
            print("[손절 슬립] (체결 수익률 − 의도 손절선, 음수=초과 하락)")
            for t, sl, slip in slip_rows:
                print(f"  {t['symbol']} {names.get(t['symbol'], ''):<10.10} {t['strategy']:<11} "
                      f"기준 {sl:+.1f}% → 체결 {t['ret_pct']:+.2f}% (슬립 {slip:+.2f}%p)")
            avg_slip = sum(s for _, _, s in slip_rows) / len(slip_rows)
            print(f"  평균 슬립: {avg_slip:+.2f}%p ({len(slip_rows)}건)")
        wins_ru = [(t, r) for t, r in runup_rows if t["net"] > 0]
        if wins_ru:
            print("[익절 후 run-up] (매도가 → 이후 당일 고가, 러너 조기하차 비용)")
            for t, r in sorted(wins_ru, key=lambda x: -x[1]):
                lost = t["qty"] * t["sell_px"] * r / 100
                print(f"  {t['symbol']} {names.get(t['symbol'], ''):<10.10} {t['strategy']:<11} "
                      f"+{t['ret_pct']:.2f}% 익절 → 이후 고가 +{r:.2f}% (미실현 ≈{lost/1e4:,.1f}만)")
            tot_lost = sum(t["qty"] * t["sell_px"] * r / 100 for t, r in wins_ru)
            print(f"  합계 미실현 run-up: ≈{tot_lost/1e4:,.1f}만 ({len(wins_ru)}건)")

    print(f"\n=== sim-live 괴리 (아침 일봉시뮬 예측 vs 실현) ===")
    for sym in symbols:
        strat = strat_by_sym.get(sym, "unknown")
        pred = sim_pred.get((sym, strat))
        real = per_sym[sym]["realized"]
        if pred is None:
            continue
        flip = "✗ 부호반전" if (pred > 0) != (real > 0) and abs(real) > 1e4 else ""
        print(f"  {sym} {names.get(sym, ''):<10.10} {strat:10} 시뮬 {pred/1e4:>+7.1f}만 → 실현 {real/1e4:>+7.1f}만  {flip}")

    if save:
        out = {"date": date_str, "source": _label,
               "per_strategy": {s: {k: v for k, v in d.items() if k != "syms"} | {"syms": d["syms"]} for s, d in per_strat.items()},
               # sell_details 는 datetime 포함(시간대 버킷 내부용) — JSON 직렬화 제외
               "per_symbol": {s: {k: v for k, v in d.items() if k != "sell_details"} for s, d in per_sym.items()},
               "carry": {"sells": carry_n, "sell_value": carry_v},
               # [BAR-OPS-39] 갭 가드 임계 측정 누적 데이터 + 추정-실측 괴리
               "gap_records": gap_records,
               "estimate_total": est_tot,
               "total_realized": tot, "alarms": alarms}
        p = _REPO / "reports" / f"strategy_audit_{date_str}.json"
        p.parent.mkdir(exist_ok=True)
        json.dump(out, open(p, "w"), ensure_ascii=False, indent=2)
        print(f"\n저장: {p}")


def main():
    ap = argparse.ArgumentParser(description="일일 전략 audit (고도화 Phase 0 ①②)")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--save", action="store_true", help="reports/strategy_audit_<date>.json 저장")
    ap.add_argument("--source", choices=["auto", "fill", "estimate"], default="auto",
                    help="[BAR-OPS-39] auto=fill_audit 있으면 실측, fill=실측 강제, estimate=1분봉 추정")
    args = ap.parse_args()
    asyncio.run(run(args.date, args.save, args.source))


if __name__ == "__main__":
    main()
