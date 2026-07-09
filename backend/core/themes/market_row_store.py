"""Kiwoom ranking rows -> CSV -> theme aggregates.

테마보드의 실시간 통계는 DB theme_stocks.score 를 바로 덮어쓰기 전에 원천 row 를
CSV 로 남기는 단계를 둔다. 입력 원천은 키움 REST 랭킹 TR:

- ka10032 거래대금상위
- ka10027 전일대비등락률상위(상승/하락)

저장 파일은 운영 중 사람이 열어 검증할 수 있는 CSV 를 우선 진실원천으로 삼고,
테마 집계도 같은 CSV row 를 기준으로 만든다.
"""
from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import text

from backend.db.database import get_db

logger = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DATA_DIR = _REPO_ROOT / "data"
_DEFAULT_DIR_NAME = "theme_market_rows"

VALID_FILTERS = {"value", "gainers", "losers"}
DEFAULT_FILTERS = ("value", "gainers", "losers")

MARKET_ROW_FIELDS = [
    "captured_at",
    "trade_date",
    "source",
    "source_rank",
    "symbol",
    "name",
    "price",
    "change_pct",
    "value_traded",
    "stex_tp",
    "mrkt_tp",
    "top_n",
]

THEME_AGG_FIELDS = [
    "captured_at",
    "trade_date",
    "rank_by_value",
    "rank_by_change",
    "theme_id",
    "theme_name",
    "stock_count",
    "matched_count",
    "avg_change_pct",
    "value_weighted_change_pct",
    "sum_value_traded",
    "top_value_traded",
    "max_change_pct",
    "min_change_pct",
    "positive_count",
    "negative_count",
    "top_symbols",
]


def data_dir() -> Path:
    env = os.environ.get("BARRO_DATA_DIR", "").strip()
    return Path(env) if env else _DEFAULT_DATA_DIR


def market_rows_dir() -> Path:
    env = os.environ.get("BARRO_THEME_MARKET_ROWS_DIR", "").strip()
    return Path(env) if env else data_dir() / _DEFAULT_DIR_NAME


def normalize_filters(filters: str | Sequence[str] | None) -> list[str]:
    if filters is None:
        parts = list(DEFAULT_FILTERS)
    elif isinstance(filters, str):
        parts = [p.strip() for p in filters.split(",") if p.strip()]
    else:
        parts = [str(p).strip() for p in filters if str(p).strip()]
    out: list[str] = []
    for part in parts:
        if part not in VALID_FILTERS:
            raise ValueError(f"invalid ranking filter: {part}")
        if part not in out:
            out.append(part)
    return out or list(DEFAULT_FILTERS)


def _now_kst() -> datetime:
    return datetime.now(_KST)


def _fmt_num(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f.is_integer():
        return str(int(f))
    return f"{f:.6f}".rstrip("0").rstrip(".")


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _fmt_num(row.get(k)) for k in fieldnames})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_csv_rows(path: Path, *, limit: int | None = None) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    return rows[:limit] if limit is not None else rows


def latest_meta() -> dict[str, Any] | None:
    path = market_rows_dir() / "latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("theme market latest.json 읽기 실패", exc_info=True)
        return None


def latest_rows(*, limit: int | None = None) -> list[dict[str, str]]:
    meta = latest_meta() or {}
    path = meta.get("rows_csv")
    return read_csv_rows(Path(path), limit=limit) if path else []


def latest_aggregates(*, limit: int | None = None) -> list[dict[str, str]]:
    meta = latest_meta() or {}
    path = meta.get("aggregates_csv")
    return read_csv_rows(Path(path), limit=limit) if path else []


def _get_quotes():
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
    except Exception:
        logger.warning("theme market rows quotes 초기화 실패", exc_info=True)
        return None


async def fetch_ranking_rows(
    *,
    quotes=None,
    top_n: int = 100,
    filters: str | Sequence[str] | None = None,
    stex_tp: str = "3",
    mrkt_tp: str = "000",
    captured_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """키움 랭킹 row 를 공통 CSV 스키마로 정규화한다."""
    normalized_filters = normalize_filters(filters)
    quotes = quotes or _get_quotes()
    if quotes is None:
        return []

    captured = captured_at or _now_kst()
    captured_iso = captured.isoformat()
    trade_date = captured.date().isoformat()
    rows: list[dict[str, Any]] = []
    for source in normalized_filters:
        source_rows = await quotes.ranking(
            filter=source,
            stex_tp=stex_tp,
            mrkt_tp=mrkt_tp,
            limit=top_n,
        ) or []
        for idx, row in enumerate(source_rows, start=1):
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                continue
            rows.append(
                {
                    "captured_at": captured_iso,
                    "trade_date": trade_date,
                    "source": source,
                    "source_rank": idx,
                    "symbol": symbol,
                    "name": row.get("name") or "",
                    "price": row.get("price"),
                    "change_pct": row.get("change_pct"),
                    "value_traded": row.get("value_traded"),
                    "stex_tp": stex_tp,
                    "mrkt_tp": mrkt_tp,
                    "top_n": top_n,
                }
            )
    return rows


def merge_symbol_rows(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """중복 랭킹 row 를 심볼 단위로 합친다. 소스별 rank 는 보존한다."""
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        item = merged.setdefault(
            symbol,
            {
                "symbol": symbol,
                "name": row.get("name") or symbol,
                "price": _to_float(row.get("price")),
                "change_pct": _to_float(row.get("change_pct")),
                "value_traded": _to_float(row.get("value_traded")),
                "sources": set(),
                "value_rank": None,
                "gainers_rank": None,
                "losers_rank": None,
            },
        )
        source = str(row.get("source") or "")
        if source:
            item["sources"].add(source)
            rank_key = f"{source}_rank"
            if rank_key in item and item[rank_key] is None:
                item[rank_key] = int(_to_float(row.get("source_rank")) or 0) or None
        if not item.get("name") or item["name"] == symbol:
            item["name"] = row.get("name") or symbol
        price = _to_float(row.get("price"))
        change_pct = _to_float(row.get("change_pct"))
        value_traded = _to_float(row.get("value_traded"))
        if price is not None:
            item["price"] = price
        if change_pct is not None:
            item["change_pct"] = change_pct
        if value_traded is not None:
            current = item.get("value_traded")
            item["value_traded"] = value_traded if current is None else max(current, value_traded)
    for item in merged.values():
        item["sources"] = "|".join(sorted(item["sources"]))
    return merged


def aggregate_theme_memberships(
    rows: Sequence[dict[str, Any]],
    memberships: Sequence[dict[str, Any]],
    *,
    captured_at: str | None = None,
    trade_date: str | None = None,
) -> list[dict[str, Any]]:
    """랭킹 row 와 theme_stocks 매핑으로 테마별 집계를 계산한다."""
    symbol_rows = merge_symbol_rows(rows)
    if not memberships:
        return []

    themes: dict[int, dict[str, Any]] = {}
    for m in memberships:
        theme_id = int(m["theme_id"])
        theme = themes.setdefault(
            theme_id,
            {
                "theme_id": theme_id,
                "theme_name": m.get("theme_name") or "",
                "stock_count": 0,
                "_stocks": [],
            },
        )
        theme["stock_count"] += 1
        symbol = str(m.get("symbol") or "").strip()
        market_row = symbol_rows.get(symbol)
        if market_row:
            theme["_stocks"].append({**market_row, "score": _to_float(m.get("score"))})

    out: list[dict[str, Any]] = []
    for theme in themes.values():
        stocks = theme.pop("_stocks")
        if not stocks:
            continue
        changes = [s["change_pct"] for s in stocks if s.get("change_pct") is not None]
        values = [s["value_traded"] for s in stocks if s.get("value_traded") is not None]
        sum_value = sum(values) if values else None
        weighted = None
        weighted_stocks = [
            s
            for s in stocks
            if s.get("change_pct") is not None
            and s.get("value_traded") is not None
            and s["value_traded"] > 0
        ]
        weighted_value = sum(s["value_traded"] for s in weighted_stocks)
        if weighted_value > 0:
            weighted = (
                sum(s["change_pct"] * s["value_traded"] for s in weighted_stocks)
                / weighted_value
            )

        top_stocks = sorted(
            stocks,
            key=lambda s: (
                s.get("value_traded") is not None,
                s.get("value_traded") or 0.0,
                s.get("change_pct") or -999.0,
            ),
            reverse=True,
        )[:5]
        out.append(
            {
                "captured_at": captured_at or "",
                "trade_date": trade_date or "",
                "rank_by_value": "",
                "rank_by_change": "",
                "theme_id": theme["theme_id"],
                "theme_name": theme["theme_name"],
                "stock_count": theme["stock_count"],
                "matched_count": len(stocks),
                "avg_change_pct": round(sum(changes) / len(changes), 4) if changes else None,
                "value_weighted_change_pct": round(weighted, 4) if weighted is not None else None,
                "sum_value_traded": round(sum_value, 2) if sum_value is not None else None,
                "top_value_traded": round(max(values), 2) if values else None,
                "max_change_pct": round(max(changes), 4) if changes else None,
                "min_change_pct": round(min(changes), 4) if changes else None,
                "positive_count": sum(1 for v in changes if v > 0),
                "negative_count": sum(1 for v in changes if v < 0),
                "top_symbols": "|".join(
                    f"{s['symbol']}:{s.get('name') or ''}:{_fmt_num(s.get('change_pct'))}:{_fmt_num(s.get('value_traded'))}"
                    for s in top_stocks
                ),
            }
        )

    value_sorted = sorted(
        out,
        key=lambda r: (r.get("sum_value_traded") is not None, r.get("sum_value_traded") or 0.0),
        reverse=True,
    )
    for idx, row in enumerate(value_sorted, start=1):
        row["rank_by_value"] = idx
    change_sorted = sorted(
        out,
        key=lambda r: (r.get("avg_change_pct") is not None, r.get("avg_change_pct") or -999.0),
        reverse=True,
    )
    for idx, row in enumerate(change_sorted, start=1):
        row["rank_by_change"] = idx

    return value_sorted


async def load_theme_memberships() -> list[dict[str, Any]]:
    async with get_db() as db:
        if db is None:
            return []
        res = await db.execute(
            text(
                "SELECT t.id AS theme_id, t.name AS theme_name, ts.symbol, ts.score "
                "FROM theme_stocks ts JOIN themes t ON t.id = ts.theme_id"
            )
        )
        return [dict(r) for r in res.mappings().all()]


async def aggregate_theme_rows(
    rows: Sequence[dict[str, Any]],
    *,
    captured_at: str | None = None,
    trade_date: str | None = None,
) -> list[dict[str, Any]]:
    memberships = await load_theme_memberships()
    return aggregate_theme_memberships(
        rows,
        memberships,
        captured_at=captured_at,
        trade_date=trade_date,
    )


async def capture_theme_market_rows(
    *,
    top_n: int = 100,
    filters: str | Sequence[str] | None = None,
    stex_tp: str = "3",
    mrkt_tp: str = "000",
    quotes=None,
) -> dict[str, Any]:
    """랭킹 row CSV 와 테마 집계 CSV 를 생성하고 latest.json 을 갱신한다."""
    captured = _now_kst()
    rows = await fetch_ranking_rows(
        quotes=quotes,
        top_n=top_n,
        filters=filters,
        stex_tp=stex_tp,
        mrkt_tp=mrkt_tp,
        captured_at=captured,
    )
    captured_iso = captured.isoformat()
    trade_date = captured.date().isoformat()

    # 일시적인 인증/레이트리밋 실패로 빈 응답이 와도 마지막 정상 스냅숏은 보존한다.
    # 빈 CSV로 latest 포인터를 덮으면 프론트 정렬이 다음 성공 사이클까지 사라진다.
    if not rows:
        previous = latest_meta()
        return {
            "status": "no_rows",
            "captured_at": captured_iso,
            "trade_date": trade_date,
            "top_n": top_n,
            "filters": normalize_filters(filters),
            "stex_tp": stex_tp,
            "mrkt_tp": mrkt_tp,
            "row_count": 0,
            "symbol_count": 0,
            "aggregate_count": 0,
            "rows_csv": None,
            "aggregates_csv": None,
            "latest_preserved": previous is not None,
        }

    aggregates = await aggregate_theme_rows(
        rows,
        captured_at=captured_iso,
        trade_date=trade_date,
    )

    stamp = captured.strftime("%Y%m%d_%H%M%S")
    base = market_rows_dir() / trade_date
    rows_csv = base / f"theme_market_rows_{stamp}.csv"
    aggregates_csv = base / f"theme_market_aggregates_{stamp}.csv"
    latest_rows_csv = market_rows_dir() / "latest_rows.csv"
    latest_aggregates_csv = market_rows_dir() / "latest_aggregates.csv"

    _write_csv(rows_csv, MARKET_ROW_FIELDS, rows)
    _write_csv(aggregates_csv, THEME_AGG_FIELDS, aggregates)
    _write_csv(latest_rows_csv, MARKET_ROW_FIELDS, rows)
    _write_csv(latest_aggregates_csv, THEME_AGG_FIELDS, aggregates)

    payload = {
        "status": "ok",
        "captured_at": captured_iso,
        "trade_date": trade_date,
        "top_n": top_n,
        "filters": normalize_filters(filters),
        "stex_tp": stex_tp,
        "mrkt_tp": mrkt_tp,
        "row_count": len(rows),
        "symbol_count": len(merge_symbol_rows(rows)),
        "aggregate_count": len(aggregates),
        "rows_csv": str(rows_csv),
        "aggregates_csv": str(aggregates_csv),
        "latest_rows_csv": str(latest_rows_csv),
        "latest_aggregates_csv": str(latest_aggregates_csv),
        "latest_preserved": False,
    }
    _write_json(market_rows_dir() / "latest.json", payload)
    return payload


__all__ = [
    "MARKET_ROW_FIELDS",
    "THEME_AGG_FIELDS",
    "aggregate_theme_memberships",
    "capture_theme_market_rows",
    "fetch_ranking_rows",
    "latest_aggregates",
    "latest_meta",
    "latest_rows",
    "merge_symbol_rows",
    "normalize_filters",
]
