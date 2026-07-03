"""전략 스크리너 / 차트 기준선 API schemas (티마 앱 벤치마킹 P0)."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

# 모든 스크리너/기준선 응답에 부착하는 면책 문구 (매수·매도 권유 아님).
DISCLAIMER = (
    "본 정보는 알고리즘에 의해 산출된 참고용 계산값으로 매수·매도 권유가 아닙니다. "
    "투자의 책임은 투자자 본인에게 있습니다."
)


class StrategyTabOut(BaseModel):
    """스크리너 전략 탭 메타."""

    key: str
    label: str


class LevelOut(BaseModel):
    """기준가 사다리 한 칸 (파생 계산값)."""

    label: str
    price: float
    kind: str  # "support" | "target" | "anchor"
    active: bool


class ScreenerItemOut(BaseModel):
    """스크리너 종목 1건."""

    symbol: str
    name: str
    detected_at: Optional[str] = None       # ISO8601
    price: Optional[float] = None
    change_pct: Optional[float] = None       # gateway 실패 시 None
    value_traded: Optional[float] = None     # 억원, gateway 실패 시 None
    market_cap: Optional[float] = None       # 억원, 미제공 시 None
    score: float = 0.0
    reason: str = ""
    levels: List[LevelOut] = Field(default_factory=list)


class ScreenerResponse(BaseModel):
    """스크리너 응답 래퍼."""

    strategy: str
    generated_at: str
    count: int
    items: List[ScreenerItemOut] = Field(default_factory=list)
    status: str = "ok"                       # "ok" | "no_data" | "not_ready"
    disclaimer: str = DISCLAIMER


class ChartLevelsResponse(BaseModel):
    """차트 기준선 응답."""

    symbol: str
    strategy: Optional[str] = None
    levels: List[LevelOut] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


__all__ = [
    "DISCLAIMER",
    "StrategyTabOut",
    "LevelOut",
    "ScreenerItemOut",
    "ScreenerResponse",
    "ChartLevelsResponse",
]
