"""종목 상세 API 라우터 (읽기 전용).

  GET /api/stocks/{symbol}/fundamental  - 기본정보(시총·유통비율·PER/PBR 등, ka10001)

kiwoom_quotes(키움 REST) 우선, 키/실패 시 종목명·상태만 담아 우아하게 degrade.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Path

from backend.api.routes.market import _get_quotes
from backend.core.market_data import stock_names

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/stocks/{symbol}/fundamental")
async def get_fundamental(symbol: str = Path(..., description="종목 코드")) -> dict:
    """종목 기본정보(펀더멘털). 키/실패 시 status=unsupported|no_data."""
    quotes = _get_quotes()
    if not quotes:
        return {
            "symbol": symbol,
            "name": stock_names.resolve(symbol),
            "status": "unsupported",
        }
    info = await quotes.stock_info(symbol)
    if not info:
        return {
            "symbol": symbol,
            "name": stock_names.resolve(symbol),
            "status": "no_data",
        }
    name = info.get("name") or stock_names.resolve(symbol)
    return {
        "symbol": info["symbol"],
        "name": name,
        "market_cap": info.get("market_cap"),
        "capital": info.get("capital"),
        "listed_shares": info.get("listed_shares"),
        "float_ratio": info.get("float_ratio"),
        "float_shares": info.get("float_shares"),
        "foreign_ratio": info.get("foreign_ratio"),
        "per": info.get("per"),
        "pbr": info.get("pbr"),
        "eps": info.get("eps"),
        "roe": info.get("roe"),
        "bps": info.get("bps"),
        "price": info.get("price"),
        "change_pct": info.get("change_pct"),
        "high_52w": info.get("high_52w"),
        "low_52w": info.get("low_52w"),
        "status": "ok",
    }


__all__ = ["router"]
