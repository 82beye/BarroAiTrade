"""테마 매핑 + 집계 — 시장-맥락 add-on 의 결정적 기반 (LLM 무관).

종목→테마 매핑은 **커밋된 `data/theme_map.json`** 을 진실원천으로 한다(유지보수 대상).
이 모듈은 순수·결정적이며 다음을 제공한다:
  · load_theme_map         — theme_map.json 로드(fail-safe).
  · themes_of              — 종목의 테마 리스트.
  · hot_themes             — leaders 거래대금 → 테마별 turnover 집중도(오늘 핫테마).
  · theme_exposure         — 보유 포지션 → 테마별 노출 비중(포트폴리오 쏠림).

주의(명시): 한 종목이 복수 테마에 속하면 그 종목의 거래대금/평가액이 각 테마에 중복
가산된다(쏠림 가드는 보수적으로 더 많이 잡는 게 안전). share 합은 1을 넘을 수 있다.
미매핑 종목은 어떤 테마에도 기여하지 않는다(가드 미적용 = fail-open).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


def load_theme_map(path) -> dict:
    """data/theme_map.json → {symbol: [themes]}. 부재/오류 → {}(fail-safe).

    스키마: {"version":..., "map": {"005930": ["반도체","AI"], ...}}
    상위 호환: dict 직결({symbol:[...]})도 허용.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    raw = data.get("map") if isinstance(data, dict) and "map" in data else data
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for sym, themes in raw.items():
        if isinstance(themes, str):
            themes = [themes]
        if isinstance(themes, (list, tuple)):
            cleaned = [str(t).strip() for t in themes if str(t).strip()]
            if cleaned:
                out[str(sym).strip()] = cleaned
    return out


def themes_of(symbol, theme_map: dict) -> list:
    return list(theme_map.get(str(symbol), []))


def _accumulate(items: Iterable[tuple], theme_map: dict) -> tuple[dict, float]:
    """(symbol, value) 들을 테마별로 합산. 반환: ({theme: value}, total_value)."""
    by_theme: dict = {}
    total = 0.0
    for sym, val in items:
        try:
            v = float(val or 0.0)
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        total += v
        for t in theme_map.get(str(sym), []):
            by_theme[t] = by_theme.get(t, 0.0) + v
    return by_theme, total


def hot_themes(leaders: Iterable[dict], theme_map: dict, *, top: int | None = None) -> list:
    """오늘 거래대금 집중 테마. leaders=[{symbol, trade_value}].

    반환: [{theme, turnover, turnover_pct(0~1), rank, symbols:[...]}] turnover 내림차순.
    """
    pairs = [(d.get("symbol"), d.get("trade_value")) for d in leaders if isinstance(d, dict)]
    by_theme, total = _accumulate(pairs, theme_map)
    if total <= 0 or not by_theme:
        return []
    # 테마별 기여 종목(거래대금 보유 종목만)
    members: dict = {}
    for sym, tv in pairs:
        try:
            if float(tv or 0.0) <= 0:
                continue
        except (TypeError, ValueError):
            continue
        for t in theme_map.get(str(sym), []):
            members.setdefault(t, []).append(str(sym))
    ranked = sorted(by_theme.items(), key=lambda kv: kv[1], reverse=True)
    out = []
    for i, (theme, turnover) in enumerate(ranked, start=1):
        out.append({
            "theme": theme,
            "turnover": round(turnover, 0),
            "turnover_pct": round(turnover / total, 4),
            "rank": i,
            "symbols": members.get(theme, []),
        })
    return out[:top] if top else out


def theme_exposure(positions: Iterable[dict], theme_map: dict) -> dict:
    """보유 포트폴리오의 테마별 노출 비중. positions=[{symbol, eval_value}].

    반환: {theme: exposure_pct(0~1)} — eval_value 합산 / 총 평가액.
    """
    pairs = [(d.get("symbol"), d.get("eval_value")) for d in positions if isinstance(d, dict)]
    by_theme, total = _accumulate(pairs, theme_map)
    if total <= 0:
        return {}
    return {t: round(v / total, 4) for t, v in by_theme.items()}


__all__ = ["load_theme_map", "themes_of", "hot_themes", "theme_exposure"]
