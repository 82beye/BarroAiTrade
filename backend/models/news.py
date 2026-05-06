"""
BAR-57 — News data models (Pydantic v2 frozen).

자금흐름 X → Decimal 정책 N/A. tags 는 frozen 호환을 위해 tuple 사용.
SourceIdStr 길이/문자 제약 — security 권고 (CWE-1284 메모리 폭주 방지).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class NewsSource(str, Enum):
    """뉴스 출처 — RSS 도메인 또는 DART."""

    DART = "dart"
    RSS_HANKYUNG = "rss_hankyung"
    RSS_MAEKYUNG = "rss_maekyung"
    RSS_YONHAP = "rss_yonhap"
    RSS_EDAILY = "rss_edaily"


SourceIdStr = Annotated[
    str,
    StringConstraints(max_length=256, pattern=r"^[\w\-/.]+$"),
]


class NewsItem(BaseModel):
    """수집된 뉴스/공시 1건. frozen — 외부 변조 차단."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: NewsSource
    source_id: SourceIdStr
    title: str = Field(min_length=1, max_length=512)
    body: str = Field(default="", max_length=20_000)
    url: str = Field(min_length=1)
    published_at: datetime
    fetched_at: datetime
    tags: tuple[str, ...] = ()


__all__ = ["NewsSource", "SourceIdStr", "NewsItem"]
