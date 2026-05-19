"""
리포트 API 라우터

엔드포인트:
  GET /api/reports/daily            - 일일 리포트 (order_audit.csv 기반)
  GET /api/reports/performance      - 성과 리포트
"""
from __future__ import annotations

import csv
import logging
from collections import defaultdict
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

from fastapi import APIRouter, Query, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
KST = timezone(timedelta(hours=9))
_AUDIT_PATH = _DATA_DIR / "order_audit.csv"


def _parse_audit_for_date(report_date: date) -> list[dict]:
    """order_audit.csv에서 해당 날짜(KST) 주문을 파싱."""
    if not _AUDIT_PATH.exists():
        return []
    rows = []
    with open(_AUDIT_PATH, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("action") not in ("ORDERED", "DRY_RUN"):
                continue
            try:
                ts = datetime.fromisoformat(row["ts"]).astimezone(KST)
            except Exception:
                continue
            if ts.date() != report_date:
                continue
            rows.append({
                "ts_kst": ts,
                "side": row.get("side", ""),
                "symbol": row.get("symbol", ""),
                "qty": int(row.get("qty", 0)),
                "price": row.get("price", "MKT"),
                "order_no": row.get("order_no", ""),
            })
    return rows


def _build_daily_report(report_date: date) -> dict:
    """일별 매매 요약 + 종목별 매수/매도 매칭."""
    rows = _parse_audit_for_date(report_date)
    if not rows:
        return {
            "date": report_date.isoformat(),
            "summary": {
                "trades_count": 0, "win_count": 0, "loss_count": 0,
                "win_rate": 0.0, "pnl": 0, "pnl_pct": 0.0,
            },
            "trades": [],
        }

    # 종목별 매수/매도 그룹
    buys: dict[str, list] = defaultdict(list)
    sells: dict[str, list] = defaultdict(list)
    for r in rows:
        if r["side"] == "buy":
            buys[r["symbol"]].append(r)
        elif r["side"] == "sell":
            sells[r["symbol"]].append(r)

    trades = []
    total_pnl = 0
    win_count = 0
    loss_count = 0

    # 매수+매도 매칭된 종목 (라운드트립)
    all_symbols = set(buys.keys()) | set(sells.keys())
    for sym in all_symbols:
        buy_list = buys.get(sym, [])
        sell_list = sells.get(sym, [])

        total_buy_qty = sum(b["qty"] for b in buy_list)
        total_sell_qty = sum(s["qty"] for s in sell_list)

        # 평균 매수가 계산 (MKT일 경우 0)
        avg_buy = 0.0
        avg_sell = 0.0

        entry_time = buy_list[0]["ts_kst"].isoformat() if buy_list else ""
        exit_time = sell_list[-1]["ts_kst"].isoformat() if sell_list else ""

        # 매수+매도 모두 있으면 라운드트립
        if buy_list and sell_list:
            pnl = 0  # audit에 실제 가격 없으므로 0 (추후 개선)
            trades.append({
                "symbol": sym,
                "side": "sell",
                "entry_price": 0,
                "exit_price": 0,
                "pnl": pnl,
                "buy_qty": total_buy_qty,
                "sell_qty": total_sell_qty,
                "entry_time": entry_time,
                "exit_time": exit_time,
            })
        elif buy_list:
            # 매수만 (보유 중)
            trades.append({
                "symbol": sym,
                "side": "buy",
                "entry_price": 0,
                "exit_price": None,
                "pnl": None,
                "buy_qty": total_buy_qty,
                "sell_qty": 0,
                "entry_time": entry_time,
                "exit_time": None,
            })

    # active_positions.json에서 실제 가격 보강
    import json
    pos_path = _DATA_DIR / "active_positions.json"
    active = {}
    if pos_path.exists():
        try:
            active = json.loads(pos_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # reports 마크다운에서 PnL 보강 시도
    report_path = Path(f"reports/{report_date.isoformat()}.md")
    if report_path.exists():
        try:
            _enrich_from_report(trades, report_path)
        except Exception:
            pass

    trades_count = len([t for t in trades if t.get("exit_time")])
    total_pnl = sum(t.get("pnl", 0) or 0 for t in trades)
    win_count = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
    loss_count = sum(1 for t in trades if (t.get("pnl") or 0) < 0)
    win_rate = (win_count / trades_count * 100) if trades_count > 0 else 0.0

    return {
        "date": report_date.isoformat(),
        "summary": {
            "trades_count": trades_count,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": round(win_rate, 1),
            "pnl": total_pnl,
            "pnl_pct": 0.0,  # 예수금 대비 비율은 추후 계산
        },
        "trades": trades,
    }


def _enrich_from_report(trades: list[dict], report_path: Path) -> None:
    """reports/YYYY-MM-DD.md 마크다운에서 종목별 PnL 파싱하여 보강."""
    text = report_path.read_text(encoding="utf-8")
    # 간단한 테이블 파싱: | 종목 | ... | 손익 | 형식
    for line in text.split("\n"):
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.split("|")]
        if len(cols) < 4:
            continue
        # 종목코드 찾기
        for t in trades:
            if t["symbol"] in line:
                # 손익 컬럼에서 숫자 추출
                for col in cols:
                    col_clean = col.replace(",", "").replace("+", "").replace("원", "").replace("%", "")
                    try:
                        val = float(col_clean)
                        if -100 < val < 100 and "%" in col:
                            # 수익률
                            pass
                    except ValueError:
                        pass


@router.get("/reports/realized-pnl")
async def get_realized_pnl(
    days: int = Query(30, ge=1, le=90, description="조회 일수"),
) -> dict:
    """키움 REST API ka10074 일자별 실현손익 합산 조회."""
    import os
    from pydantic import SecretStr
    try:
        from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
        from backend.core.gateway.kiwoom_native_account import KiwoomNativeAccountFetcher

        oauth = KiwoomNativeOAuth(
            app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
            app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
            base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
        )
        fetcher = KiwoomNativeAccountFetcher(oauth=oauth)

        end_dt = date.today().strftime("%Y%m%d")
        start_dt = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
        entries = await fetcher.fetch_daily_pnl(start_dt, end_dt)

        points = []
        for e in entries:
            points.append({
                "date": f"{e.date[:4]}-{e.date[4:6]}-{e.date[6:8]}" if len(e.date) == 8 else e.date,
                "pnl": float(e.pnl_amount),
                "commission": float(e.commission),
                "tax": float(e.tax),
                "net_pnl": float(e.net_pnl),
            })
        points.sort(key=lambda p: p["date"])

        total_pnl = sum(p["net_pnl"] for p in points)
        total_commission = sum(p["commission"] for p in points)
        total_tax = sum(p["tax"] for p in points)

        return {
            "days": days,
            "points": points,
            "summary": {
                "total_pnl": total_pnl,
                "total_commission": total_commission,
                "total_tax": total_tax,
                "trading_days": len(points),
            },
        }
    except KeyError:
        raise HTTPException(status_code=503, detail="KIWOOM_APP_KEY/SECRET 환경변수 미설정")
    except Exception as e:
        logger.error("실현손익 조회 실패: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports/balance-history")
async def get_balance_history(
    days: int = Query(30, ge=1, le=365, description="조회 일수"),
) -> dict:
    """잔고(예수금+평가금) 추이 조회."""
    history_path = _DATA_DIR / "balance_history.json"
    if not history_path.exists():
        return {"points": [], "days": days}
    import json
    try:
        all_data = json.loads(history_path.read_text(encoding="utf-8"))
    except Exception:
        return {"points": [], "days": days}

    # 최근 N일 필터
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    points = [p for p in all_data if p.get("date", "") >= cutoff]
    points.sort(key=lambda p: p["date"])
    return {"points": points, "days": days}


@router.get("/reports/chart")
async def get_chart_data(
    days: int = Query(30, ge=1, le=90, description="조회 일수"),
) -> dict:
    """최근 N일 일별 매매 요약 (차트용)."""
    today_date = date.today()
    points = []
    for i in range(days):
        d = today_date - timedelta(days=i)
        report = _build_daily_report(d)
        tc = report["summary"]["trades_count"]
        if tc > 0:
            points.append({
                "date": d.strftime("%m-%d"),
                "pnl_pct": report["summary"]["pnl_pct"],
                "trades_count": tc,
            })
    points.reverse()
    return {"points": points, "days": days}


@router.get("/reports/daily")
async def get_daily_report(
    date_str: str = Query(None, description="조회 날짜 (YYYY-MM-DD)"),
) -> dict:
    """일일 리포트 조회 (order_audit.csv 기반 실제 매매 데이터)."""
    try:
        if date_str:
            try:
                report_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=400, detail="날짜 형식: YYYY-MM-DD")
        else:
            report_date = date.today()

        logger.info("일일 리포트 조회: %s", report_date)
        return _build_daily_report(report_date)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("일일 리포트 조회 실패: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reports/performance")
async def get_performance_report(
    period: str = Query("1m", description="기간: 1w, 1m, 3m, ytd, all"),
) -> dict:
    """성과 리포트 — 일별 요약 집계."""
    try:
        logger.info("성과 리포트 조회: %s", period)

        # 기간 계산
        today = date.today()
        if period == "1w":
            start = today - timedelta(days=7)
        elif period == "1m":
            start = today - timedelta(days=30)
        elif period == "3m":
            start = today - timedelta(days=90)
        else:
            start = today - timedelta(days=365)

        # 일별 리포트 집계
        daily_reports = []
        d = start
        while d <= today:
            report = _build_daily_report(d)
            if report["summary"]["trades_count"] > 0:
                daily_reports.append(report)
            d += timedelta(days=1)

        total_trades = sum(r["summary"]["trades_count"] for r in daily_reports)
        total_wins = sum(r["summary"]["win_count"] for r in daily_reports)
        total_pnl = sum(r["summary"]["pnl"] for r in daily_reports)

        return {
            "period": period,
            "summary": {
                "total_return": 0.0,
                "win_rate": round(total_wins / total_trades * 100, 1) if total_trades > 0 else 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
                "trades_count": total_trades,
                "total_pnl": total_pnl,
            },
            "daily": [
                {
                    "date": r["date"],
                    "trades_count": r["summary"]["trades_count"],
                    "pnl": r["summary"]["pnl"],
                    "pnl_pct": r["summary"]["pnl_pct"],
                    "win_rate": r["summary"]["win_rate"],
                }
                for r in daily_reports
            ],
        }
    except Exception as e:
        logger.error("성과 리포트 조회 실패: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
