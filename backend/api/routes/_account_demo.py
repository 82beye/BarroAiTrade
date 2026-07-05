"""계좌 화면 데모(샘플) 데이터.

`BARRO_ACCOUNT_DEMO=1` 일 때만 계좌 API 가 실계좌 조회 대신 이 샘플을 반환한다
(기본 OFF). 키움 키가 없는 개발/데모 환경에서 계좌 화면을 채워 보기 위한 용도로,
읽기 전용이며 주문·체결과 무관하다. 실데이터가 아님을 명확히 하기 위해 응답에
`demo: True` 를 부착한다.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

_KST = timezone(timedelta(hours=9))


def account_demo_on() -> bool:
    """BARRO_ACCOUNT_DEMO 플래그(기본 OFF)."""
    return os.environ.get("BARRO_ACCOUNT_DEMO", "0").strip().lower() in ("1", "true", "on")


# ── 샘플 보유 종목 (내부 정합: eval = qty*cur, pnl = eval - qty*avg) ──
_HOLDINGS = [
    # symbol, name, qty, avg, cur, strategy, tranche
    ("005930", "삼성전자", 40, 82000, 90300, "supertrend", "2/3"),
    ("000660", "SK하이닉스", 12, 250000, 288500, "f_zone", "1/2"),
    ("010120", "LS ELECTRIC", 20, 215000, 242000, "gold_zone", "1/1"),
    ("002990", "금호건설", 300, 3800, 4410, "swing_38", "2/3"),
    ("002780", "진흥기업", 5000, 950, 892, "sf_zone", "3/3"),
]

_AVAILABLE_CASH = 18_500_000.0


def _holding_rows():
    for symbol, name, qty, avg, cur, strat, tranche in _HOLDINGS:
        eval_amt = float(qty * cur)
        cost = float(qty * avg)
        pnl = eval_amt - cost
        pnl_rate = round(pnl / cost * 100, 2) if cost else 0.0
        yield {
            "symbol": symbol, "name": name, "qty": qty, "avg": float(avg),
            "cur": float(cur), "eval": eval_amt, "cost": cost,
            "pnl": pnl, "pnl_rate": pnl_rate, "strategy": strat, "tranche": tranche,
        }


def sample_balance() -> dict:
    rows = list(_holding_rows())
    eval_total = sum(r["eval"] for r in rows)
    cost_total = sum(r["cost"] for r in rows)
    pnl_total = eval_total - cost_total
    return {
        "total_value": round(_AVAILABLE_CASH + eval_total, 0),
        "available_cash": _AVAILABLE_CASH,
        "invested_value": cost_total,
        "eval_value": eval_total,
        "total_pnl": pnl_total,
        "total_pnl_pct": round(pnl_total / cost_total * 100, 2) if cost_total else 0.0,
        "holdings": [
            {
                "symbol": r["symbol"], "name": r["name"], "qty": r["qty"],
                "avg_buy_price": r["avg"], "cur_price": r["cur"],
                "eval_amount": r["eval"], "pnl": r["pnl"], "pnl_rate": r["pnl_rate"],
            }
            for r in rows
        ],
        "position_count": len(rows),
        "timestamp": datetime.now(_KST).isoformat(),
        "demo": True,
    }


def sample_positions() -> dict:
    rows = list(_holding_rows())
    return {
        "positions": [
            {
                "symbol": r["symbol"], "name": r["name"], "quantity": r["qty"],
                "avg_price": r["avg"], "cur_price": r["cur"], "pnl_rate": r["pnl_rate"],
                "strategy": r["strategy"], "tranche": r["tranche"],
            }
            for r in rows
        ],
        "count": len(rows),
        "status": "ok",
        "demo": True,
    }


# 최근 거래일 실현손익 샘플 (순손익 혼합)
_PNL_SEQ = [320000, -145000, 610000, 88000, -230000, 415000, 175000, -60000, 520000, 240000]


def sample_realized_pnl(days: int = 30) -> dict:
    points = []
    today = date.today()
    seq = _PNL_SEQ[-days:] if days < len(_PNL_SEQ) else _PNL_SEQ
    n = len(seq)
    for i, net in enumerate(seq):
        d = today - timedelta(days=(n - i) * 3)  # 대략 3거래일 간격
        commission = round(abs(net) * 0.015, 0)
        tax = round(abs(net) * 0.02, 0) if net > 0 else 0.0
        points.append({
            "date": d.isoformat(),
            "pnl": float(net + commission + tax),
            "commission": commission,
            "tax": tax,
            "net_pnl": float(net),
        })
    return {
        "days": days,
        "points": points,
        "summary": {
            "total_pnl": sum(p["net_pnl"] for p in points),
            "total_commission": sum(p["commission"] for p in points),
            "total_tax": sum(p["tax"] for p in points),
            "trading_days": len(points),
        },
        "demo": True,
    }


def sample_balance_history(days: int = 30) -> dict:
    """자산 추이(cash+eval) 완만한 상승 곡선 샘플."""
    rows = list(_holding_rows())
    eval_total = sum(r["eval"] for r in rows)
    end_total = _AVAILABLE_CASH + eval_total
    start_total = end_total - 2_050_000  # 기간 시작 대비 소폭 상승
    today = date.today()
    points = []
    for i in range(days + 1):
        frac = i / days if days else 1.0
        # 완만한 우상향 + 소폭 굴곡
        wobble = 180_000 * ((i % 5) - 2) / 2.0
        total = start_total + (end_total - start_total) * frac + wobble
        d = today - timedelta(days=(days - i))
        eval_v = total - _AVAILABLE_CASH
        points.append({
            "date": d.isoformat(),
            "cash": _AVAILABLE_CASH,
            "eval_total": round(eval_v, 0),
            "total": round(total, 0),
            "position_count": len(rows),
        })
    return {"points": points, "days": days, "demo": True}
