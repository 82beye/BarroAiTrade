"""BAR-62 — REST 엔드포인트 (themes / calendar / news)."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from backend.api.schemas.theme import EventOut, NewsOut, ThemeOut, ThemeStockOut
from backend.core.state import app_state
from backend.db.database import get_db

# tima P0: 테마 종목 시세 보강 종목 수 상한 (gateway 부하 방지)
_THEME_QUOTE_CAP = 20

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/themes", response_model=list[ThemeOut])
async def list_themes() -> list[ThemeOut]:
    async with get_db() as db:
        if db is None:
            return []
        res = await db.execute(text("SELECT id, name, description FROM themes ORDER BY name"))
        return [
            ThemeOut(id=int(r["id"]), name=r["name"], description=r["description"] or "")
            for r in res.mappings().all()
        ]


@router.get("/api/themes/{theme_id}/stocks", response_model=list[ThemeStockOut])
async def get_theme_stocks(theme_id: int) -> list[ThemeStockOut]:
    async with get_db() as db:
        if db is None:
            return []
        # theme 존재 확인
        res = await db.execute(
            text("SELECT id, name FROM themes WHERE id = :id"), {"id": theme_id}
        )
        theme = res.mappings().first()
        if not theme:
            raise HTTPException(status_code=404, detail="theme not found")
        res2 = await db.execute(
            text(
                "SELECT symbol, score FROM theme_stocks WHERE theme_id = :id "
                "ORDER BY score DESC"
            ),
            {"id": theme_id},
        )
        rows = res2.mappings().all()

    stocks = [
        ThemeStockOut(
            symbol=r["symbol"],
            score=float(r["score"]),
            theme_id=theme_id,
            theme_name=theme["name"],
        )
        for r in rows
    ]

    # tima P0: gateway 가용 시 ticker 시세 보강 (동시성, 개별 실패는 None 유지).
    await _enrich_theme_stocks(stocks)
    return stocks


async def _enrich_theme_stocks(stocks: list[ThemeStockOut]) -> None:
    """theme_stocks 시세 보강 (in-place). gateway 미초기화 시 no-op (하위호환)."""
    gateway = app_state.market_gateway
    if gateway is None or not stocks:
        return

    async def _fill(stock: ThemeStockOut) -> None:
        try:
            ticker = await gateway.get_ticker(stock.symbol)
        except Exception as e:  # 개별 실패는 무시 (None 유지)
            logger.debug("theme stock ticker 보강 실패 %s: %s", stock.symbol, e)
            return
        stock.name = ticker.name
        stock.price = ticker.price
        stock.change_pct = ticker.change_pct
        if ticker.price and ticker.volume:
            stock.value_traded = round(ticker.price * ticker.volume / 1e8, 2)

    await asyncio.gather(*(_fill(s) for s in stocks[:_THEME_QUOTE_CAP]))


@router.get("/api/calendar", response_model=list[EventOut])
async def list_events(
    start: date = Query(...), end: date = Query(...)
) -> list[EventOut]:
    if start > end:
        raise HTTPException(status_code=422, detail="start > end")
    async with get_db() as db:
        if db is None:
            return []
        is_sqlite = db.engine.dialect.name == "sqlite"
        params = {
            "start": start.isoformat() if is_sqlite else start,
            "end": end.isoformat() if is_sqlite else end,
        }
        res = await db.execute(
            text(
                "SELECT * FROM market_events WHERE event_date BETWEEN :start AND :end "
                "ORDER BY event_date ASC LIMIT 1000"
            ),
            params,
        )
        return [
            EventOut(
                id=int(r["id"]),
                event_type=r["event_type"],
                symbol=r.get("symbol"),
                event_date=str(r["event_date"]),
                title=r["title"],
                source=r["source"],
            )
            for r in res.mappings().all()
        ]


@router.get("/api/calendar/symbol/{symbol}", response_model=list[EventOut])
async def list_events_by_symbol(symbol: str) -> list[EventOut]:
    async with get_db() as db:
        if db is None:
            return []
        res = await db.execute(
            text(
                "SELECT * FROM market_events WHERE symbol = :symbol "
                "ORDER BY event_date DESC LIMIT 100"
            ),
            {"symbol": symbol},
        )
        return [
            EventOut(
                id=int(r["id"]),
                event_type=r["event_type"],
                symbol=r.get("symbol"),
                event_date=str(r["event_date"]),
                title=r["title"],
                source=r["source"],
            )
            for r in res.mappings().all()
        ]


@router.get("/api/news/recent", response_model=list[NewsOut])
async def recent_news(
    source: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[NewsOut]:
    async with get_db() as db:
        if db is None:
            return []
        if source:
            sql = text(
                "SELECT * FROM news_items WHERE source = :source "
                "ORDER BY published_at DESC LIMIT :limit"
            )
            params = {"source": source, "limit": limit}
        else:
            sql = text(
                "SELECT * FROM news_items ORDER BY published_at DESC LIMIT :limit"
            )
            params = {"limit": limit}
        res = await db.execute(sql, params)
        results = []
        for r in res.mappings().all():
            tags_raw = r.get("tags") or "[]"
            if isinstance(tags_raw, str):
                try:
                    tags = json.loads(tags_raw)
                except Exception:
                    tags = []
            else:
                tags = list(tags_raw or [])
            results.append(
                NewsOut(
                    id=int(r["id"]),
                    source=r["source"],
                    source_id=r["source_id"],
                    title=r["title"],
                    url=r["url"],
                    published_at=str(r["published_at"]),
                    tags=[str(t) for t in tags],
                )
            )
        return results


__all__ = ["router"]
