"""BAR-62 — REST 엔드포인트 (themes / calendar / news)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time as _time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from backend.api.schemas.theme import EventOut, NewsOut, ThemeOut, ThemeStockOut
from backend.core.state import app_state
from backend.db.database import get_db

# tima P0: 테마 종목 시세 보강 종목 수 상한 (gateway 부하 방지)
# [2026-07-08] Finup 도입으로 테마 수 21→30 증가 + theme_market_rows_capture(60s)
# 신규 병행 실행 — 기존 20 유지 시 1 사이클(30테마×20종목)이 429 누적으로 수분간
# 안 끝나는 것을 실측(theme_board_cache_jobs 20s 주기 대비 압도적으로 느려짐).
# 카드 요약 표시 목적상 테마당 상위 8종목이면 충분해 부하를 유의미하게 줄인다.
_THEME_QUOTE_CAP = int(os.environ.get("BARRO_THEME_QUOTE_CAP", "8") or 8)

# 테마보드 전체 로딩 시 여러 테마가 동시에 각자 최대 _THEME_QUOTE_CAP 종목을
# 동시조회하면 mockapi 429/커넥션 과부하로 이어짐(socket hang up 관측) — 동시
# 실거래소 조회 수를 프로세스 전역으로 제한(락은 순서 무관, 부하 완화 목적).
_THEME_QUOTE_SEMAPHORE = asyncio.Semaphore(5)

# 테마보드 화면표시 캐시 — GET 은 기본적으로 DB/캐시만 읽는다.
# Finup 기반 테마는 테마당 종목 수가 많아, 프론트 폴링 요청에서 키움 REST를 직접
# 기다리면 화면 전체가 멈춘다. 라이브 시세 보강은 명시적인 enrich=true 또는 별도
# 배경잡에서만 수행한다.
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
            change_pct=float(r["score"]),
        )
        for r in rows
    ]
    _apply_theme_stock_names(stocks)
    if enrich:
        await _enrich_theme_stocks(stocks)
    return stocks


@router.get("/api/themes", response_model=list[ThemeOut])
async def list_themes() -> list[ThemeOut]:
    return await fetch_themes()


@router.get("/api/themes/{theme_id}/stocks", response_model=list[ThemeStockOut])
async def get_theme_stocks(
    theme_id: int,
    enrich: bool = Query(
        default=False,
        description="true면 키움 REST/캐시로 시세 보강. 기본 false는 DB 스냅숏 즉시 반환.",
    ),
) -> list[ThemeStockOut]:
    if not enrich and _THEME_STOCKS_CACHE_TTL > 0:
        ent = _THEME_STOCKS_CACHE.get(theme_id)
        if ent and (_time.monotonic() - ent[0]) <= _THEME_STOCKS_CACHE_TTL:
            return ent[1]

    # 기본 경로는 Finup/DB 스냅숏의 score(change_pct fallback)를 즉시 반환한다.
    # enrich=true 에서만 ticker 시세 보강을 수행한다.
    stocks = await fetch_theme_stocks(theme_id, enrich=enrich)
    if stocks is None:
        raise HTTPException(status_code=404, detail="theme not found")
    if not enrich and _THEME_STOCKS_CACHE_TTL > 0:
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


@router.post("/api/themes/import-finup")
async def import_finup_themes(
    snapshot_path: Optional[str] = Query(default=None),
    replace: bool = Query(default=True),
) -> dict:
    """Finup 크롤링 스냅숏 → themes/theme_stocks 적재.

    `snapshot_path` 미지정 시 `data/finup_theme/latest.json`이 가리키는 최신 스냅숏을
    사용한다. `replace=true`는 기존 큐레이션 시드 테마를 비우고 Finup 스냅숏으로
    보드를 교체한다.
    """
    from backend.core.themes.finup_importer import import_finup_theme_snapshot

    result = await import_finup_theme_snapshot(
        Path(snapshot_path) if snapshot_path else None,
        replace=replace,
    )
    _THEME_STOCKS_CACHE.clear()
    return result


@router.post("/api/themes/discover")
async def discover_themes(
    top_n: int = Query(default=100, ge=1, le=200),
    min_value_traded_eok: float = Query(default=100.0, ge=0.0),
    lookback_days: int = Query(default=7, ge=1, le=30),
    exclude_themed: bool = Query(default=False),
    analyst_backend: str = Query(
        default="auto", pattern="^(auto|claude-cli|claude|rules|taxonomy)$"
    ),
) -> dict:
    """뉴스기반 신규 테마 그룹 자동 발굴(큐레이션 시드와 별개).

    거래대금 top-N ∪ 등락률 top-N(≥min_value_traded_eok 억원) 후보종목 전체를
    대상으로 최근 lookback_days 일 뉴스에서 중복 키워드를 추출하고, 주식
    애널리스트 분류기(analyst_backend=auto: claude-cli 가능 시 사용, 실패 시 rules)
    가 상승/하락/거래대금이 붙는 이유를 자동 테마명으로 확정·적재한다.
    exclude_themed=true 일 때만 기존 테마 보유 종목을 제외하는 갭필링 모드로 실행한다.
    읽기 전용 시세 조회 + DB 적재만 수행하며 주문 경로와 무관하다. news_items 가
    비어있으면 조용히 빈 결과를 반환한다(날조 금지).

    반환: {status, candidates, unthemed_candidates, symbols_with_news,
    themes_created, links_created, themes, analyst_backend, raw_themes, rejected_themes}.
    """
    from backend.core.themes.news_theme_discovery import discover_dynamic_themes

    result = await discover_dynamic_themes(
        top_n=top_n,
        min_value_traded_eok=min_value_traded_eok,
        lookback_days=lookback_days,
        exclude_already_themed=exclude_themed,
        analyst_backend=analyst_backend,
    )
    if result.get("themes_created"):
        _THEME_STOCKS_CACHE.clear()
    return result


@router.post("/api/themes/market-rows/capture")
async def capture_theme_market_rows(
    top_n: int = Query(default=100, ge=1, le=200),
    filters: str = Query(
        default="value,gainers,losers",
        description="comma separated: value,gainers,losers",
    ),
    stex_tp: str = Query(default="3", description="1=KRX, 2=NXT, 3=통합"),
    mrkt_tp: str = Query(default="000", description="키움 시장 구분"),
) -> dict:
    """키움 랭킹 row 를 CSV 로 저장하고, 같은 row 기준 테마 집계 CSV 를 생성한다."""
    from backend.core.themes.market_row_store import capture_theme_market_rows as _capture

    try:
        return await _capture(top_n=top_n, filters=filters, stex_tp=stex_tp, mrkt_tp=mrkt_tp)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/api/themes/market-rows/latest")
async def latest_theme_market_rows(
    limit: int = Query(default=200, ge=0, le=1000),
) -> dict:
    """가장 최근 CSV 로 저장한 키움 랭킹 row 를 조회한다."""
    from backend.core.themes.market_row_store import latest_meta, latest_rows

    meta = latest_meta()
    if meta is None:
        return {"status": "no_data", "rows": []}
    return {"status": "ok", "meta": meta, "rows": latest_rows(limit=limit or None)}


@router.get("/api/themes/market-aggregates/latest")
async def latest_theme_market_aggregates(
    limit: int = Query(default=200, ge=0, le=1000),
) -> dict:
    """가장 최근 CSV row 기준 테마별 등락률/거래대금 집계를 조회한다."""
    from backend.core.themes.market_row_store import latest_aggregates, latest_meta

    meta = latest_meta()
    if meta is None:
        return {"status": "no_data", "aggregates": []}
    return {
        "status": "ok",
        "meta": meta,
        "aggregates": latest_aggregates(limit=limit or None),
    }


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

    from backend.core.market_data import cache_quotes

    _apply_theme_stock_names(stocks[:_THEME_QUOTE_CAP])

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


def _apply_theme_stock_names(stocks: list[ThemeStockOut]) -> None:
    """로컬 stock_names.json 으로 종목명을 채운다. 네트워크 호출 없음."""
    if not stocks:
        return
    try:
        from backend.core.market_data import stock_names
    except Exception:
        return

    for stock in stocks:
        try:
            resolved = stock_names.resolve(stock.symbol)
        except Exception:
            continue
        if resolved and resolved != stock.symbol:
            stock.name = resolved


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
