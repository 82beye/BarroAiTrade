"""뉴스기반 테마 동적 발굴 파이프라인 (수동/스케줄 트리거, 큐레이션 시드와 별개).

`theme_refresher.py`(큐레이션 시드 theme_map.json, 고정 그룹) 와 달리 이 모듈은
실제 news_items 를 재료로 **새 테마 그룹을 발굴**한다 — theme_refresher 문서에서
"별도 대형 인프라"로 명시했던 부분(정직성 원칙: 뉴스 기반 재분류 없음)을 여기서
독립 파이프라인으로 구현한다. 기존 curated 테마는 건드리지 않고, 발굴된 테마는
description="뉴스기반 자동발굴"로 구분해 새 row 로 추가된다.

파이프라인 (전부 읽기 전용, 주문/게이트웨이 무관):
    1) 후보 종목 유니버스 = 거래대금 top-N ∪ 등락률(상승) top-N (OR),
       value_traded(억원) ≥ min_value_traded_eok 필터.
    2) 최근 lookback_days 일 news_items 중 후보 종목명이 title/body 에 등장하는
       기사만 매칭(종목명 언급 없으면 그 종목은 스킵 — 날조 금지, 억지 배정 없음).
    3) 매칭 기사 텍스트를 kiwipiepy 명사추출 + TF-IDF(sklearn) 로 종목별 상위
       키워드 추출.
    4) min_symbols_per_theme 개 이상 종목에 공통 등장하는 키워드 = 신규 테마명
       으로 승격 (종목 중복 허용 — 한 종목이 여러 테마에 속할 수 있음).
    5) ThemeRepository.upsert_theme + add_keyword + link_stock 로 적재.

전제: news_items 가 실제로 채워져 있어야 유의미한 결과가 나온다(news_collector_jobs
가 꺼져 있으면 symbols_with_news=0, themes_created=0 — 조용히 빈 결과, 에러 아님).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_KIWI = None  # lazy 싱글턴 — Kiwi() 생성 비용 회피


def _tokenize_nouns(text: str) -> list[str]:
    """명사(NNG/NNP)만 추출, 2자 이상 — 테마명 후보로서 의미 있는 토큰만."""
    global _KIWI
    if _KIWI is None:
        from kiwipiepy import Kiwi

        _KIWI = Kiwi()
    try:
        tokens = _KIWI.tokenize(text)
    except Exception:
        return []
    return [t.form for t in tokens if t.tag in ("NNG", "NNP") and len(t.form) >= 2]


def _get_quotes():
    """KiwoomQuotes lazy 구성 — readonly_scan_gateway._get_quotes() 와 동일 패턴.

    이 모듈은 API 서버/스크립트 양쪽에서 독립 호출 가능해야 하므로 자체 보유
    (app_state.market_gateway 의존 없음 — 순수 읽기전용 조회 전용).
    """
    app_key = os.environ.get("KIWOOM_APP_KEY", "").strip()
    app_secret = os.environ.get("KIWOOM_APP_SECRET", "").strip()
    if not app_key or not app_secret:
        return None
    try:
        from pydantic import SecretStr

        from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
        from backend.core.gateway.kiwoom_quotes import KiwoomQuotes

        oauth = KiwoomNativeOAuth(
            app_key=SecretStr(app_key),
            app_secret=SecretStr(app_secret),
            base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
        )
        return KiwoomQuotes(oauth=oauth)
    except Exception as exc:
        logger.warning("news_theme_discovery quotes 초기화 실패: %s", type(exc).__name__)
        return None


async def build_candidate_universe(
    quotes,
    *,
    top_n: int = 100,
    min_value_traded_eok: float = 100.0,
    stex_tp: str = "3",
) -> list[dict]:
    """거래대금 top-N ∪ 등락률(상승) top-N, value_traded(억원) ≥ 임계 필터.

    반환 항목: {symbol, name, price, change_pct, value_traded}. 실패한 쪽은
    빈 리스트로 취급(둘 다 실패면 빈 유니버스 — 조용히 반환, 에러 아님).
    """
    value_rows = await quotes.ranking(filter="value", stex_tp=stex_tp, limit=top_n) or []
    gainer_rows = await quotes.ranking(filter="gainers", stex_tp=stex_tp, limit=top_n) or []

    by_symbol: dict[str, dict] = {}
    for row in [*value_rows, *gainer_rows]:
        sym = (row.get("symbol") or "").strip()
        if sym and sym not in by_symbol:
            by_symbol[sym] = row

    return [
        row for row in by_symbol.values()
        if (row.get("value_traded") or 0.0) >= min_value_traded_eok
    ]


async def match_articles_to_symbols(
    candidates: list[dict], *, lookback_days: int = 7
) -> dict[str, list[str]]:
    """최근 lookback_days 일 news_items 중 종목명이 언급된 기사만 종목별로 매칭.

    반환: {symbol: [article_text, ...]}. 언급 기사가 하나도 없는 종목은 키 자체가
    없다(날조 금지 — 없는 뉴스로 억지 배정하지 않음).
    """
    from sqlalchemy import text

    from backend.db.database import get_db

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    async with get_db() as db:
        if db is None:
            return {}
        is_sqlite = db.engine.dialect.name == "sqlite"
        res = await db.execute(
            text("SELECT title, body FROM news_items WHERE published_at >= :cutoff"),
            {"cutoff": cutoff.isoformat() if is_sqlite else cutoff},
        )
        rows = res.mappings().all()

    out: dict[str, list[str]] = {}
    for cand in candidates:
        name = (cand.get("name") or "").strip()
        symbol = (cand.get("symbol") or "").strip()
        if not name or not symbol:
            continue
        matched = [
            f"{r['title']} {r.get('body') or ''}"
            for r in rows
            if name in (r["title"] or "") or name in (r.get("body") or "")
        ]
        if matched:
            out[symbol] = matched
    return out


def extract_theme_groups(
    symbol_articles: dict[str, list[str]],
    *,
    keywords_per_symbol: int = 5,
    min_symbols_per_theme: int = 2,
) -> dict[str, list[tuple[str, float]]]:
    """종목별 매칭기사에서 TF-IDF 상위 키워드 추출 → 공통 키워드로 테마 후보군 구성.

    반환: {키워드(테마명 후보): [(symbol, tfidf_score), ...]}. min_symbols_per_theme
    미달 키워드(=한 종목에만 등장)는 테마로 승격하지 않고 제외 — 최소 2종목 이상
    공유해야 "그룹"으로서 의미가 있다는 전제.
    """
    symbols = list(symbol_articles.keys())
    if len(symbols) < min_symbols_per_theme:
        return {}

    from sklearn.feature_extraction.text import TfidfVectorizer

    docs = [" ".join(_tokenize_nouns(" ".join(symbol_articles[s]))) for s in symbols]
    if not any(doc.strip() for doc in docs):
        return {}

    vectorizer = TfidfVectorizer(max_features=2000, token_pattern=r"(?u)\S+")
    try:
        matrix = vectorizer.fit_transform(docs)
    except ValueError:
        return {}
    feature_names = vectorizer.get_feature_names_out()

    keyword_symbols: dict[str, list[tuple[str, float]]] = {}
    for i, sym in enumerate(symbols):
        row = matrix[i].toarray().ravel()
        top_idx = row.argsort()[::-1][:keywords_per_symbol]
        for idx in top_idx:
            score = float(row[idx])
            if score <= 0:
                continue
            keyword_symbols.setdefault(feature_names[idx], []).append((sym, score))

    return {
        kw: syms for kw, syms in keyword_symbols.items()
        if len(syms) >= min_symbols_per_theme
    }


async def discover_dynamic_themes(
    *,
    top_n: int = 100,
    min_value_traded_eok: float = 100.0,
    lookback_days: int = 7,
    keywords_per_symbol: int = 5,
    min_symbols_per_theme: int = 2,
    quotes: Optional[object] = None,
) -> dict:
    """전체 파이프라인 1회 실행. 반환: 요약 통계 dict(status/candidates/themes_created 등)."""
    if quotes is None:
        quotes = _get_quotes()
    if quotes is None:
        return {
            "status": "no_key", "candidates": 0, "symbols_with_news": 0,
            "themes_created": 0, "links_created": 0, "themes": [],
        }

    candidates = await build_candidate_universe(
        quotes, top_n=top_n, min_value_traded_eok=min_value_traded_eok
    )
    symbol_articles = await match_articles_to_symbols(candidates, lookback_days=lookback_days)
    groups = extract_theme_groups(
        symbol_articles,
        keywords_per_symbol=keywords_per_symbol,
        min_symbols_per_theme=min_symbols_per_theme,
    )

    from backend.db.repositories.theme_repo import theme_repo

    themes_created = 0
    links_created = 0
    for keyword, sym_scores in groups.items():
        theme_id = await theme_repo.upsert_theme(keyword, description="뉴스기반 자동발굴")
        if theme_id is None:
            continue
        themes_created += 1
        await theme_repo.add_keyword(theme_id, keyword)
        for sym, score in sym_scores:
            if await theme_repo.link_stock(theme_id, sym, score):
                links_created += 1

    logger.info(
        "테마 뉴스발굴 완료: candidates=%d news_matched=%d themes=%d links=%d",
        len(candidates), len(symbol_articles), themes_created, links_created,
    )
    return {
        "status": "ok",
        "candidates": len(candidates),
        "symbols_with_news": len(symbol_articles),
        "themes_created": themes_created,
        "links_created": links_created,
        "themes": sorted(groups.keys()),
    }


__all__ = [
    "build_candidate_universe",
    "match_articles_to_symbols",
    "extract_theme_groups",
    "discover_dynamic_themes",
]
