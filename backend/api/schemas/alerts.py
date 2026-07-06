"""알림 이벤트 로그 / 설정 API schemas (티마 앱 벤치마킹 P1)."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class AlertEventOut(BaseModel):
    """알림내역 항목 1건 (PRD §4.5)."""

    id: int
    strategy: str
    symbol: str
    name: str
    message: str
    level_label: Optional[str] = None
    occurred_at: str  # ISO8601


class AlertHistoryResponse(BaseModel):
    """알림내역 응답 래퍼."""

    items: List[AlertEventOut] = Field(default_factory=list)
    count: int = 0
    status: str = "ok"  # "ok" | "no_data"


class AlertSettings(BaseModel):
    """전략별 Push 알림 토글 (기본 모두 ON — PRD §4.5)."""

    f_zone: bool = True
    sf_zone: bool = True
    gold_zone: bool = True
    swing_38: bool = True


class AlertSettingsUpdate(BaseModel):
    """부분 업데이트용 — 지정한 토글만 반영."""

    f_zone: Optional[bool] = None
    sf_zone: Optional[bool] = None
    gold_zone: Optional[bool] = None
    swing_38: Optional[bool] = None


__all__ = [
    "AlertEventOut",
    "AlertHistoryResponse",
    "AlertSettings",
    "AlertSettingsUpdate",
]
