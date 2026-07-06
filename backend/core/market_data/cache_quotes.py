"""OHLCV 일봉 캐시 기반 지연 시세 폴백 (읽기 전용, R1 핵심).

키움 게이트웨이가 없거나 실패할 때, data/ohlcv_cache/*.json 의 마지막 2봉으로
지연(전일 종가) 시세를 산출한다. 실시간이 아니므로 응답에 항상 source="cache" 와
as_of(캐시 마지막 일자)를 붙여 정직하게 표기한다.

캐시 파일 포맷: {"data": [{"date":"YYYYMMDD","open":..,"high":..,"low":..,
                          "close":..,"volume":..}, ...]}  (일자 오름차순)

경로 해석:
  기본값 = repo_root/data/ohlcv_cache  (Path(__file__).parents[3]/data/ohlcv_cache)
  환경변수 BARRO_OHLCV_CACHE_DIR 로 override 가능(워크트리·테스트에서 실캐시 지정용).
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# repo_root/data/ohlcv_cache — 이 파일: backend/core/market_data/cache_quotes.py
_DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "ohlcv_cache"


def cache_dir() -> Path:
    """OHLCV 캐시 디렉토리 경로. 환경변수 우선."""
    env = os.environ.get("BARRO_OHLCV_CACHE_DIR", "").strip()
    return Path(env) if env else _DEFAULT_CACHE_DIR


def _normalize_symbol(symbol: str) -> str:
    """'005930_AL' / '005930_NX' → '005930' (통합거래소 마커 strip)."""
    if not symbol:
        return ""
    return symbol.split("_", 1)[0].strip()


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def load_candles(symbol: str, base_dir: Optional[Path] = None) -> list[dict]:
    """종목 캐시 파일의 캔들 리스트(일자 오름차순). 없으면 빈 리스트."""
    sym = _normalize_symbol(symbol)
    if not sym:
        return []
    path = (base_dir or cache_dir()) / f"{sym}.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    # 방어적 정렬(일자 오름차순) — 캐시가 정렬돼 있어도 보장
    try:
        rows = sorted(rows, key=lambda r: str(r.get("date", "")))
    except Exception:
        pass
    return rows


def get_daily_candles(
    symbol: str, limit: int = 300, base_dir: Optional[Path] = None
) -> list[dict]:
    """최근 limit 개 일봉을 정규화하여 반환(오래된→최신 순).

    각 원소: {date, open, high, low, close, volume}
    """
    rows = load_candles(symbol, base_dir)
    if not rows:
        return []
    if limit and limit > 0:
        rows = rows[-limit:]
    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "date": str(r.get("date", "")),
                "open": _to_float(r.get("open")),
                "high": _to_float(r.get("high")),
                "low": _to_float(r.get("low")),
                "close": _to_float(r.get("close")),
                "volume": _to_float(r.get("volume")),
            }
        )
    return out


def get_quote(symbol: str, base_dir: Optional[Path] = None) -> Optional[dict]:
    """캐시 마지막 2봉으로 지연 시세 산출. 데이터 부족 시 None.

    반환:
      {
        symbol, price(최근 종가), change_pct(전일 대비 %),
        day_open, day_high, day_low, close,
        value_traded(억원, 근사=종가×거래량/1e8),
        volume, date(YYYYMMDD), as_of(=date), source="cache"
      }
    """
    rows = load_candles(symbol, base_dir)
    if not rows:
        return None
    last = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None

    close = _to_float(last.get("close"))
    if close <= 0:
        return None
    prev_close = _to_float(prev.get("close")) if prev else 0.0
    change_pct = (
        round((close - prev_close) / prev_close * 100.0, 2)
        if prev_close > 0
        else 0.0
    )
    volume = _to_float(last.get("volume"))
    date = str(last.get("date", ""))
    return {
        "symbol": _normalize_symbol(symbol),
        "price": close,
        "close": close,
        "change_pct": change_pct,
        "day_open": _to_float(last.get("open")),
        "day_high": _to_float(last.get("high")),
        "day_low": _to_float(last.get("low")),
        "volume": volume,
        # 억원 근사: 일봉 종가×거래량. 실 거래대금(체결가 가중) 아님 — 근사치.
        "value_traded": round(close * volume / 1e8, 2),
        "date": date,
        "as_of": date,
        "source": "cache",
    }


__all__ = [
    "cache_dir",
    "load_candles",
    "get_daily_candles",
    "get_quote",
]
