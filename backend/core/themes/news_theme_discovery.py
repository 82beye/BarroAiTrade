"""뉴스기반 테마 동적 발굴 파이프라인 (자동 분류/스케줄 트리거, 큐레이션 시드와 별개).

`theme_refresher.py`(큐레이션 시드 theme_map.json, 고정 그룹) 와 달리 이 모듈은
실제 news_items 를 재료로 **새 테마 그룹을 발굴**한다 — theme_refresher 문서에서
"별도 대형 인프라"로 명시했던 부분(정직성 원칙: 뉴스 기반 재분류 없음)을 여기서
독립 파이프라인으로 구현한다. 기존 curated 테마는 건드리지 않고, 발굴된 테마는
description="뉴스기반 자동발굴"로 구분해 새 row 로 추가된다.

파이프라인 (전부 읽기 전용, 주문/게이트웨이 무관):
    1) 후보 종목 유니버스 = 거래대금 top-N ∪ 등락률(상승) top-N (OR),
       value_traded(억원) ≥ min_value_traded_eok 필터.
    2) 기본은 상위 후보 전체를 사용한다. 필요 시 exclude_already_themed=True 로
       이미 테마가 있는 종목을 제외해 갭필링 모드로 실행할 수 있다.
    3) 최근 lookback_days 일 news_items 중 후보 종목명이 title/body
       에 등장하는 기사만 매칭(종목명 언급 없으면 그 종목은 스킵 — 날조 금지).
    4) 매칭 기사 텍스트를 kiwipiepy 명사추출 + TF-IDF(sklearn) 로 종목별 상위
       키워드 추출.
    5) min_symbols_per_theme 개 이상 종목에 공통 등장하는 키워드를 뽑되,
       곧장 테마명으로 쓰지 않고 주식 애널리스트 분류기(claude-cli 가능 시)로
       "왜 오늘 수급이 붙는 테마인지"를 판단한다.
    6) 애널리스트 분류가 성공한 후보만 ThemeRepository.upsert_theme + add_keyword
       + link_stock 로 적재한다. 언론사/상용 금융 명사/회사명은 등록하지 않는다.

전제: news_items 가 실제로 채워져 있어야 유의미한 결과가 나온다(news_collector_jobs
가 꺼져 있으면 symbols_with_news=0, themes_created=0 — 조용히 빈 결과, 에러 아님).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_KIWI = None  # lazy 싱글턴 — Kiwi() 생성 비용 회피

# [실측 2026-07-06] 실뉴스로 1차 검증 시 일반 금융 상용어가 테마명으로 잘못
# 승격되는 문제 확인("설정액"·"운용"·"자산"·"펀드" 등) — 업종/산업 특정성이
# 없는 보일러플레이트 명사를 사전 제외. 회사명 자체(예: 삼성전자)는 별도로
# extract_theme_groups 에서 후보 종목명 목록과 대조해 제외한다.
_STOPWORD_NOUNS: frozenset[str] = frozenset({
    "펀드", "운용", "자산", "설정액", "투자", "증권", "거래소", "코스피", "코스닥",
    "전일", "종목", "상승", "하락", "마감", "오늘", "이날", "기자", "특징주",
    "시장", "주가", "종가", "거래", "매매", "매수", "매도", "기업", "회사",
    "발표", "공시", "실적", "전망", "분석", "관계자", "지난해", "올해", "최근",
    "서울", "뉴욕", "사진", "제공", "대표", "회장", "사장", "부사장", "김태종",
    "돌파", "급등", "강세", "약세", "개인", "기관", "외국인", "수급",
    "사업", "제품", "서비스", "매출", "영업", "수익", "분기", "계약", "공급",
    "주주", "상장", "인수", "매각", "합병", "개발", "생산", "판매", "고객",
    "삼성전자", "하이닉스", "SK하이닉스", "삼전닉스50",
    # [실측 2026-07-06] 언론사 boilerplate(바이라인/저작권 문구) 자체가 테마로
    # 승격되던 문제 — 수집 대상 언론사명은 항상 노이즈이므로 명시 제외.
    "연합뉴스", "한국경제", "한경", "매일경제", "매경", "이데일리",
    "뉴스", "보도", "특파원", "취재", "뉴스1", "뉴시스", "머니투데이",
    "파이낸셜뉴스", "아시아경제", "헤럴드경제", "조선비즈",
})

# taxonomy 는 애널리스트 자동분류의 보조 힌트/폴백이다. 신규 당일 테마는 아래 목록에
# 없어도 noise 필터와 애널리스트 판단을 통과하면 등록될 수 있다.
_THEME_ALIASES: dict[str, str] = {
    # 반도체/AI
    "반도체": "반도체",
    "메모리": "반도체",
    "D램": "반도체",
    "DRAM": "반도체",
    "낸드": "반도체",
    "NAND": "반도체",
    "파운드리": "반도체",
    "후공정": "반도체",
    "패키징": "반도체",
    "HBM": "HBM",
    "AI반도체": "AI",
    "온디바이스AI": "AI",
    "인공지능": "AI",
    "AI": "AI",
    "로봇": "로봇",
    # 2차전지/전기차
    "2차전지": "2차전지",
    "이차전지": "2차전지",
    "배터리": "2차전지",
    "전고체": "2차전지",
    "양극재": "2차전지",
    "음극재": "2차전지",
    "분리막": "2차전지",
    "전해액": "2차전지",
    "리튬": "2차전지",
    "전기차": "자동차",
    "자율주행": "자동차",
    # 전력/원전/인프라
    "원전": "원전",
    "SMR": "원전",
    "전력기기": "전력기기",
    "변압기": "전력기기",
    "전력망": "전력기기",
    "송전": "전력기기",
    "전선": "전선",
    # 산업 모멘텀
    "조선": "조선",
    "LNG": "조선",
    "방산": "방산",
    "우주항공": "방산",
    "드론": "방산",
    # 헬스케어
    "바이오": "바이오",
    "제약": "제약",
    "의료기기": "의료기기",
    "비만치료제": "바이오",
    "ADC": "바이오",
    # 인터넷/콘텐츠/보안
    "게임": "인터넷",
    "웹툰": "인터넷",
    "엔터": "엔터",
    "K팝": "엔터",
    "보안": "보안",
    "사이버보안": "보안",
}


def classify_theme_keyword(keyword: str) -> tuple[Optional[str], str]:
    """원시 뉴스 키워드를 표준 테마명으로 분류한다.

    Returns:
        (표준 테마명, reason). 표준 테마명이 None 이면 자동 등록 금지.
    """
    kw = (keyword or "").strip()
    if not kw:
        return None, "empty"
    if kw in _STOPWORD_NOUNS:
        return None, "stopword"
    canonical = _THEME_ALIASES.get(kw) or _THEME_ALIASES.get(kw.upper())
    if canonical:
        return canonical, "taxonomy"
    return None, "unclassified"


def _tokenize_nouns(text: str) -> list[str]:
    """명사(NNG/NNP)만 추출, 2자 이상 + 상용어 제외 — 테마명 후보로서 의미 있는 토큰만."""
    global _KIWI
    if _KIWI is None:
        from kiwipiepy import Kiwi

        _KIWI = Kiwi()
    try:
        tokens = _KIWI.tokenize(text)
    except Exception:
        return []
    return [
        t.form for t in tokens
        if t.tag in ("NNG", "NNP")
        and len(t.form) >= 2
        and t.form not in _STOPWORD_NOUNS
        and not any(ch.isdigit() for ch in t.form)  # [실측] 토큰화 잡음(예: "삼전닉스50") 제외
    ]


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

    market_row_store.fetch_ranking_rows/merge_symbol_rows 를 재사용해 랭킹
    후보 유니버스 로직 중복을 제거한다(docs/03-analysis/2026-07-08-theme-
    implementation-issues-and-fix-design.md §2-D). 반환 항목은 기존과 동일
    {symbol, name, price, change_pct, value_traded, ...} — 실패한 쪽은 빈
    리스트로 취급(둘 다 실패면 빈 유니버스 — 조용히 반환, 에러 아님).
    """
    from backend.core.themes.market_row_store import fetch_ranking_rows, merge_symbol_rows

    rows = await fetch_ranking_rows(
        quotes=quotes, top_n=top_n, filters=("value", "gainers"), stex_tp=stex_tp,
    )
    merged = merge_symbol_rows(rows)

    return [
        row for row in merged.values()
        if (row.get("value_traded") or 0.0) >= min_value_traded_eok
    ]


async def filter_unthemed_symbols(candidates: list[dict]) -> list[dict]:
    """이미 (큐레이션이든 발굴이든) 테마가 하나라도 있는 종목은 후보에서 제외.

    갭필링 원칙: 거래대금·등락률 상위 종목 중 **테마 분류가 없는 종목만** 뉴스기반
    파이프라인의 대상으로 삼는다 — 이미 21종 큐레이션 테마(반도체 등)에 속한
    종목까지 중복으로 재분류하지 않는다.
    """
    if not candidates:
        return []

    from sqlalchemy import text

    from backend.db.database import get_db

    symbols = [c["symbol"] for c in candidates if c.get("symbol")]
    if not symbols:
        return []

    async with get_db() as db:
        if db is None:
            return candidates  # DB 미가용 — 필터 불가, 전체를 폴백 후보로(조용히 강등)
        placeholders = ", ".join(f":s{i}" for i in range(len(symbols)))
        params = {f"s{i}": sym for i, sym in enumerate(symbols)}
        res = await db.execute(
            text(f"SELECT DISTINCT symbol FROM theme_stocks WHERE symbol IN ({placeholders})"),
            params,
        )
        already_themed = {row["symbol"] for row in res.mappings().all()}

    return [c for c in candidates if c["symbol"] not in already_themed]


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
    exclude_names: Optional[set[str]] = None,
) -> dict[str, list[tuple[str, float]]]:
    """종목별 매칭기사에서 TF-IDF 상위 키워드 추출 → 공통 키워드로 테마 후보군 구성.

    exclude_names: 회사명 자체(예: 삼성전자) — 다른 종목 기사에 우연히 언급돼
    테마처럼 승격되는 것을 방지(회사명은 테마가 아님, [실측 2026-07-06] 발견).

    반환: {키워드(테마명 후보): [(symbol, tfidf_score), ...]}. min_symbols_per_theme
    미달 키워드(=한 종목에만 등장)는 테마로 승격하지 않고 제외 — 최소 2종목 이상
    공유해야 "그룹"으로서 의미가 있다는 전제.
    """
    exclude_names = exclude_names or set()
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
            kw = feature_names[idx]
            if score <= 0 or kw in exclude_names:
                continue
            keyword_symbols.setdefault(kw, []).append((sym, score))

    return {
        kw: syms for kw, syms in keyword_symbols.items()
        if len(syms) >= min_symbols_per_theme
    }


def classify_theme_groups(
    groups: dict[str, list[tuple[str, float]]],
) -> tuple[dict[str, list[tuple[str, float]]], dict[str, list[str]], dict[str, str]]:
    """원시 키워드 그룹을 표준 테마 그룹으로 병합한다.

    Returns:
        classified_groups:
            {표준테마명: [(symbol, score), ...]}. 같은 종목이 여러 alias 에서
            들어오면 가장 높은 score 만 유지한다.
        theme_keywords:
            {표준테마명: [원시키워드, ...]}. theme_keywords 테이블에 남길 근거.
        rejected_groups:
            {원시키워드: reason}. CLI/로그 검토용. DB 등록 대상 아님.
    """
    classified_scores: dict[str, dict[str, float]] = {}
    theme_keywords: dict[str, set[str]] = {}
    rejected: dict[str, str] = {}

    for keyword, sym_scores in groups.items():
        theme_name, reason = classify_theme_keyword(keyword)
        if theme_name is None:
            rejected[keyword] = reason
            continue
        theme_keywords.setdefault(theme_name, set()).add(keyword)
        by_symbol = classified_scores.setdefault(theme_name, {})
        for sym, score in sym_scores:
            by_symbol[sym] = max(float(score), by_symbol.get(sym, 0.0))

    classified_groups = {
        theme_name: sorted(scores.items(), key=lambda item: item[1], reverse=True)
        for theme_name, scores in sorted(classified_scores.items())
    }
    keyword_map = {
        theme_name: sorted(keywords)
        for theme_name, keywords in sorted(theme_keywords.items())
    }
    return classified_groups, keyword_map, dict(sorted(rejected.items()))


def _is_noise_theme_name(name: str, company_names: set[str]) -> tuple[bool, str]:
    theme = (name or "").strip()
    if not theme:
        return True, "empty"
    if theme in _STOPWORD_NOUNS:
        return True, "stopword"
    if theme in company_names:
        return True, "company_name"
    if len(theme) < 2 or len(theme) > 24:
        return True, "bad_length"
    if any(theme in company or company in theme for company in company_names if len(company) >= 2):
        return True, "company_name"
    return False, ""


def is_noise_theme_name(name: str, company_names: Optional[set[str]] = None) -> tuple[bool, str]:
    """외부 CLI/정리 도구용 noise 판정."""
    return _is_noise_theme_name(name, company_names or set())


def _rule_based_analyst_classify(
    groups: dict[str, list[tuple[str, float]]],
    *,
    company_names: Optional[set[str]] = None,
) -> tuple[dict[str, list[tuple[str, float]]], dict[str, list[str]], dict[str, str], list[dict]]:
    """LLM 부재 시 자동 폴백. taxonomy 힌트 + 동적 키워드 noise 필터를 사용한다."""
    company_names = company_names or set()
    classified_scores: dict[str, dict[str, float]] = {}
    theme_keywords: dict[str, set[str]] = {}
    rejected: dict[str, str] = {}
    decisions: list[dict] = []

    for keyword, sym_scores in groups.items():
        canonical, reason = classify_theme_keyword(keyword)
        if canonical is None:
            is_noise, noise_reason = _is_noise_theme_name(keyword, company_names)
            if is_noise:
                rejected[keyword] = noise_reason
                decisions.append({
                    "keyword": keyword, "action": "reject", "reason": noise_reason,
                    "backend": "rules",
                })
                continue
            theme_name = keyword
            reason = "dynamic_keyword"
        else:
            theme_name = canonical

        theme_keywords.setdefault(theme_name, set()).add(keyword)
        by_symbol = classified_scores.setdefault(theme_name, {})
        for sym, score in sym_scores:
            by_symbol[sym] = max(float(score), by_symbol.get(sym, 0.0))
        decisions.append({
            "keyword": keyword, "action": "accept", "theme": theme_name,
            "reason": reason, "backend": "rules",
        })

    classified_groups = {
        theme_name: sorted(scores.items(), key=lambda item: item[1], reverse=True)
        for theme_name, scores in sorted(classified_scores.items())
    }
    keyword_map = {
        theme_name: sorted(keywords)
        for theme_name, keywords in sorted(theme_keywords.items())
    }
    return classified_groups, keyword_map, dict(sorted(rejected.items())), decisions


def _claude_bin() -> str:
    env_bin = (os.environ.get("CLAUDE_CLI_BIN") or "").strip()
    if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
        return env_bin
    return shutil.which("claude") or ""


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    raw = text.strip()
    try:
        outer = json.loads(raw)
        if isinstance(outer, dict) and "result" in outer:
            raw = str(outer["result"]).strip()
    except (json.JSONDecodeError, ValueError):
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _run_claude_theme_analyst(
    prompt: str, *, timeout: float = 35.0, model: Optional[str] = None
) -> dict | None:
    bin_path = _claude_bin()
    if not bin_path:
        return None
    cmd = [bin_path, "-p", prompt, "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return _extract_json(proc.stdout)


def _truncate(text: str, limit: int) -> str:
    compact = " ".join(str(text or "").split())
    return compact[:limit]


def _build_theme_analyst_prompt(
    *,
    candidates: list[dict],
    symbol_articles: dict[str, list[str]],
    raw_groups: dict[str, list[tuple[str, float]]],
) -> str:
    by_symbol = {str(c.get("symbol")): c for c in candidates if c.get("symbol")}
    candidate_lines = []
    for row in candidates[:160]:
        symbol = str(row.get("symbol") or "")
        name = str(row.get("name") or "")
        change = row.get("change_pct", "")
        value = row.get("value_traded", "")
        candidate_lines.append(f"- {symbol} {name} 등락률={change} 거래대금억={value}")

    keyword_lines = []
    for keyword, rows in sorted(raw_groups.items()):
        members = []
        for sym, score in rows[:12]:
            name = by_symbol.get(sym, {}).get("name", "")
            members.append(f"{sym}({name},{score:.3f})")
        keyword_lines.append(f"- {keyword}: {', '.join(members)}")

    article_lines = []
    for sym, articles in sorted(symbol_articles.items()):
        name = by_symbol.get(sym, {}).get("name", "")
        snippets = " / ".join(_truncate(a, 160) for a in articles[:2])
        article_lines.append(f"- {sym} {name}: {snippets}")

    return f"""\
너는 한국 주식 장중 테마/수급 애널리스트다.
아래 데이터는 거래대금 상위와 등락률 상위 종목, 각 종목 관련 최근 뉴스, 그리고 여러 종목에 중복 등장한 키워드다.
목표는 "오늘 어떤 이유로 종목들이 함께 오르거나 거래대금이 붙는지"를 자동으로 테마명으로 분류하는 것이다.

규칙:
- 테마명은 실제 매매자가 이해하는 한국어 테마명으로 만든다. 예: 전력망 증설, 변압기, 원전, HBM, 전고체, 우크라이나 재건, 양자암호.
- 새 당일 테마라면 기존 표준테마에 억지로 맞추지 말고 새 이름을 만든다.
- 언론사명, 지역명, 기자명, 회사명 단독, 펀드/운용/자산/설정액 같은 금융 일반명사는 반드시 제외한다.
- 단순 "급등/강세/돌파/실적/계약/공급" 같은 현상·일반어는 테마로 만들지 않는다.
- 최소 2개 이상 종목과 뉴스/키워드 근거가 있는 테마만 채택한다.
- 출력은 JSON 객체 하나만 반환한다.

출력 스키마:
{{
  "themes": [
    {{
      "theme": "테마명",
      "keywords": ["근거 키워드"],
      "symbols": ["005930", "000660"],
      "confidence": 0.0,
      "reason": "왜 이 테마로 분류했는지 한 문장"
    }}
  ],
  "rejected_keywords": [
    {{"keyword": "연합뉴스", "reason": "언론사명"}}
  ]
}}

[상위 후보 종목]
{chr(10).join(candidate_lines) or "(없음)"}

[중복 키워드 후보]
{chr(10).join(keyword_lines) or "(없음)"}

[종목별 뉴스 스니펫]
{chr(10).join(article_lines) or "(없음)"}
"""


def _parse_analyst_response(
    obj: dict,
    raw_groups: dict[str, list[tuple[str, float]]],
    *,
    company_names: set[str],
) -> tuple[dict[str, list[tuple[str, float]]], dict[str, list[str]], dict[str, str], list[dict]]:
    raw_symbols = {
        sym for rows in raw_groups.values() for sym, _ in rows
    }
    symbol_scores_by_keyword = {
        keyword: {sym: float(score) for sym, score in rows}
        for keyword, rows in raw_groups.items()
    }
    classified_scores: dict[str, dict[str, float]] = {}
    theme_keywords: dict[str, set[str]] = {}
    used_keywords: set[str] = set()
    rejected: dict[str, str] = {}
    decisions: list[dict] = []

    themes = obj.get("themes") if isinstance(obj, dict) else None
    if not isinstance(themes, list):
        return {}, {}, {keyword: "invalid_analyst_response" for keyword in raw_groups}, []

    for item in themes:
        if not isinstance(item, dict):
            continue
        theme_name = str(item.get("theme") or "").strip()
        is_noise, reason = _is_noise_theme_name(theme_name, company_names)
        if is_noise:
            decisions.append({
                "theme": theme_name, "action": "reject", "reason": reason,
                "backend": "claude-cli",
            })
            continue
        keywords = [
            str(k).strip() for k in (item.get("keywords") or [])
            if str(k).strip() in raw_groups
        ]
        if not keywords and theme_name in raw_groups:
            keywords = [theme_name]
        allowed_symbols = {
            str(s).strip() for s in (item.get("symbols") or [])
            if str(s).strip() in raw_symbols
        }
        symbol_scores: dict[str, float] = {}
        for keyword in keywords:
            for sym, score in symbol_scores_by_keyword.get(keyword, {}).items():
                if allowed_symbols and sym not in allowed_symbols:
                    continue
                symbol_scores[sym] = max(float(score), symbol_scores.get(sym, 0.0))
        if len(symbol_scores) < 2:
            decisions.append({
                "theme": theme_name, "keywords": keywords, "action": "reject",
                "reason": "min_symbols_not_met", "backend": "claude-cli",
            })
            continue

        used_keywords.update(keywords)
        by_symbol = classified_scores.setdefault(theme_name, {})
        for sym, score in symbol_scores.items():
            by_symbol[sym] = max(score, by_symbol.get(sym, 0.0))
        theme_keywords.setdefault(theme_name, set()).update(keywords)
        decisions.append({
            "theme": theme_name, "keywords": keywords,
            "symbols": sorted(symbol_scores), "action": "accept",
            "confidence": float(item.get("confidence") or 0.0),
            "reason": str(item.get("reason") or ""),
            "backend": "claude-cli",
        })

    for item in obj.get("rejected_keywords") or []:
        if not isinstance(item, dict):
            continue
        keyword = str(item.get("keyword") or "").strip()
        if keyword in raw_groups and keyword not in used_keywords:
            rejected[keyword] = str(item.get("reason") or "analyst_rejected")

    for keyword in raw_groups:
        if keyword not in used_keywords and keyword not in rejected:
            rejected[keyword] = "not_selected_by_analyst"

    classified_groups = {
        theme_name: sorted(scores.items(), key=lambda item: item[1], reverse=True)
        for theme_name, scores in sorted(classified_scores.items())
    }
    keyword_map = {
        theme_name: sorted(keywords)
        for theme_name, keywords in sorted(theme_keywords.items())
    }
    return classified_groups, keyword_map, dict(sorted(rejected.items())), decisions


async def classify_theme_groups_with_analyst(
    raw_groups: dict[str, list[tuple[str, float]]],
    *,
    candidates: list[dict],
    symbol_articles: dict[str, list[str]],
    analyst_backend: str = "auto",
    analyst_model: Optional[str] = None,
    analyst_timeout: float = 35.0,
    llm_fn=None,
) -> tuple[dict[str, list[tuple[str, float]]], dict[str, list[str]], dict[str, str], list[dict], str]:
    """주식 애널리스트 관점으로 원시 키워드를 자동 테마 분류한다."""
    company_names = {str(c.get("name") or "").strip() for c in candidates if c.get("name")}
    backend = (analyst_backend or os.environ.get("BARRO_THEME_ANALYST_BACKEND") or "auto").strip()
    backend = backend.lower()

    if backend == "taxonomy":
        classified, keywords, rejected = classify_theme_groups(raw_groups)
        decisions = [
            {
                "keyword": keyword,
                "action": "reject" if keyword in rejected else "accept",
                "reason": rejected.get(keyword, "taxonomy"),
                "backend": "taxonomy",
            }
            for keyword in raw_groups
        ]
        return classified, keywords, rejected, decisions, "taxonomy"

    should_try_llm = backend in {"auto", "claude", "claude-cli"} and (
        llm_fn is not None or bool(_claude_bin())
    )
    if should_try_llm and raw_groups:
        prompt = _build_theme_analyst_prompt(
            candidates=candidates,
            symbol_articles=symbol_articles,
            raw_groups=raw_groups,
        )
        if llm_fn is not None:
            obj = await asyncio.to_thread(llm_fn, prompt)
        else:
            obj = await asyncio.to_thread(
                _run_claude_theme_analyst,
                prompt,
                timeout=analyst_timeout,
                model=analyst_model,
            )
        if isinstance(obj, dict):
            classified, keywords, rejected, decisions = _parse_analyst_response(
                obj, raw_groups, company_names=company_names
            )
            if classified or backend in {"claude", "claude-cli"}:
                return classified, keywords, rejected, decisions, "claude-cli"

    classified, keywords, rejected, decisions = _rule_based_analyst_classify(
        raw_groups, company_names=company_names
    )
    return classified, keywords, rejected, decisions, "rules"


def _serialize_groups(
    groups: dict[str, list[tuple[str, float]]],
) -> dict[str, list[dict[str, float | str]]]:
    return {
        name: [
            {"symbol": sym, "score": round(float(score), 6)}
            for sym, score in sym_scores
        ]
        for name, sym_scores in sorted(groups.items())
    }


async def discover_dynamic_theme_candidates(
    *,
    top_n: int = 100,
    min_value_traded_eok: float = 100.0,
    lookback_days: int = 7,
    keywords_per_symbol: int = 5,
    min_symbols_per_theme: int = 2,
    quotes: Optional[object] = None,
    exclude_already_themed: bool = False,
    analyst_backend: str = "auto",
    analyst_model: Optional[str] = None,
    analyst_timeout: float = 35.0,
    analyst_llm_fn=None,
) -> dict:
    """자동등록 전 후보 산출. DB 쓰기 없이 원시/분류/거절 그룹을 반환한다."""
    if quotes is None:
        quotes = _get_quotes()
    if quotes is None:
        return {
            "status": "no_key", "candidates": 0, "unthemed_candidates": 0,
            "selected_candidates": 0, "exclude_already_themed": exclude_already_themed,
            "analyst_backend": analyst_backend,
            "symbols_with_news": 0, "raw_groups": {}, "classified_groups": {},
            "theme_keywords": {}, "rejected_groups": {}, "analyst_decisions": [],
        }

    all_candidates = await build_candidate_universe(
        quotes, top_n=top_n, min_value_traded_eok=min_value_traded_eok
    )
    unthemed_candidates = await filter_unthemed_symbols(all_candidates)
    candidates = unthemed_candidates if exclude_already_themed else all_candidates
    symbol_articles = await match_articles_to_symbols(candidates, lookback_days=lookback_days)
    company_names = {c["name"] for c in all_candidates if c.get("name")}
    raw_groups = extract_theme_groups(
        symbol_articles,
        keywords_per_symbol=keywords_per_symbol,
        min_symbols_per_theme=min_symbols_per_theme,
        exclude_names=company_names,
    )
    classified_groups, theme_keywords, rejected_groups, analyst_decisions, used_backend = (
        await classify_theme_groups_with_analyst(
            raw_groups,
            candidates=candidates,
            symbol_articles=symbol_articles,
            analyst_backend=analyst_backend,
            analyst_model=analyst_model,
            analyst_timeout=analyst_timeout,
            llm_fn=analyst_llm_fn,
        )
    )

    return {
        "status": "ok",
        "candidates": len(all_candidates),
        "unthemed_candidates": len(unthemed_candidates),
        "selected_candidates": len(candidates),
        "exclude_already_themed": exclude_already_themed,
        "analyst_backend": used_backend,
        "symbols_with_news": len(symbol_articles),
        "raw_groups": _serialize_groups(raw_groups),
        "classified_groups": _serialize_groups(classified_groups),
        "theme_keywords": theme_keywords,
        "rejected_groups": rejected_groups,
        "analyst_decisions": analyst_decisions,
    }


def _coerce_group_scores(group_rows: list) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for row in group_rows:
        if isinstance(row, dict):
            sym = str(row.get("symbol") or "").strip()
            score = row.get("score") or 0.0
        else:
            sym = str(row[0]).strip()
            score = row[1] if len(row) > 1 else 0.0
        if not sym:
            continue
        out.append((sym, float(score)))
    return out


async def persist_theme_groups(
    classified_groups: dict[str, list],
    *,
    theme_keywords: Optional[dict[str, list[str]]] = None,
    min_symbols_per_theme: int = 2,
) -> dict:
    """분류 완료된 테마 그룹을 DB에 등록한다."""
    from backend.db.repositories.theme_repo import theme_repo

    theme_keywords = theme_keywords or {}
    themes_created = 0
    links_created = 0
    individual_groups = 0
    themes: list[str] = []

    for theme_name, group_rows in sorted(classified_groups.items()):
        sym_scores = _coerce_group_scores(group_rows)
        if not theme_name or not sym_scores:
            continue
        if len({sym for sym, _ in sym_scores}) < min_symbols_per_theme:
            individual_groups += 1
            continue
        theme_id = await theme_repo.upsert_theme(theme_name, description="뉴스기반 자동발굴")
        if theme_id is None:
            continue
        themes_created += 1
        themes.append(theme_name)
        for keyword in theme_keywords.get(theme_name, [theme_name]):
            await theme_repo.add_keyword(theme_id, keyword)
        for sym, score in sym_scores:
            if await theme_repo.link_stock(theme_id, sym, score):
                links_created += 1

    return {
        "themes_created": themes_created,
        "links_created": links_created,
        "individual_groups": individual_groups,
        "themes": sorted(themes),
    }


async def discover_dynamic_themes(
    *,
    top_n: int = 100,
    min_value_traded_eok: float = 100.0,
    lookback_days: int = 7,
    keywords_per_symbol: int = 5,
    min_symbols_per_theme: int = 2,
    quotes: Optional[object] = None,
    exclude_already_themed: bool = False,
    analyst_backend: str = "auto",
    analyst_model: Optional[str] = None,
    analyst_timeout: float = 35.0,
) -> dict:
    """전체 파이프라인 1회 실행. 반환: 요약 통계 dict(status/candidates/themes_created 등)."""
    candidate_result = await discover_dynamic_theme_candidates(
        top_n=top_n,
        min_value_traded_eok=min_value_traded_eok,
        lookback_days=lookback_days,
        keywords_per_symbol=keywords_per_symbol,
        min_symbols_per_theme=min_symbols_per_theme,
        quotes=quotes,
        exclude_already_themed=exclude_already_themed,
        analyst_backend=analyst_backend,
        analyst_model=analyst_model,
        analyst_timeout=analyst_timeout,
    )
    if candidate_result["status"] != "ok":
        return {
            **candidate_result,
            "themes_created": 0,
            "links_created": 0,
            "themes": [],
        }
    persist_result = await persist_theme_groups(
        candidate_result["classified_groups"],
        theme_keywords=candidate_result["theme_keywords"],
        min_symbols_per_theme=min_symbols_per_theme,
    )

    logger.info(
        "테마 뉴스발굴 완료: candidates=%d news_matched=%d raw=%d classified=%d rejected=%d links=%d",
        candidate_result["unthemed_candidates"],
        candidate_result["symbols_with_news"],
        len(candidate_result["raw_groups"]),
        len(candidate_result["classified_groups"]),
        len(candidate_result["rejected_groups"]),
        persist_result["links_created"],
    )
    return {
        "status": "ok",
        "candidates": candidate_result["candidates"],
        "unthemed_candidates": candidate_result["unthemed_candidates"],
        "selected_candidates": candidate_result.get("selected_candidates", 0),
        "analyst_backend": candidate_result.get("analyst_backend", ""),
        "symbols_with_news": candidate_result["symbols_with_news"],
        "themes_created": persist_result["themes_created"],
        "links_created": persist_result["links_created"],
        "individual_groups": persist_result.get("individual_groups", 0),
        "themes": persist_result["themes"],
        "raw_themes": sorted(candidate_result["raw_groups"].keys()),
        "rejected_themes": candidate_result["rejected_groups"],
        "analyst_decisions": candidate_result.get("analyst_decisions", []),
    }


__all__ = [
    "build_candidate_universe",
    "match_articles_to_symbols",
    "extract_theme_groups",
    "classify_theme_keyword",
    "classify_theme_groups",
    "classify_theme_groups_with_analyst",
    "is_noise_theme_name",
    "discover_dynamic_theme_candidates",
    "persist_theme_groups",
    "discover_dynamic_themes",
]
