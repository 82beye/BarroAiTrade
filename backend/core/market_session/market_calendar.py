"""KRX 휴장일 캘린더 — 사전 판정으로 휴장일 매매를 대기시킨다.

배경 (2026-09-24)
-----------------
추석 연휴 첫날인데 데몬이 09:30~14:30 스캔하며 주문마다
`RC4010:모의투자 영업일이 아닙니다` 를 맞았다. 데몬에 휴장일 인식이 전혀 없었고
`is_krx_trading_day()` 는 주말만 보는 간이 판정에 *"TODO: KRX 공휴일 API 연동 권장"*
주석만 남아 있었다. 브로커가 거부해 매매 위험은 없었지만, **"진입 0건" 을 전략
문제로 오독**하게 만들어 성과 판정을 왜곡했다.

판정 규칙
--------
1. 주말(토·일)
2. `holidays.SouthKorea` — 공휴일. 음력 명절(설·추석)과 대체공휴일을 규칙으로 계산해
   해마다 손댈 필요가 없다.
3. **근로자의날(5/1)** — 증시 휴장이나 패키지 카테고리·연도에 따라 누락될 수 있어 명시.
4. **연말 폐장일(12/31)** — 공휴일이 아니지만 증시는 휴장.
5. `data/market_holidays_override.json` — 임시 휴장(전산장애 등)·오판 정정용 수동 목록.

검증 (2026-09-24)
-----------------
`data/ohlcv_cache/005930.json` 의 실제 거래일 **637일(2024-02-13 ~ 2026-09-23)** 과 대조:
**오탐 0건 · 미탐 0건**. (보정 규칙 없이는 미탐 4건 — 5/1 ×2, 12/31 ×2)

fail-open 원칙
--------------
`holidays` 미설치·판정 예외 시 **주말만 판정하고 나머지는 개장으로 본다**.
캘린더 오류가 정상 거래일의 매매를 멈추는 것이 가장 비싼 실패이므로,
불확실할 때는 거래를 허용하고 판단을 하류 게이트(브로커 RC4010 거부,
`[SHADOW-MARKET-CLOSED]` 당일봉 감지)에 넘긴다.
"""
from __future__ import annotations

import json
import os
from datetime import date as _date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

KST = timezone(timedelta(hours=9))

ENV_ENABLED = "BARRO_HOLIDAY_SKIP_ENABLED"      # 기본 ON. "0" 이면 판정 자체를 끈다.
ENV_OVERRIDE_PATH = "BARRO_HOLIDAY_OVERRIDE_PATH"

_DEFAULT_OVERRIDE = Path(__file__).resolve().parents[3] / "data" / "market_holidays_override.json"

_FALSY = {"0", "false", "no", "off"}


def skip_enabled(env: Optional[dict] = None) -> bool:
    """휴장일 스킵 활성 여부. 기본 True — `BARRO_HOLIDAY_SKIP_ENABLED=0` 으로 끈다."""
    e = os.environ if env is None else env
    return str(e.get(ENV_ENABLED, "1") or "1").strip().lower() not in _FALSY


def today_kst() -> _date:
    return datetime.now(KST).date()


@lru_cache(maxsize=4)
def _overrides(path_str: str) -> tuple[frozenset, frozenset]:
    """(추가 휴장일, 강제 개장일) — 파일 없거나 깨지면 빈 집합(fail-open).

    형식: {"closed": ["2026-12-30"], "open": ["2026-07-17"]}
      closed = 임시 휴장 추가 / open = 오판 정정(캘린더가 휴장이라 해도 개장으로 본다)
    """
    try:
        raw = json.loads(Path(path_str).read_text(encoding="utf-8"))
    except Exception:
        return frozenset(), frozenset()

    def _parse(key: str) -> frozenset:
        out = set()
        for s in raw.get(key, []) or []:
            try:
                out.add(_date.fromisoformat(str(s).strip()))
            except Exception:
                continue
        return frozenset(out)

    return _parse("closed"), _parse("open")


def override_path() -> str:
    return (os.environ.get(ENV_OVERRIDE_PATH, "") or str(_DEFAULT_OVERRIDE)).strip()


@lru_cache(maxsize=8)
def _public_holidays(year: int) -> dict:
    """해당 연도 공휴일 {date: 이름}. `holidays` 미설치면 빈 dict(fail-open)."""
    try:
        import holidays  # type: ignore
    except ImportError:
        return {}
    try:
        return {d: str(n) for d, n in holidays.SouthKorea(years=[year]).items()}
    except Exception:
        return {}


def is_market_holiday(d: Optional[_date] = None) -> Tuple[bool, str]:
    """(휴장 여부, 사유). 판정 불가 시 (False, "") — fail-open."""
    day = d or today_kst()
    try:
        closed, forced_open = _overrides(override_path())
        if day in forced_open:
            return False, ""
        if day in closed:
            return True, "override:임시 휴장"
        if day.weekday() >= 5:
            return True, "주말"
        name = _public_holidays(day.year).get(day)
        if name:
            return True, name
        if (day.month, day.day) == (5, 1):
            return True, "근로자의날(증시 휴장)"
        if (day.month, day.day) == (12, 31):
            return True, "연말 폐장일"
    except Exception:
        return False, ""       # 어떤 예외든 개장으로 본다
    return False, ""


def next_trading_day(d: Optional[_date] = None, *, limit: int = 30) -> Optional[_date]:
    """d 다음의 첫 개장일. limit 일 안에 못 찾으면 None."""
    cur = (d or today_kst()) + timedelta(days=1)
    for _ in range(limit):
        if not is_market_holiday(cur)[0]:
            return cur
        cur += timedelta(days=1)
    return None


def calendar_available() -> bool:
    """`holidays` 패키지 사용 가능 여부 — 로그에 축약 판정임을 알리는 용도."""
    try:
        import holidays  # noqa: F401
        return True
    except ImportError:
        return False


__all__ = [
    "KST", "ENV_ENABLED", "ENV_OVERRIDE_PATH",
    "skip_enabled", "today_kst", "override_path",
    "is_market_holiday", "next_trading_day", "calendar_available",
]
