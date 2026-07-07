#!/usr/bin/env python3
"""Collect Finup theme-log baseline data.

The public Finup theme-log page renders its data from a small set of JSON
endpoints. This collector keeps the crawl intentionally narrow and sequential:
theme ranking snapshot, detail summary, related stocks, similar themes, news,
and focus contents for the visible top themes.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://finance.finup.co.kr"
THEMELOG_URL = f"{BASE_URL}/lab/themelog"


def to_number(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def text(value: Any) -> str:
    return "" if value is None else str(value)


def type_stock_market(value: Any) -> str:
    code = text(value)
    if code == "1":
        return "kospi"
    if code == "2":
        return "kosdaq"
    return ""


class FinupClient:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        referer: str = THEMELOG_URL,
    ) -> Any:
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        body: bytes | None = None
        headers = {
            "accept": "application/json, text/plain, */*",
            "referer": referer,
            "user-agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
            ),
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["content-type"] = "application/json; charset=utf-8"
            headers["origin"] = BASE_URL
            method = "POST"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raise RuntimeError(f"{method} {url} failed: HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"{method} {url} failed: {exc.reason}") from exc
        return json.loads(raw) if raw else None

    def capture_chart(self, capture_idx: int, top: int) -> list[dict[str, Any]]:
        data = self.request_json(
            "/api/radar/themelog/capture-chart",
            payload={"captureIdx": capture_idx, "top": top},
        )
        return data if isinstance(data, list) else []

    def theme_summary(self, keyword_idx: str) -> dict[str, Any]:
        data = self.request_json(
            f"/api/radar/theme/summary?keywordIdx={quote(keyword_idx)}",
            referer=f"{BASE_URL}/theme/{quote(keyword_idx)}",
        )
        rows = data.get("resultData") if isinstance(data, dict) else None
        return rows[0] if isinstance(rows, list) and rows else {}

    def relation_stocks(self, keyword_idx: str) -> list[dict[str, Any]]:
        data = self.request_json(
            f"/api/radar/theme/relation-stocks?keywordIdx={quote(keyword_idx)}",
            referer=f"{BASE_URL}/theme/{quote(keyword_idx)}",
        )
        rows = data.get("resultData") if isinstance(data, dict) else None
        return rows if isinstance(rows, list) else []

    def similar_themes(self, keyword_idx: str, count: int) -> list[dict[str, Any]]:
        query = urlencode({"keywordIdx": keyword_idx, "count": count})
        data = self.request_json(
            f"/api/radar/theme/similarity?{query}",
            referer=f"{BASE_URL}/theme/{quote(keyword_idx)}",
        )
        rows = data.get("resultData") if isinstance(data, dict) else None
        return rows if isinstance(rows, list) else []

    def theme_news(self, keyword_idx: str, page_size: int) -> list[dict[str, Any]]:
        data = self.request_json(
            "/api/radar/themelog/news",
            payload={
                "keywordIdx": int(keyword_idx),
                "pageNo": 1,
                "pageSize": page_size,
                "stockCode": "",
            },
            referer=THEMELOG_URL,
        )
        rows = data.get("news") if isinstance(data, dict) else None
        return rows if isinstance(rows, list) else []

    def focus_contents(self, keyword_idx: str, page_size: int) -> list[dict[str, Any]]:
        query = urlencode({"keywordIdx": keyword_idx, "page": 1, "pageSize": page_size})
        data = self.request_json(
            f"/api/finance/contents?{query}",
            referer=f"{BASE_URL}/theme/{quote(keyword_idx)}",
        )
        container = data.get("data") if isinstance(data, dict) else None
        rows = container.get("items") if isinstance(container, dict) else None
        return rows if isinstance(rows, list) else []


def normalize_theme(
    capture_row: dict[str, Any],
    summary: dict[str, Any],
    collected_at: str,
) -> dict[str, Any]:
    keyword_idx = text(capture_row.get("keywordIdx") or summary.get("keywordIdx"))
    return {
        "collected_at": collected_at,
        "theme_id": keyword_idx,
        "theme_name": text(capture_row.get("keyword") or summary.get("keyword")),
        "rank": to_number(capture_row.get("rank") or summary.get("rankTheme")),
        "capture_idx": None,
        "capture_item_idx": text(capture_row.get("captureItemIdx")),
        "capture_dt": text(capture_row.get("captureDT")),
        "score": to_number(capture_row.get("score")),
        "percentage_weight": to_number(capture_row.get("percentage")),
        "avg_diff_pct": to_number(capture_row.get("diff") or summary.get("averageDiff")),
        "new_flag": to_number(capture_row.get("new")),
        "hot_flag": to_number(capture_row.get("hot")),
        "summary_rank": to_number(summary.get("rankTheme")),
        "description": text(summary.get("description")),
        "max_stock_diff_pct": to_number(summary.get("maxStockDiff")),
        "min_stock_diff_pct": to_number(summary.get("minStockDiff")),
        "sum_trade_value": to_number(summary.get("sumTranCost")),
        "top5_trade_value": to_number(summary.get("tranCostTop5")),
        "type_stock_name": text(summary.get("typeStockName")),
        "business_name": text(summary.get("businessName")),
        "theme_url": f"{BASE_URL}/theme/{keyword_idx}",
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def collect(args: argparse.Namespace) -> dict[str, Any]:
    client = FinupClient(timeout=args.timeout)
    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    capture_rows = client.capture_chart(args.capture_idx, args.top)
    output: dict[str, Any] = {
        "metadata": {
            "source": THEMELOG_URL,
            "collected_at": collected_at,
            "capture_idx": args.capture_idx,
            "requested_top": args.top,
            "theme_count": len(capture_rows),
            "news_page_size": args.news_page_size,
            "similar_count": args.similar_count,
            "focus_page_size": args.focus_page_size,
        },
        "themes": [],
    }

    def optional(label: str, fallback: Any, func: Any) -> tuple[Any, str | None]:
        try:
            return func(), None
        except Exception as exc:  # noqa: BLE001 - keep crawl narrow but resilient.
            return fallback, f"{label}: {exc}"

    for idx, row in enumerate(capture_rows, start=1):
        keyword_idx = text(row.get("keywordIdx"))
        print(f"[{idx}/{len(capture_rows)}] {keyword_idx} {row.get('keyword')}", file=sys.stderr)
        errors: list[str] = []
        summary, error = optional("summary", {}, lambda: client.theme_summary(keyword_idx))
        if error:
            errors.append(error)
        time.sleep(args.sleep)
        stocks, error = optional("relation_stocks", [], lambda: client.relation_stocks(keyword_idx))
        if error:
            errors.append(error)
        time.sleep(args.sleep)
        similar, error = optional(
            "similar_themes",
            [],
            lambda: client.similar_themes(keyword_idx, args.similar_count),
        )
        if error:
            errors.append(error)
        time.sleep(args.sleep)
        news, error = optional("theme_news", [], lambda: client.theme_news(keyword_idx, args.news_page_size))
        if error:
            errors.append(error)
        time.sleep(args.sleep)
        focus, error = optional(
            "focus_contents",
            [],
            lambda: client.focus_contents(keyword_idx, args.focus_page_size),
        )
        if error:
            errors.append(error)
        time.sleep(args.sleep)

        theme = normalize_theme(row, summary, collected_at)
        theme["capture_idx"] = args.capture_idx
        output["themes"].append(
            {
                "theme": theme,
                "raw_capture": row,
                "raw_summary": summary,
                "relation_stocks": stocks,
                "similar_themes": similar,
                "news": news,
                "focus_contents": focus,
                "errors": errors,
            }
        )
    return output


def flatten_and_write(dataset: dict[str, Any], out_dir: Path, stamp: str) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"finup_theme_snapshot_{stamp}.json"
    json_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")

    theme_rows: list[dict[str, Any]] = []
    stock_rows: list[dict[str, Any]] = []
    similar_rows: list[dict[str, Any]] = []
    news_rows: list[dict[str, Any]] = []
    focus_rows: list[dict[str, Any]] = []

    for item in dataset["themes"]:
        theme = item["theme"]
        theme_rows.append(theme)
        theme_id = theme["theme_id"]
        theme_name = theme["theme_name"]

        for order, stock in enumerate(item["relation_stocks"], start=1):
            stock_rows.append(
                {
                    "theme_id": theme_id,
                    "theme_name": theme_name,
                    "stock_order": order,
                    "stock_keyword_idx": text(stock.get("keywordIdx")),
                    "stock_code": text(stock.get("stockCode")),
                    "stock_name": text(stock.get("keyword")),
                    "type_stock": to_number(stock.get("typeStock")),
                    "type_stock_market_inferred": type_stock_market(stock.get("typeStock")),
                    "diff_pct": to_number(stock.get("diff")),
                    "price": to_number(stock.get("price")),
                    "volume": to_number(stock.get("volume")),
                    "trade_value": to_number(stock.get("valueSum")),
                    "news_count": to_number(stock.get("countNews")),
                    "description": text(stock.get("description")),
                }
            )

        for order, similar in enumerate(item["similar_themes"], start=1):
            count_target = to_number(similar.get("countStockTarget")) or 0
            similarity_stock = to_number(similar.get("similarityStock")) or 0
            similar_rows.append(
                {
                    "theme_id": theme_id,
                    "theme_name": theme_name,
                    "similar_order": order,
                    "similar_theme_id": text(similar.get("keywordIdx")),
                    "similar_theme_name": text(similar.get("keyword")),
                    "type_keyword": to_number(similar.get("typeKeyword")),
                    "similarity_stock": similarity_stock,
                    "count_stock_similarity": to_number(similar.get("countStockSimilarity")),
                    "count_stock_target": count_target,
                    "similarity_pct_shown": round(similarity_stock / count_target * 100, 2)
                    if count_target
                    else None,
                }
            )

        for order, news in enumerate(item["news"], start=1):
            news_rows.append(
                {
                    "theme_id": theme_id,
                    "theme_name": theme_name,
                    "news_order": order,
                    "publish_dt": text(news.get("publishDT")),
                    "date_diff": text(news.get("dateDiff")),
                    "media_name": text(news.get("mediaName")),
                    "title": text(news.get("title")),
                    "url": text(news.get("url")),
                    "thumbnail": text(news.get("thumbnail")),
                    "summary": text(news.get("summary")),
                    "category": text(news.get("category")),
                }
            )

        for order, focus in enumerate(item["focus_contents"], start=1):
            focus_rows.append(
                {
                    "theme_id": theme_id,
                    "theme_name": theme_name,
                    "content_order": order,
                    "content_idx": text(focus.get("contentIdx")),
                    "parent_category_idx": text(focus.get("parentCategoryIdx")),
                    "title": text(focus.get("title")),
                    "summary": text(focus.get("summary")),
                    "image_url": text(focus.get("imageUrl")),
                    "reg_dt": text(focus.get("regDT")),
                    "view_count": to_number(focus.get("viewCnt")),
                    "content_url": f"{BASE_URL}/content/{text(focus.get('contentIdx'))}",
                }
            )

    paths = {
        "json": json_path,
        "themes_csv": out_dir / f"finup_themes_{stamp}.csv",
        "stocks_csv": out_dir / f"finup_theme_stocks_{stamp}.csv",
        "similar_csv": out_dir / f"finup_similar_themes_{stamp}.csv",
        "news_csv": out_dir / f"finup_theme_news_{stamp}.csv",
        "focus_csv": out_dir / f"finup_theme_focus_contents_{stamp}.csv",
    }
    write_csv(paths["themes_csv"], theme_rows, list(theme_rows[0].keys()) if theme_rows else [])
    write_csv(paths["stocks_csv"], stock_rows, list(stock_rows[0].keys()) if stock_rows else [])
    write_csv(paths["similar_csv"], similar_rows, list(similar_rows[0].keys()) if similar_rows else [])
    write_csv(paths["news_csv"], news_rows, list(news_rows[0].keys()) if news_rows else [])
    write_csv(paths["focus_csv"], focus_rows, list(focus_rows[0].keys()) if focus_rows else [])

    latest_path = out_dir / "latest.json"
    latest_path.write_text(
        json.dumps({key: str(path) for key, path in paths.items()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths["latest"] = latest_path
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-idx", type=int, default=10)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--news-page-size", type=int, default=5)
    parser.add_argument("--similar-count", type=int, default=4)
    parser.add_argument("--focus-page-size", type=int, default=2)
    parser.add_argument("--sleep", type=float, default=0.15)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--out-dir", type=Path, default=Path("data/finup_theme"))
    parser.add_argument(
        "--stamp",
        default=datetime.now().strftime("%Y%m%d_%H%M%S"),
        help="Output filename timestamp. Defaults to local current time.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dataset = collect(args)
    paths = flatten_and_write(dataset, args.out_dir, args.stamp)
    print(json.dumps({key: str(path) for key, path in paths.items()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
