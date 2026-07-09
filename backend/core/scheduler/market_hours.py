"""장 시작 직후 유예(open-rush-yield) 판정 — 대시보드 배경잡 공용.

배경(docs/03-analysis/2026-07-08-theme-implementation-issues-and-fix-design.md
§2-C): 대시보드용 배경잡(테마보드 캐시·랭킹 row 캡처·뉴스수집/발굴)이 실거래
데몬과 동일한 키움 계정 레이트리밋 예산을 공유한다. 장 시작 직후(09:00~09:05)는
실거래 데몬의 개장 스캔이 가장 민감한 구간이라, 이 시간대에는 대시보드 잡이
한 사이클을 양보한다. `intraday_buy_daemon.py`의 `CLOSE-RUSH-YIELD`(장마감 직전
양보) 패턴과 대칭.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

_KST = timezone(timedelta(hours=9))

_FLAG_ENV = "BARRO_OPEN_RUSH_YIELD_ENABLED"
_START_ENV = "BARRO_OPEN_RUSH_START_HHMM"  # 기본 "0900"
_END_ENV = "BARRO_OPEN_RUSH_END_HHMM"      # 기본 "0905"


def _parse_hhmm(value: str, default: tuple[int, int]) -> tuple[int, int]:
    v = (value or "").strip()
    if len(v) == 4 and v.isdigit():
        return int(v[:2]), int(v[2:])
    return default


def is_open_rush(now: Optional[datetime] = None) -> bool:
    """평일 개장 유예 구간(기본 09:00~09:05 KST) 여부. 기본 ON — =0 이면 항상 False."""
    if os.environ.get(_FLAG_ENV, "1").strip().lower() in {"0", "false", "no", "off"}:
        return False

    current = now or datetime.now(_KST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_KST)
    if current.weekday() >= 5:  # 토(5)/일(6)
        return False

    start_h, start_m = _parse_hhmm(os.environ.get(_START_ENV, ""), (9, 0))
    end_h, end_m = _parse_hhmm(os.environ.get(_END_ENV, ""), (9, 5))
    start = current.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = current.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    return start <= current < end


__all__ = ["is_open_rush"]
