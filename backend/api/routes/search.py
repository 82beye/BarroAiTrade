"""통합검색 API 라우터 (티마 앱 벤치마킹 P2 — PRD §3.4).

엔드포인트:
  GET /api/search?q=&limit=10   - 종목/테마 통합검색

매칭 소스:
  - 종목: refined_signals.json(symbol+한글명) + theme_stocks(DB, 코드)
          + gateway universe(코드) 를 병합한 후보에서 코드/이름 부분일치.
  - 테마: themes 테이블(DB) name 부분일치.

DB·gateway·refined 파일이 모두 없으면 빈 결과. 대소문자 무시,
종목코드(숫자/영숫자)와 한글명 둘 다 매칭.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query
from sqlalchemy import text

from backend.core.state import app_state
from backend.db.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


def _refined_path() -> Path:
    """운영 데몬이 쓰는 data/refined_signals.json 경로 (repo_root/data)."""
    return Path(__file__).resolve().parents[3] / "data" / "refined_signals.json"


def _load_symbol_names() -> dict[str, str]:
    """refined_signals.json 에서 {symbol: name} 매핑 로드 (없으면 빈 dict)."""
    path = _refined_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("refined_signals.json 읽기 실패: %s", e)
        return {}
    out: dict[str, str] = {}
    for s in data.get("signals", []):
        sym = str(s.get("symbol") or "").upper()
        if not sym:
            continue
        name = s.get("name")
        if name:
            out[sym] = str(name)
    return out


async def _load_theme_stock_symbols(db) -> set[str]:
    """theme_stocks 테이블의 종목코드 집합 (테이블 없으면 빈 집합)."""
    try:
        res = await db.execute(text("SELECT DISTINCT symbol FROM theme_stocks"))
        return {str(r["symbol"]).upper() for r in res.mappings().all()}
    except Exception as e:
        logger.debug("theme_stocks 조회 실패: %s", e)
        return set()


async def _search_themes(db, q: str, limit: int) -> list[dict]:
    """themes.name 부분일치 → [{type, id, name}]."""
    try:
        res = await db.execute(
            text(
                "SELECT id, name FROM themes "
                "WHERE LOWER(name) LIKE :pat ORDER BY name LIMIT :limit"
            ),
            {"pat": f"%{q}%", "limit": limit},
        )
        return [
            {"type": "theme", "id": int(r["id"]), "name": r["name"]}
            for r in res.mappings().all()
        ]
    except Exception as e:
        logger.debug("themes 검색 실패: %s", e)
        return []


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="검색어 (종목코드 또는 한글명/테마명)"),
    limit: int = Query(10, ge=1, le=50, description="종류별 최대 결과 수"),
) -> dict:
    """종목/테마 통합검색.

    - 종목: 종목코드(부분일치) 또는 한글명(부분일치), 대소문자 무시
    - 테마: 테마명(부분일치)
    응답: {query, results:[{type:"stock",symbol,name} | {type:"theme",id,name}]}
    데이터 소스가 전무하면 results=[] (에러 아님).
    """
    query = q.strip()
    q_lower = query.lower()

    # ── 종목 후보 병합: {symbol: name} ─────────────────────────────────────
    candidates: dict[str, str] = {}

    # 1) refined_signals.json (symbol + 한글명)
    candidates.update(_load_symbol_names())

    # 2) DB theme_stocks 종목코드 + themes 검색
    theme_results: list[dict] = []
    async with get_db() as db:
        if db is not None:
            for sym in await _load_theme_stock_symbols(db):
                candidates.setdefault(sym, sym)
            theme_results = await _search_themes(db, q_lower, limit)

    # 3) gateway universe (코드만)
    gateway = app_state.market_gateway
    if gateway is not None:
        try:
            for sym in await gateway.get_universe():
                candidates.setdefault(str(sym).upper(), str(sym).upper())
        except Exception as e:
            logger.debug("universe 조회 실패: %s", e)

    # ── 종목 매칭: 코드 또는 이름 부분일치 (대소문자 무시) ──────────────────
    stock_results: list[dict] = []
    for sym, name in candidates.items():
        if q_lower in sym.lower() or q_lower in str(name).lower():
            stock_results.append({"type": "stock", "symbol": sym, "name": name})
    # 코드 정확/접두 일치를 앞으로, 그 외 이름순 안정 정렬
    stock_results.sort(
        key=lambda r: (
            0 if r["symbol"].lower().startswith(q_lower) else 1,
            r["symbol"],
        )
    )
    stock_results = stock_results[:limit]

    return {"query": query, "results": stock_results + theme_results}


__all__ = ["router"]
