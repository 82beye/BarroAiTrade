"""알림내역 / Push 설정 API 라우터 (티마 앱 벤치마킹 P1).

엔드포인트:
  GET /api/alerts/history?strategy=&limit=100  - 전략 시그널 알림내역 (최신순)
  GET /api/alerts/settings                      - Push 알림 토글 4종 조회
  PUT /api/alerts/settings                      - Push 알림 토글 부분 업데이트

데이터 소스:
  - 이벤트: data/alert_events.jsonl (운영 데몬·스캐너가 append — 후속 배선)
  - 설정  : data/alert_settings.json (원자적 쓰기: tmp → os.replace)
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Query

from backend.api.schemas.alerts import (
    AlertEventOut,
    AlertHistoryResponse,
    AlertSettings,
    AlertSettingsUpdate,
)
from backend.core.alerts.event_log import read_alert_events

logger = logging.getLogger(__name__)
router = APIRouter()


def _settings_path() -> Path:
    """repo_root/data/alert_settings.json (alerts.py → parents[3] == repo root)."""
    return Path(__file__).resolve().parents[3] / "data" / "alert_settings.json"


def _load_settings() -> AlertSettings:
    """설정 로드 (파일 없거나 파싱 실패 시 기본값 모두 ON)."""
    path = _settings_path()
    if not path.exists():
        return AlertSettings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return AlertSettings(**raw)
    except Exception as e:
        logger.warning("alert_settings.json 읽기 실패, 기본값 사용: %s", e)
        return AlertSettings()


def _save_settings(settings: AlertSettings) -> None:
    """설정 원자적 저장 (tmp 파일 write → os.replace)."""
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(settings.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


@router.get("/alerts/history", response_model=AlertHistoryResponse)
async def alert_history(
    strategy: str | None = Query(None, description="전략 필터 (미지정 시 전체)"),
    limit: int = Query(100, ge=1, le=1000, description="최대 이벤트 수"),
) -> AlertHistoryResponse:
    """전략 시그널 알림내역 — 최신순. 파일 없으면 status=no_data."""
    events = read_alert_events(strategy=strategy, limit=limit)
    items = [AlertEventOut(**e) for e in events]
    return AlertHistoryResponse(
        items=items,
        count=len(items),
        status="ok" if items else "no_data",
    )


@router.get("/alerts/settings", response_model=AlertSettings)
async def get_alert_settings() -> AlertSettings:
    """Push 알림 토글 4종 조회 (기본 모두 ON)."""
    return _load_settings()


@router.put("/alerts/settings", response_model=AlertSettings)
async def update_alert_settings(update: AlertSettingsUpdate) -> AlertSettings:
    """Push 알림 토글 부분 업데이트 — 지정한 필드만 반영 후 저장."""
    current = _load_settings()
    patch = update.model_dump(exclude_none=True)
    merged = current.model_copy(update=patch)
    _save_settings(merged)
    return merged


__all__ = ["router"]
