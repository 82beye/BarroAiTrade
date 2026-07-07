#!/usr/bin/env python3
"""뉴스기반 테마 자동분류/등록 CLI.

기본은 dry-run 이다. 실제 DB 반영은 --apply 를 명시했을 때만 수행한다.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from backend.core.themes.news_theme_discovery import (  # noqa: E402
    classify_theme_keyword,
    discover_dynamic_theme_candidates,
    is_noise_theme_name,
    persist_theme_groups,
)
from backend.db.database import get_db  # noqa: E402

AUTO_DESC = "뉴스기반 자동발굴"


def _symbols_label(rows: list[dict]) -> str:
    pairs = [
        f"{row.get('symbol')}:{float(row.get('score') or 0.0):.3f}"
        for row in rows[:8]
    ]
    suffix = "" if len(rows) <= 8 else f" ... +{len(rows) - 8}"
    return ", ".join(pairs) + suffix


def _merge_group(
    groups: dict[str, list[dict]],
    keywords: dict[str, list[str]],
    theme_name: str,
    raw_keyword: str,
    rows: list[dict],
) -> None:
    by_symbol = {
        str(row["symbol"]): float(row.get("score") or 0.0)
        for row in groups.get(theme_name, [])
        if row.get("symbol")
    }
    for row in rows:
        sym = str(row.get("symbol") or "").strip()
        if not sym:
            continue
        by_symbol[sym] = max(float(row.get("score") or 0.0), by_symbol.get(sym, 0.0))
    groups[theme_name] = [
        {"symbol": sym, "score": score}
        for sym, score in sorted(by_symbol.items(), key=lambda item: item[1], reverse=True)
    ]
    merged_keywords = set(keywords.get(theme_name, []))
    merged_keywords.add(raw_keyword)
    keywords[theme_name] = sorted(merged_keywords)


def _review_groups(result: dict) -> tuple[dict[str, list[dict]], dict[str, list[str]]]:
    accepted_groups: dict[str, list[dict]] = {}
    accepted_keywords: dict[str, list[str]] = {}

    for raw_keyword, rows in result["raw_groups"].items():
        canonical, reason = classify_theme_keyword(raw_keyword)
        members = _symbols_label(rows)
        if canonical:
            prompt = (
                f"{raw_keyword} (추천:{canonical}) [{members}] "
                "[Enter=키워드명 등록, s=추천테마, r=거절, 직접입력=테마명]: "
            )
        else:
            prompt = (
                f"{raw_keyword} ({reason}) [{members}] "
                "[Enter=키워드명 등록, r=거절, 직접입력=테마명]: "
            )

        answer = input(prompt).strip()
        if answer.lower() in {"r", "reject", "skip", "n", "no"}:
            continue
        if answer.lower() in {"s", "suggest", "추천"} and canonical:
            theme_name = canonical
        else:
            theme_name = raw_keyword if not answer else answer
        _merge_group(accepted_groups, accepted_keywords, theme_name, raw_keyword, rows)

    return accepted_groups, accepted_keywords


async def _cleanup_invalid_auto_themes(*, apply: bool) -> list[dict]:
    """기존 자동발굴 행 중 명백한 noise 행을 정리한다."""
    async with get_db() as db:
        res = await db.execute(
            text("SELECT id, name FROM themes WHERE description = :desc ORDER BY id"),
            {"desc": AUTO_DESC},
        )
        rows = [dict(row) for row in res.mappings().all()]

        invalid: list[dict] = []
        for row in rows:
            is_noise, reason = is_noise_theme_name(str(row["name"]))
            if is_noise:
                invalid.append({"id": int(row["id"]), "name": row["name"], "reason": reason})

        if apply:
            for row in invalid:
                await db.execute(
                    text("DELETE FROM theme_stocks WHERE theme_id = :id"),
                    {"id": row["id"]},
                )
                await db.execute(
                    text("DELETE FROM theme_keywords WHERE theme_id = :id"),
                    {"id": row["id"]},
                )
                await db.execute(
                    text("DELETE FROM themes WHERE id = :id"),
                    {"id": row["id"]},
                )
    return invalid


def _print_candidate_summary(result: dict) -> None:
    print(
        "후보:",
        f"ranked={result['candidates']}",
        f"selected={result.get('selected_candidates', result['unthemed_candidates'])}",
        f"unthemed={result['unthemed_candidates']}",
        f"news_matched={result['symbols_with_news']}",
        f"exclude_themed={result.get('exclude_already_themed', True)}",
    )

    if result["raw_groups"]:
        print("\n[중복 키워드 후보]")
        for keyword, rows in result["raw_groups"].items():
            canonical, reason = classify_theme_keyword(keyword)
            hint = f"추천:{canonical}" if canonical else reason
            print(f"- {keyword}: {hint} ({_symbols_label(rows)})")
    else:
        print("\n중복 키워드 후보 없음")

    if result.get("classified_groups"):
        print(f"\n[자동 분류 결과: {result.get('analyst_backend', '-')}]")
        for theme_name, rows in result["classified_groups"].items():
            kws = ", ".join(result.get("theme_keywords", {}).get(theme_name, []))
            print(f"- {theme_name} <= {kws} ({_symbols_label(rows)})")

    if result.get("rejected_groups"):
        print("\n[자동 제외]")
        for keyword, reason in result["rejected_groups"].items():
            print(f"- {keyword}: {reason}")


async def _run(args: argparse.Namespace) -> int:
    if args.cleanup_invalid_auto:
        invalid = await _cleanup_invalid_auto_themes(apply=args.apply)
        action = "삭제" if args.apply else "삭제 예정"
        print(f"기존 자동발굴 invalid 테마 {len(invalid)}건 {action}")
        for row in invalid:
            print(f"- #{row['id']} {row['name']} ({row['reason']})")
        if args.cleanup_only:
            return 0

    result = await discover_dynamic_theme_candidates(
        top_n=args.top_n,
        min_value_traded_eok=args.min_value_traded_eok,
        lookback_days=args.lookback_days,
        keywords_per_symbol=args.keywords_per_symbol,
        min_symbols_per_theme=args.min_symbols_per_theme,
        exclude_already_themed=args.exclude_themed,
        analyst_backend=args.analyst_backend,
        analyst_model=args.analyst_model,
        analyst_timeout=args.analyst_timeout,
    )
    if result["status"] != "ok":
        print(f"테마 후보 산출 불가: status={result['status']}")
        return 0

    _print_candidate_summary(result)

    if args.review:
        groups, keywords = _review_groups(result)
    else:
        groups = result["classified_groups"]
        keywords = result["theme_keywords"]

    if not args.apply:
        if args.review:
            names = ", ".join(sorted(groups)) or "-"
            print(f"\ndry-run: 등록 예정 테마 {len(groups)}건 ({names}). 실제 반영하려면 --apply 사용.")
        else:
            names = ", ".join(sorted(groups)) or "-"
            print(f"\ndry-run: 자동 등록 예정 테마 {len(groups)}건 ({names}). 실제 반영하려면 --apply 사용.")
        return 0

    persisted = await persist_theme_groups(groups, theme_keywords=keywords)
    print(
        "\n등록 완료:",
        f"themes={persisted['themes_created']}",
        f"links={persisted['links_created']}",
        f"names={', '.join(persisted['themes'])}",
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="뉴스기반 테마 자동분류/등록 CLI")
    ap.add_argument("--top-n", type=int, default=100, help="등락률/거래대금 각각 조회할 상위 N")
    ap.add_argument("--min-value-traded-eok", type=float, default=100.0)
    ap.add_argument("--lookback-days", type=int, default=7)
    ap.add_argument("--keywords-per-symbol", type=int, default=5)
    ap.add_argument("--min-symbols-per-theme", type=int, default=2)
    ap.add_argument(
        "--exclude-themed",
        action="store_true",
        help="이미 테마가 있는 종목은 후보에서 제외한다. 기본은 당일 수급 포착을 위해 포함.",
    )
    ap.add_argument(
        "--analyst-backend",
        default="auto",
        choices=["auto", "claude-cli", "claude", "rules", "taxonomy"],
        help="테마 자동분류 백엔드. auto=claude-cli 가능 시 사용, 실패 시 rules.",
    )
    ap.add_argument("--analyst-model", default=None, help="claude-cli --model 값")
    ap.add_argument("--analyst-timeout", type=float, default=35.0)
    ap.add_argument("--review", action="store_true", help="자동분류 결과를 수동으로 덮어쓰기")
    ap.add_argument("--apply", action="store_true", help="DB에 실제 등록/삭제 반영")
    ap.add_argument(
        "--cleanup-invalid-auto",
        action="store_true",
        help="기존 뉴스기반 자동발굴 중 taxonomy 미분류 테마 정리",
    )
    ap.add_argument(
        "--cleanup-only",
        action="store_true",
        help="cleanup-invalid-auto 만 수행하고 신규 후보 발굴은 생략",
    )
    return ap


def main() -> int:
    return asyncio.run(_run(_build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
