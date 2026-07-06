"""BAR-62 — REST 엔드포인트 (themes / calendar / news)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time as _time
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from backend.api.schemas.theme import EventOut, NewsOut, ThemeOut, ThemeStockOut
from backend.core.state import app_state
from backend.db.database import get_db

# tima P0: 테마 종목 시세 보강 종목 수 상한 (gateway 부하 방지)
_THEME_QUOTE_CAP = 20

# 테마보드 전체 로딩 시 여러 테마가 동시에 각자 최대 _THEME_QUOTE_CAP 종목을
# 동시조회하면 mockapi 429/커넥션 과부하로 이어짐(socket hang up 관측) — 동시
# 실거래소 조회 수를 프로세스 전역으로 제한(락은 순서 무관, 부하 완화 목적).
_THEME_QUOTE_SEMAPHORE = asyncio.Semaphore(5)

# 테마보드 화면표시 캐시 — 데이터 작업(라이브 시세 갱신)은 백엔드 배경잡
# (theme_board_cache_jobs, 기본 15s 주기)이 전담하고, 이 GET 은 순수 읽기(표시)만
# 수행한다. TTL 은 "이 값보다 오래되면 배경잡이 멈춘 것으로 보고 인라인 폴백
# 계산"하는 안전망일 뿐 — 정상 운영 시 항상 잡 주기(15s) 내로 신선하다.
_THEME_STOCKS_CACHE_TTL = float(os.environ.get("BARRO_THEME_STOCKS_CACHE_SEC", "30") or 0)
_THEME_STOCKS_CACHE: dict[int, tuple] = {}  # theme_id -> (monotonic_ts, list[ThemeStockOut])

logger = logging.getLogger(__name__)

router = APIRouter()

# app_state.market_gateway 가 None(API 서버 프로세스는 항상 그렇다 — 오케스트레이터는
# 별도 라이브 데몬)일 때의 읽기전용 폴백 게이트웨이. screener.py 와 동일 패턴 —
# 캐시에 없는 종목(예: 대형주)도 키움 REST 온디맨드 조회로 이름·시세를 채운다.
_readonly_gateway = None


def _get_readonly_gateway():
    global _readonly_gateway
    if _readonly_gateway is None:
        from backend.core.gateway.readonly_scan_gateway import ReadOnlyScanGateway

        _readonly_gateway = ReadOnlyScanGateway()
    return _readonly_gateway


async def fetch_themes() -> list[ThemeOut]:
    """전체 테마 목록 (DB 미가용 시 빈 리스트). 스냅숏·라우트 공용."""
    async with get_db() as db:
        if db is None:
            return []
        res = await db.execute(text("SELECT id, name, description FROM themes ORDER BY name"))
        return [
            ThemeOut(id=int(r["id"]), name=r["name"], description=r["description"] or "")
            for r in res.mappings().all()
        ]


async def fetch_theme_stocks(
    theme_id: int, *, enrich: bool = True
) -> Optional[list[ThemeStockOut]]:
    """테마 종목 리스트 (score desc). 테마 없으면 None, DB 미가용 시 빈 리스트.

    스냅숏·라우트 공용. enrich=True 시 gateway 시세 보강(개별 실패는 None 유지).
    """
    async with get_db() as db:
        if db is None:
            return []
        res = await db.execute(
            text("SELECT id, name FROM themes WHERE id = :id"), {"id": theme_id}
        )
        theme = res.mappings().first()
        if not theme:
            return None
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
    if enrich:
        await _enrich_theme_stocks(stocks)
    return stocks


@router.get("/api/themes", response_model=list[ThemeOut])
async def list_themes() -> list[ThemeOut]:
    return await fetch_themes()


@router.get("/api/themes/{theme_id}/stocks", response_model=list[ThemeStockOut])
async def get_theme_stocks(theme_id: int) -> list[ThemeStockOut]:
    if _THEME_STOCKS_CACHE_TTL > 0:
        ent = _THEME_STOCKS_CACHE.get(theme_id)
        if ent and (_time.monotonic() - ent[0]) <= _THEME_STOCKS_CACHE_TTL:
            return ent[1]

    # tima P0: gateway 가용 시 ticker 시세 보강 (동시성, 개별 실패는 None 유지).
    stocks = await fetch_theme_stocks(theme_id, enrich=True)
    if stocks is None:
        raise HTTPException(status_code=404, detail="theme not found")
    if _THEME_STOCKS_CACHE_TTL > 0:
        _THEME_STOCKS_CACHE[theme_id] = (_time.monotonic(), stocks)
    return stocks


@router.post("/api/themes/refresh")
async def refresh_themes() -> dict:
    """큐레이션 시드(theme_map.json) → themes/theme_stocks 재적재 + 시세 스코어 갱신.

    테마 그룹 자체는 시드 고정, 종목별 스코어(등락률)만 캐시/거래소 시세로 갱신한다
    (뉴스 실시간 재분류 아님 — theme_refresher 참조). 읽기 전용 시세 조회 + 테마 테이블
    쓰기만 수행하므로 주문 경로와 무관, 게이트 불필요(상시 호출 가능).

    반환: {theme_count, symbol_count, status("ok"|"no_seed"), refreshed_at(ISO8601)}.
    """
    from backend.core.themes.theme_refresher import refresh_themes_from_seed

    result = await refresh_themes_from_seed()
    _THEME_STOCKS_CACHE.clear()  # 명시적 갱신 후에는 캐시 만료 대기 없이 즉시 반영
    return result


@router.post("/api/themes/discover")
async def discover_themes(
    top_n: int = Query(default=100, ge=1, le=200),
    min_value_traded_eok: float = Query(default=100.0, ge=0.0),
    lookback_days: int = Query(default=7, ge=1, le=30),
) -> dict:
    """뉴스기반 신규 테마 그룹 동적 발굴(큐레이션 시드와 별개, news_theme_discovery 참조).

    거래대금 top-N ∪ 등락률 top-N(≥min_value_traded_eok 억원) 후보종목의 최근
    lookback_days 일 뉴스에서 키워드를 추출해 공통 키워드를 테마로 승격·적재한다.
    읽기 전용(시세 조회+DB) — 주문 경로 무관, 게이트 불필요. news_items 가 비어있으면
    (news_collector 미가동) 조용히 빈 결과를 반환한다(날조 금지).

    반환: {status, candidates, symbols_with_news, themes_created, links_created, themes}.
    """
    from backend.core.themes.news_theme_discovery import discover_dynamic_themes

    result = await discover_dynamic_themes(
        top_n=top_n,
        min_value_traded_eok=min_value_traded_eok,
        lookback_days=lookback_days,
    )
    if result.get("themes_created"):
        _THEME_STOCKS_CACHE.clear()
    return result


# ── tima P1: 시간대별 테마 스냅숏(타임라인) ──────────────────────────────────


@router.get("/api/themes/snapshots")
async def theme_snapshots(
    date: Optional[str] = Query(None, description="YYYY-MM-DD (기본 오늘 UTC)"),
    slot: Optional[str] = Query(None, description="10:00 | 12:30 | 15:35"),
) -> dict:
    """테마 스냅숏 조회.

    - slot 미지정: 해당 날짜의 가용 slot 목록 {date, slots:[...]}.
    - slot 지정  : 해당 스냅숏 {date, slot, captured_at, themes:[...]}.
                   slot 이 유효범위 밖이면 422, 파일 없으면 status=no_data.
    """
    from backend.core.themes.snapshot import (
        VALID_SLOTS,
        is_valid_slot,
        list_available_slots,
        load_theme_snapshot,
    )

    date_str = date or datetime.now(timezone.utc).date().isoformat()

    if slot is None:
        return {"date": date_str, "slots": list_available_slots(date_str)}

    if not is_valid_slot(slot):
        raise HTTPException(
            status_code=422, detail=f"invalid slot: {slot} (허용: {list(VALID_SLOTS)})"
        )

    snap = load_theme_snapshot(date_str, slot)
    if snap is None:
        return {"date": date_str, "slot": slot, "status": "no_data"}
    return snap


@router.post("/api/themes/snapshots/capture")
async def capture_snapshot(
    slot: str = Query(..., description="10:00 | 12:30 | 15:35"),
) -> dict:
    """현재 테마 보드를 지정 slot 으로 동결 저장.

    gateway 미초기화 시 시세 null 인 채 저장(날조 금지). 운영 데몬의 10:00/12:30/15:35
    스케줄 배선은 후속 — 현재는 수동/관리 트리거용.
    """
    from backend.core.themes.snapshot import capture_theme_snapshot

    try:
        snap = await capture_theme_snapshot(slot)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {
        "date": snap["date"],
        "slot": snap["slot"],
        "captured_at": snap["captured_at"],
        "theme_count": len(snap["themes"]),
        "status": "ok",
    }


# ── tima P1: 종목 → 테마 역조회 ──────────────────────────────────────────────


@router.get("/api/stocks/{symbol}/themes")
async def stock_themes(symbol: str) -> dict:
    """종목이 속한 테마 목록 (score desc). theme_stocks JOIN themes."""
    async with get_db() as db:
        if db is None:
            return {"symbol": symbol, "themes": []}
        res = await db.execute(
            text(
                "SELECT t.id, t.name, t.description, ts.score "
                "FROM themes t JOIN theme_stocks ts ON ts.theme_id = t.id "
                "WHERE ts.symbol = :symbol "
                "ORDER BY ts.score DESC"
            ),
            {"symbol": symbol},
        )
        themes = [
            {
                "id": int(r["id"]),
                "name": r["name"],
                "description": r["description"] or "",
                "score": float(r["score"]),
            }
            for r in res.mappings().all()
        ]
    return {"symbol": symbol, "themes": themes}


async def _enrich_theme_stocks(stocks: list[ThemeStockOut]) -> None:
    """theme_stocks 시세 보강 (in-place).

    조달 우선순위: market_gateway(라이브 데몬) → ReadOnlyScanGateway(키움 REST 온디맨드,
    screener.py 와 동일) → ohlcv 캐시(cache_quotes, 지연 시세). 종목명은 stock_names
    마스터로 항상 선시도, 실패 시 게이트웨이 응답명으로 보강.
    """
    if not stocks:
        return

    from backend.core.market_data import cache_quotes, stock_names

    for stock in stocks[:_THEME_QUOTE_CAP]:
        resolved = stock_names.resolve(stock.symbol)
        if resolved and resolved != stock.symbol:
            stock.name = resolved

    gateway = app_state.market_gateway or _get_readonly_gateway()

    async def _fill(stock: ThemeStockOut) -> None:
        try:
            async with _THEME_QUOTE_SEMAPHORE:
                ticker = await gateway.get_ticker(stock.symbol)
        except Exception as e:  # 개별 실패는 캐시 폴백 (None 유지 아님)
            logger.debug("theme stock ticker 보강 실패 %s: %s", stock.symbol, e)
            q = cache_quotes.get_quote(stock.symbol)
            if not q:
                return
            stock.price = q.get("price")
            stock.change_pct = q.get("change_pct")
            stock.day_open = q.get("day_open")
            stock.day_high = q.get("day_high")
            stock.day_low = q.get("day_low")
            stock.value_traded = q.get("value_traded")
            return
        if ticker.name and ticker.name != stock.symbol:
            stock.name = ticker.name
        stock.price = ticker.price
        stock.change_pct = ticker.change_pct
        if ticker.price and ticker.volume:
            stock.value_traded = round(ticker.price * ticker.volume / 1e8, 2)
        else:
            # ka10001(기본정보) 라이브 조회는 거래량 미포함 — 거래대금은 캐시로 보강
            q = cache_quotes.get_quote(stock.symbol)
            if q:
                stock.value_traded = q.get("value_traded")

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
