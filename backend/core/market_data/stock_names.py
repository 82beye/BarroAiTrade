"""종목코드 → 종목명 마스터 로더 (읽기 전용, lazy).

우선순위:
  1) data/stock_names.json  (scripts/build_stock_names.py 가 ka10099 로 생성한 전 종목 마스터)
  2) data/refined_signals.json 의 signals[].{symbol,name}  (운영 중 관측된 실 종목명 병합)

파일이 없으면 빈 dict 로 우아하게 동작한다. resolve(symbol) 은 이름을 찾지 못하면
정규화된 종목코드를 그대로 반환한다.

경로: repo_root/data (Path(__file__).parents[3]/data), 환경변수 BARRO_DATA_DIR override.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# 프로세스 캐시 (lazy 1회 로드)
_NAMES: Optional[dict[str, str]] = None


def data_dir() -> Path:
    env = os.environ.get("BARRO_DATA_DIR", "").strip()
    return Path(env) if env else _DEFAULT_DATA_DIR


def _normalize_symbol(symbol: str) -> str:
    if not symbol:
        return ""
    return symbol.split("_", 1)[0].strip()


def _load_stock_names_file(base: Path) -> dict[str, str]:
    path = base / "stock_names.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for code, name in data.items():
        c = _normalize_symbol(str(code))
        if c and isinstance(name, str) and name.strip():
            out[c] = name.strip()
    return out


def _load_refined_signal_names(base: Path) -> dict[str, str]:
    path = base / "refined_signals.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    out: dict[str, str] = {}
    signals = data.get("signals") if isinstance(data, dict) else None
    if isinstance(signals, list):
        for s in signals:
            if not isinstance(s, dict):
                continue
            c = _normalize_symbol(str(s.get("symbol", "")))
            n = s.get("name")
            if c and isinstance(n, str) and n.strip():
                out.setdefault(c, n.strip())
    return out


def _build() -> dict[str, str]:
    base = data_dir()
    names = _load_stock_names_file(base)
    # refined_signals 의 실관측 이름 병합(마스터에 없는 코드만 보강)
    for code, name in _load_refined_signal_names(base).items():
        names.setdefault(code, name)
    if names:
        logger.info("stock_names 로드: %d 종목", len(names))
    else:
        logger.info("stock_names 마스터 없음 — 빈 dict (코드 그대로 표기)")
    return names


def load_names(force: bool = False) -> dict[str, str]:
    """마스터 dict 반환(lazy). force=True 시 재로드."""
    global _NAMES
    if _NAMES is None or force:
        _NAMES = _build()
    return _NAMES


def resolve(symbol: str) -> str:
    """종목명 반환. 미발견 시 정규화된 종목코드 그대로."""
    sym = _normalize_symbol(symbol)
    if not sym:
        return symbol or ""
    return load_names().get(sym, sym)


def resolve_many(symbols) -> dict[str, str]:
    """여러 종목 일괄 resolve → {정규화코드: 이름}."""
    names = load_names()
    out: dict[str, str] = {}
    for s in symbols:
        c = _normalize_symbol(s)
        if c:
            out[c] = names.get(c, c)
    return out


__all__ = ["data_dir", "load_names", "resolve", "resolve_many"]
