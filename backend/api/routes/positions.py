"""
포지션 및 계좌 API 라우터

엔드포인트:
  GET /api/accounts/balance       - 잔고 조회
  GET /api/positions              - 포지션 목록 조회
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import SecretStr

from backend.core.state import app_state

logger = logging.getLogger(__name__)
router = APIRouter()


_kiwoom_oauth = None

def _build_kiwoom_fetcher():
    global _kiwoom_oauth
    from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
    from backend.core.gateway.kiwoom_native_account import KiwoomNativeAccountFetcher
    if _kiwoom_oauth is None:
        _kiwoom_oauth = KiwoomNativeOAuth(
            app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
            app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
            base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
        )
    return KiwoomNativeAccountFetcher(oauth=_kiwoom_oauth)


def _get_gateway():
    """마켓 게이트웨이 인스턴스 반환"""
    gateway = app_state.market_gateway
    if not gateway:
        raise HTTPException(
            status_code=503,
            detail="마켓 게이트웨이 미초기화"
        )
    return gateway


@router.get("/accounts/balance")
async def get_balance() -> dict:
    """계좌 잔고 조회 (키움 REST API 실시간)."""
    try:
        fetcher = _build_kiwoom_fetcher()
        balance = await fetcher.fetch_balance()
        deposit = await fetcher.fetch_deposit()

        cash = float(deposit.cash)
        eval_total = float(balance.total_eval)
        total = float(balance.estimated_deposit)

        holdings = []
        for h in (balance.holdings or []):
            holdings.append({
                "symbol": h.symbol,
                "name": h.name,
                "qty": h.qty,
                "avg_buy_price": float(h.avg_buy_price),
                "cur_price": float(h.cur_price),
                "eval_amount": float(h.eval_amount),
                "pnl": float(h.pnl),
                "pnl_rate": float(h.pnl_rate),
            })

        from datetime import datetime, timezone, timedelta
        now_kst = datetime.now(timezone(timedelta(hours=9)))

        return {
            "total_value": total,
            "available_cash": cash,
            "invested_value": float(balance.total_purchase),
            "eval_value": eval_total,
            "total_pnl": float(balance.total_pnl),
            "total_pnl_pct": float(balance.total_pnl_rate),
            "holdings": holdings,
            "position_count": len(holdings),
            "timestamp": now_kst.isoformat(),
        }
    except Exception as e:
        logger.error("잔고 조회 실패: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions")
async def get_positions(
    symbol: str = None,
) -> dict:
    """
    포지션 목록 조회

    응답:
    ```json
    {
      "positions": [
        {
          "symbol": "005930",
          "name": "삼성전자",
          "quantity": 10,
          "avg_price": 75000,
          "current_price": 75500,
          "realized_pnl": 0,
          "unrealized_pnl": 5000,
          "pnl_pct": 0.67,
          "entry_time": "2026-04-11T09:00:00Z"
        }
      ],
      "count": 1
    }
    ```
    """
    from backend.core.journal.active_positions import ActivePositionStore
    active = ActivePositionStore("data/active_positions.json").load_all()

    try:
        fetcher = _build_kiwoom_fetcher()
        balance = await fetcher.fetch_balance()
        holdings = balance.holdings or []
        if symbol:
            holdings = [h for h in holdings if h.symbol == symbol]

        positions = []
        seen_symbols = set()
        for h in holdings:
            pos_meta = active.get(h.symbol)
            seen_symbols.add(h.symbol)
            positions.append({
                "symbol": h.symbol,
                "name": getattr(h, "name", ""),
                "quantity": int(h.qty),
                "avg_price": float(getattr(h, "avg_buy_price", 0)),
                "cur_price": float(getattr(h, "cur_price", 0)),
                "pnl_rate": float(getattr(h, "pnl_rate", 0)),
                "strategy": pos_meta.strategy if pos_meta else "",
                "tranche": f"{sum(1 for t in pos_meta.tranches if t.status == 'filled')}/{len(pos_meta.tranches)}" if pos_meta else "",
            })

        # active_positions에 있지만 브로커 잔고에 없는 종목 보충
        for sym, pos in active.items():
            if sym not in seen_symbols and (not symbol or sym == symbol):
                positions.append({
                    "symbol": sym,
                    "name": pos.name,
                    "quantity": pos.total_recommended_qty,
                    "avg_price": pos.entry_price,
                    "cur_price": pos.entry_price,
                    "pnl_rate": 0.0,
                    "strategy": pos.strategy,
                    "tranche": f"{sum(1 for t in pos.tranches if t.status == 'filled')}/{len(pos.tranches)}",
                })

        logger.info("포지션 목록 조회: %d 종목", len(positions))
        return {"positions": positions, "count": len(positions), "status": "ok"}
    except Exception as e:
        logger.warning(f"브로커 잔고 조회 실패, active_positions 폴백: {e}")
        # 폴백: active_positions.json 기반
        positions = []
        for sym, pos in active.items():
            if symbol and sym != symbol:
                continue
            positions.append({
                "symbol": sym,
                "name": pos.name,
                "quantity": pos.total_recommended_qty,
                "avg_price": pos.entry_price,
                "cur_price": pos.entry_price,
                "pnl_rate": 0.0,
                "strategy": pos.strategy,
                "tranche": f"{sum(1 for t in pos.tranches if t.status == 'filled')}/{len(pos.tranches)}",
            })
        return {"positions": positions, "count": len(positions), "status": "fallback"}
