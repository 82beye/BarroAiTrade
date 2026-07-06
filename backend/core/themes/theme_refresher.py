"""테마 보드 라이브 갱신 — 큐레이션 시드(theme_map.json)를 시세로 재적재.

정직성(★ 중요 ★):
    테마 그룹 자체는 `data/theme_map.json` 큐레이션 값(고정, 유지보수 대상)이다.
    이 파이프라인이 갱신하는 것은 **종목별 스코어(등락률)** 뿐이다 — 원 티마 앱처럼
    뉴스를 실시간으로 재분류해 테마 구성을 바꾸는 것이 아니다(그건 별도 대형 인프라).
    스코어는 캐시/거래소의 실제 관측 등락률을 그대로 쓴다. 시세를 구하지 못한 종목은
    스코어 0.0 으로 링크(날조 금지 — "모른다"를 0 으로 표기하고 링크는 유지).

데이터 흐름:
    theme_map.json {symbol: [themes]}  ──invert──▶  {theme: [symbols]}
        └ ThemeRepository.upsert_theme(theme)      → theme_id (존재 시 재사용)
        └ cache_quotes.get_quote(symbol).change_pct → score
        └ ThemeRepository.link_stock(theme_id, symbol, score)  (upsert)

안전:
    시세는 조회 전용(cache_quotes.get_quote — 읽기 전용 OHLCV 캐시)만 쓴다. 주문/게이트웨이
    쓰기 경로와 무관. 종목/테마 단위 예외는 삼켜 로깅만 하여 한 종목 실패가 전체 갱신을
    막지 않는다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.core.market_data import cache_quotes
from backend.core.risk.theme_map import load_theme_map
from backend.db.repositories.theme_repo import ThemeRepository

logger = logging.getLogger(__name__)

# repo_root/data/theme_map.json — 이 파일: backend/core/themes/theme_refresher.py
#   parents[0]=themes parents[1]=core parents[2]=backend parents[3]=repo_root
_DEFAULT_SEED = Path(__file__).resolve().parents[3] / "data" / "theme_map.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def refresh_themes_from_seed(seed_path: Optional[Path] = None) -> dict:
    """큐레이션 시드를 읽어 themes/theme_stocks 를 시세 스코어로 재적재.

    Args:
        seed_path: theme_map.json 경로. None 이면 repo_root/data/theme_map.json.

    Returns:
        {"theme_count": int, "symbol_count": int, "status": str, "refreshed_at": ISO8601}
          · status="ok"      — 정상 갱신 (theme_count=upsert된 테마 수, symbol_count=링크된
                               고유 종목 수).
          · status="no_seed" — 시드 파일 부재/빈 매핑 (예외 없이 카운트 0).
    """
    seed = Path(seed_path) if seed_path is not None else _DEFAULT_SEED

    # {symbol: [themes]} — 부재/오류 시 {} (load_theme_map 이 fail-safe 처리).
    theme_map = load_theme_map(seed)
    if not theme_map:
        logger.info("테마 시드 없음/빈 매핑 — 갱신 생략: %s", seed)
        return {
            "theme_count": 0,
            "symbol_count": 0,
            "status": "no_seed",
            "refreshed_at": _now_iso(),
        }

    # 역전: {theme: [symbols]} (한 종목이 복수 테마에 속하면 각 테마에 등장).
    by_theme: dict[str, list[str]] = {}
    for symbol, themes in theme_map.items():
        for theme_name in themes:
            by_theme.setdefault(theme_name, []).append(symbol)

    repo = ThemeRepository()
    theme_count = 0
    linked_symbols: set[str] = set()

    for theme_name, symbols in by_theme.items():
        try:
            theme_id = await repo.upsert_theme(
                theme_name, description=f"{theme_name} 테마 (큐레이션 시드 기반)"
            )
        except Exception:  # 테마 단위 실패는 삼켜 로깅만 — 다음 테마 계속
            logger.warning("테마 upsert 실패: %s", theme_name, exc_info=True)
            continue
        if theme_id is None:
            logger.warning("테마 upsert None 반환(DB 미가용?): %s", theme_name)
            continue
        theme_count += 1

        for symbol in symbols:
            try:
                # 조회 전용 지연 시세 — 실패/None 이면 스코어 0.0(날조 금지, 링크는 유지).
                quote = cache_quotes.get_quote(symbol)
                score = (
                    round(float(quote.get("change_pct") or 0.0), 4)
                    if quote
                    else 0.0
                )
                if await repo.link_stock(theme_id, symbol, score):
                    linked_symbols.add(symbol)
            except Exception:  # 종목 단위 실패는 삼켜 로깅만 — 다음 종목 계속
                logger.warning(
                    "테마 종목 링크 실패: theme=%s symbol=%s",
                    theme_name,
                    symbol,
                    exc_info=True,
                )
                continue

    result = {
        "theme_count": theme_count,
        "symbol_count": len(linked_symbols),
        "status": "ok",
        "refreshed_at": _now_iso(),
    }
    logger.info("테마 시드 갱신 완료: %s", result)
    return result


__all__ = ["refresh_themes_from_seed"]
