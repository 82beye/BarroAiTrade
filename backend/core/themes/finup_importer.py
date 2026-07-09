"""Import Finup theme crawler snapshots into the TIMA theme board tables."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text

from backend.core.market_data.stock_names import data_dir, load_names
from backend.db.database import get_db

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_LATEST = _REPO_ROOT / "data" / "finup_theme" / "latest.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_symbol(value: Any) -> str:
    raw = _clean_text(value)
    if not raw:
        return ""
    return raw.zfill(6) if raw.isdigit() else raw


def _resolve_snapshot_path(snapshot_path: Optional[Path] = None) -> Path:
    if snapshot_path is not None:
        return Path(snapshot_path)

    latest = _DEFAULT_LATEST
    with latest.open("r", encoding="utf-8") as f:
        data = json.load(f)
    json_path = Path(data["json"])
    if not json_path.is_absolute():
        json_path = _REPO_ROOT / json_path
    return json_path


def _load_snapshot(snapshot_path: Optional[Path] = None) -> tuple[Path, dict[str, Any]]:
    path = _resolve_snapshot_path(snapshot_path)
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("themes"), list):
        raise ValueError(f"invalid Finup theme snapshot: {path}")
    return path, data


def _extract_stock_names(snapshot: dict[str, Any]) -> dict[str, str]:
    names: dict[str, str] = {}
    for item in snapshot.get("themes") or []:
        for stock in item.get("relation_stocks") or []:
            symbol = _normalize_symbol(stock.get("stockCode"))
            name = _clean_text(stock.get("keyword"))
            if symbol and name:
                names.setdefault(symbol, name)
    return names


def _merge_stock_names(names: dict[str, str]) -> int:
    if not names:
        return 0

    path = data_dir() / "stock_names.json"
    try:
        with path.open("r", encoding="utf-8") as f:
            current = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        current = {}
    if not isinstance(current, dict):
        current = {}

    changed = 0
    for symbol, name in names.items():
        if current.get(symbol) != name:
            current[symbol] = name
            changed += 1

    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(dict(sorted(current.items())), f, ensure_ascii=False, indent=2)
            f.write("\n")
        load_names(force=True)

    return changed


# [2026-07-08 수정] Finup 재수입 시 다른 출처(큐레이션 시드·뉴스기반 자동발굴) 테마까지
# 전부 삭제되던 문제(docs/03-analysis/2026-07-08-theme-implementation-issues-and-fix-design.md
# §2-B) — 스키마 변경 없이 theme_keywords 를 출처 마커로 활용해 삭제 범위를 Finup
# 테마로만 한정한다. 이름이 다른 출처와 겹치면(예: 큐레이션의 "방산") 기존 테마의
# 설명과 소유권을 보존하고 구성 종목만 합친다.
_SOURCE_MARKER = "__source:finup__"


async def _finup_owned_theme_ids(db) -> list[int]:
    res = await db.execute(
        text("SELECT DISTINCT theme_id FROM theme_keywords WHERE keyword = :kw"),
        {"kw": _SOURCE_MARKER},
    )
    return [int(r["theme_id"]) for r in res.mappings().all()]


async def import_finup_theme_snapshot(
    snapshot_path: Optional[Path] = None,
    *,
    replace: bool = True,
    update_stock_names: bool = True,
) -> dict[str, Any]:
    """Load a crawler snapshot into `themes` and `theme_stocks`.

    The crawler snapshot uses `relation_stocks` for stock membership. `score` is
    populated from Finup's stock `diff` value so the frontend can use it as a
    fallback change percentage before live quote enrichment succeeds.

    `replace=True` clears only themes this importer previously created (tagged via
    a `theme_keywords` marker row) — curated-seed themes(theme_refresher.py) and
    news-discovered themes(news_theme_discovery.py) are left untouched.
    """
    path, snapshot = _load_snapshot(snapshot_path)
    metadata = snapshot.get("metadata") if isinstance(snapshot.get("metadata"), dict) else {}
    stock_names = _extract_stock_names(snapshot)
    names_changed = _merge_stock_names(stock_names) if update_stock_names else 0

    theme_count = 0
    stock_count = 0
    skipped_themes = 0
    now = _now_iso()

    async with get_db() as db:
        if db is None:
            return {
                "status": "no_db",
                "theme_count": 0,
                "stock_count": 0,
                "stock_names_changed": names_changed,
                "snapshot": str(path),
                "imported_at": now,
            }

        is_sqlite = db.engine.dialect.name == "sqlite"
        if replace:
            owned_ids = await _finup_owned_theme_ids(db)
            if owned_ids:
                placeholders = ", ".join(f":t{i}" for i in range(len(owned_ids)))
                params = {f"t{i}": tid for i, tid in enumerate(owned_ids)}
                await db.execute(
                    text(f"DELETE FROM theme_stocks WHERE theme_id IN ({placeholders})"), params
                )
                await db.execute(
                    text(f"DELETE FROM theme_keywords WHERE theme_id IN ({placeholders})"), params
                )
                await db.execute(text(f"DELETE FROM themes WHERE id IN ({placeholders})"), params)

        for item in snapshot.get("themes") or []:
            theme = item.get("theme") if isinstance(item, dict) else None
            if not isinstance(theme, dict):
                skipped_themes += 1
                continue

            name = _clean_text(theme.get("theme_name"))
            if not name:
                skipped_themes += 1
                continue

            description = _clean_text(theme.get("description"))
            if not description:
                description = f"{name} 테마 (Finup 크롤링 기반)"

            existing_res = await db.execute(
                text(
                    "SELECT t.id, t.description, "
                    "EXISTS(SELECT 1 FROM theme_keywords tk "
                    "WHERE tk.theme_id = t.id AND tk.keyword = :marker) AS finup_owned "
                    "FROM themes t WHERE t.name = :name"
                ),
                {"name": name, "marker": _SOURCE_MARKER},
            )
            existing = existing_res.mappings().first()
            preserve_existing = existing is not None and not bool(existing["finup_owned"])

            # 이름이 같은 큐레이션/뉴스 테마는 기존 설명과 소유권을 보존한다. 종목은
            # 합치되 Finup 마커를 붙이지 않아 다음 replace 에서 테마 전체가 삭제되지
            # 않게 한다. Finup 이 만들었거나 이미 소유한 테마만 설명을 갱신한다.
            if preserve_existing:
                theme_id = int(existing["id"])
            elif is_sqlite:
                await db.execute(
                    text(
                        "INSERT INTO themes (name, description, created_at) "
                        "VALUES (:name, :description, :created_at) "
                        "ON CONFLICT(name) DO UPDATE SET description = excluded.description"
                    ),
                    {"name": name, "description": description, "created_at": now},
                )
                res = await db.execute(
                    text("SELECT id FROM themes WHERE name = :name"), {"name": name}
                )
            else:
                res = await db.execute(
                    text(
                        "INSERT INTO themes (name, description) "
                        "VALUES (:name, :description) "
                        "ON CONFLICT(name) DO UPDATE SET description = EXCLUDED.description "
                        "RETURNING id"
                    ),
                    {"name": name, "description": description},
                )

            if not preserve_existing:
                row = res.mappings().first()
                if row is None:
                    skipped_themes += 1
                    continue
                theme_id = int(row["id"])
            theme_count += 1

            if not preserve_existing:
                kw_sql = (
                    text(
                        "INSERT OR IGNORE INTO theme_keywords (theme_id, keyword) "
                        "VALUES (:tid, :kw)"
                    )
                    if is_sqlite
                    else text(
                        "INSERT INTO theme_keywords (theme_id, keyword) VALUES (:tid, :kw) "
                        "ON CONFLICT (theme_id, keyword) DO NOTHING"
                    )
                )
                await db.execute(kw_sql, {"tid": theme_id, "kw": _SOURCE_MARKER})

            for stock in item.get("relation_stocks") or []:
                symbol = _normalize_symbol(stock.get("stockCode"))
                if not symbol:
                    continue
                score = _to_float(stock.get("diff"))
                if is_sqlite:
                    sql = text(
                        "INSERT OR REPLACE INTO theme_stocks (theme_id, symbol, score) "
                        "VALUES (:theme_id, :symbol, :score)"
                    )
                else:
                    sql = text(
                        "INSERT INTO theme_stocks (theme_id, symbol, score) "
                        "VALUES (:theme_id, :symbol, :score) "
                        "ON CONFLICT (theme_id, symbol) DO UPDATE SET score = EXCLUDED.score"
                    )
                await db.execute(sql, {"theme_id": theme_id, "symbol": symbol, "score": score})
                stock_count += 1

    result = {
        "status": "ok",
        "source": metadata.get("source") or "finup",
        "collected_at": metadata.get("collected_at"),
        "snapshot": str(path),
        "theme_count": theme_count,
        "stock_count": stock_count,
        "stock_names_changed": names_changed,
        "skipped_themes": skipped_themes,
        "replace": replace,
        "imported_at": now,
    }
    logger.info("Finup 테마 스냅숏 적재 완료: %s", result)
    return result


__all__ = ["import_finup_theme_snapshot"]
