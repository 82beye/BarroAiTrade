"""시간대별 테마 스냅숏(타임라인) — 동결 저장/조회 (티마 앱 벤치마킹 P1).

PRD §3.2: 장중 3개 고정 시점(10:00 / 12:30 / 15:35)의 테마 보드를 동결하여
오전 주도주 → 오후 주도주 로테이션을 시점별로 비교한다.

저장 구조:
  data/theme_snapshots/{YYYY-MM-DD}/{slot}.json
  { date, slot, captured_at, themes: [{id, name, description, stocks: [ThemeStockOut]}] }

운영 배선(후속): 데몬 스케줄러가 10:00/12:30/15:35 KST 에 capture_theme_snapshot(slot)
을 호출하도록 배선한다. 현재는 API POST /api/themes/snapshots/capture 로 수동 트리거.
gateway 미초기화 시 시세 필드는 null 인 채로 동결된다(날조 금지).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# 장중 고정 스냅숏 시점 (PRD §3.2)
VALID_SLOTS = ("10:00", "12:30", "15:35")


def _snapshots_dir() -> Path:
    """repo_root/data/theme_snapshots (snapshot.py → parents[3] == repo root)."""
    return Path(__file__).resolve().parents[3] / "data" / "theme_snapshots"


def _snapshot_path(date_str: str, slot: str) -> Path:
    return _snapshots_dir() / date_str / f"{slot}.json"


def is_valid_slot(slot: str) -> bool:
    return slot in VALID_SLOTS


def _today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def capture_theme_snapshot(slot: str, date_str: Optional[str] = None) -> dict:
    """현재 테마 보드(+종목 시세 enrich)를 동결하여 파일로 저장한다.

    Args:
        slot: "10:00" | "12:30" | "15:35".
        date_str: 저장 날짜 (YYYY-MM-DD). 미지정 시 오늘(UTC).

    Returns:
        저장된 스냅숏 dict.

    Raises:
        ValueError: slot 이 VALID_SLOTS 밖일 때.
    """
    if not is_valid_slot(slot):
        raise ValueError(f"invalid slot: {slot} (허용: {VALID_SLOTS})")

    # 지연 import — 라우트 ↔ 스냅숏 순환참조 방지.
    from backend.api.routes.themes_calendar_news import (
        fetch_theme_stocks,
        fetch_themes,
    )

    date_str = date_str or _today_str()
    captured_at = datetime.now(timezone.utc).isoformat()

    themes = await fetch_themes()
    theme_blocks: List[dict] = []
    for th in themes:
        stocks = await fetch_theme_stocks(th.id, enrich=True) or []
        theme_blocks.append(
            {
                "id": th.id,
                "name": th.name,
                "description": th.description,
                "stocks": [s.model_dump() for s in stocks],
            }
        )

    snapshot = {
        "date": date_str,
        "slot": slot,
        "captured_at": captured_at,
        "themes": theme_blocks,
    }

    path = _snapshot_path(date_str, slot)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    logger.info("테마 스냅숏 저장: %s/%s (themes=%d)", date_str, slot, len(theme_blocks))
    return snapshot


def load_theme_snapshot(date_str: str, slot: str) -> Optional[dict]:
    """지정 날짜·slot 스냅숏 로드 (없으면 None)."""
    path = _snapshot_path(date_str, slot)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("스냅숏 읽기 실패 %s/%s: %s", date_str, slot, e)
        return None


def list_available_slots(date_str: str) -> List[str]:
    """지정 날짜에 저장된 slot 목록 (VALID_SLOTS 순서 유지)."""
    day_dir = _snapshots_dir() / date_str
    if not day_dir.is_dir():
        return []
    present = {p.stem for p in day_dir.glob("*.json")}
    return [s for s in VALID_SLOTS if s in present]


__all__ = [
    "VALID_SLOTS",
    "is_valid_slot",
    "capture_theme_snapshot",
    "load_theme_snapshot",
    "list_available_slots",
]
