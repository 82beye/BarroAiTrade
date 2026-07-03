"""전략 스크리너 / 차트 기준선 API 라우터 (티마 앱 벤치마킹 P0).

엔드포인트:
  GET /api/screener/strategies        - 스크리너 전략 탭 메타
  GET /api/screener/{strategy}        - 전략별 종목 스크리닝 (기준가 사다리 부착)
  GET /api/chart/levels?symbol=X      - 종목 차트 기준선 세트

데이터 소스:
  - symbols 미지정: data/refined_signals.json (운영 데몬 산출) 필터
  - symbols 지정 : SignalScanner 온디맨드 스캔 (app_state.market_gateway)

⚠️ 기준가(levels)는 backend.core.strategy.reference_levels 파생 계산값 —
   원앱(티마) 산출식과 다를 수 있는 참고용 값.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time as _dtime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from backend.api.schemas.screener import (
    ChartLevelsResponse,
    ScreenerItemOut,
    ScreenerResponse,
    StrategyTabOut,
)
from backend.core.state import app_state
from backend.core.strategy.blue_line import BlueLineParams
from backend.core.strategy.f_zone import FZoneParams
from backend.core.strategy.reference_levels import SUPPORTED_STRATEGIES, compute_levels

logger = logging.getLogger(__name__)
router = APIRouter()

# (D+N) 상대일 표기 대상 전략 (골드존·38스윙 — PRD §4.2)
_D_OFFSET_STRATEGIES = ("gold_zone", "swing_38")


def _parse_detected_date(detected_at: Optional[str]) -> Optional[date]:
    """detected_at(ISO8601)의 날짜 부분만 추출 (파싱 실패 시 None)."""
    if not detected_at:
        return None
    try:
        return datetime.fromisoformat(detected_at).date()
    except (ValueError, TypeError):
        try:
            return date.fromisoformat(detected_at[:10])
        except (ValueError, TypeError):
            return None


def _attach_d_offset(
    levels: List[dict], strategy: str, detected_at: Optional[str]
) -> None:
    """골드존·38스윙 level 에 d_offset = (오늘 - 포착일).days + 1 부여 (in-place).

    포착일 파싱 불가 시 미부여(None 유지). reached_at 은 산출 근거 없어 건드리지 않음.
    """
    if strategy not in _D_OFFSET_STRATEGIES:
        return
    det = _parse_detected_date(detected_at)
    if det is None:
        return
    d_offset = (datetime.now(timezone.utc).date() - det).days + 1
    for lv in levels:
        lv["d_offset"] = d_offset


# 스크리너 전략 탭 메타 (표시 순서 유지)
_STRATEGY_TABS = [
    {"key": "f_zone", "label": "F존"},
    {"key": "sf_zone", "label": "SF존"},
    {"key": "gold_zone", "label": "골드존"},
    {"key": "swing_38", "label": "38스윙"},
]


def _refined_path() -> Path:
    """운영 데몬이 쓰는 data/refined_signals.json 경로 (repo_root/data)."""
    return Path(__file__).resolve().parents[3] / "data" / "refined_signals.json"


def _load_refined() -> Optional[dict]:
    """refined_signals.json 로드 (없거나 파싱 실패 시 None)."""
    path = _refined_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("refined_signals.json 읽기 실패: %s", e)
        return None


async def _scan_symbols(strategy: str, symbols: List[str]) -> List[dict]:
    """SignalScanner 온디맨드 스캔 → 해당 strategy signal dict 리스트.

    gateway 미초기화 시 빈 리스트. 실패는 상위에서 처리.
    """
    from backend.core.scanner.signal_scanner import SignalScanner

    gateway = app_state.market_gateway
    if gateway is None:
        return []

    # 요청 전략만 활성 (나머지 4종 비활성) — 온디맨드 비용 최소화.
    enabled = {k: (k == strategy) for k in SUPPORTED_STRATEGIES}
    scanner = SignalScanner(
        gateway,
        f_zone_params=FZoneParams(min_atr_pct=0.035, entry_time_cutoff=_dtime(14, 0)),
        blue_line_params=BlueLineParams(min_atr_pct=0.035, entry_time_cutoff=_dtime(14, 0)),
        enabled_strategies=enabled,
    )
    signals = await scanner.scan(symbols)
    return [
        s.model_dump(mode="json")
        for s in signals
        if s.signal_type == strategy
    ]


async def _ticker_enrich(symbol: str) -> dict:
    """gateway ticker 조회로 change_pct / value_traded / price 보강.

    실패 시 빈 dict (전체 요청이 죽지 않게 개별 try/except).
    """
    gateway = app_state.market_gateway
    if gateway is None:
        return {}
    try:
        ticker = await gateway.get_ticker(symbol)
        value_traded = None
        if ticker.price and ticker.volume:
            # 억원 단위 (원화 거래대금 / 1e8)
            value_traded = round(ticker.price * ticker.volume / 1e8, 2)
        return {
            "price": ticker.price,
            "change_pct": ticker.change_pct,
            "value_traded": value_traded,
        }
    except Exception as e:
        logger.debug("ticker 보강 실패 %s: %s", symbol, e)
        return {}


async def _build_item(strategy: str, sig: dict) -> ScreenerItemOut:
    """refined/scan signal dict → ScreenerItemOut (levels + ticker 보강)."""
    symbol = sig.get("symbol", "")
    signal_price = float(sig.get("price") or 0.0)

    enrich = await _ticker_enrich(symbol)
    current_price = enrich.get("price") or signal_price

    levels = compute_levels(
        signal_type=strategy,
        signal_price=signal_price,
        current_price=current_price,
    )
    _attach_d_offset(levels, strategy, sig.get("timestamp"))

    return ScreenerItemOut(
        symbol=symbol,
        name=sig.get("name") or symbol,
        detected_at=sig.get("timestamp"),
        price=signal_price or None,
        change_pct=enrich.get("change_pct"),
        value_traded=enrich.get("value_traded"),
        market_cap=None,
        score=float(sig.get("score") or 0.0),
        reason=sig.get("reason") or "",
        levels=levels,
    )


@router.get("/screener/strategies", response_model=list[StrategyTabOut])
async def list_strategy_tabs() -> list[StrategyTabOut]:
    """사용 가능한 스크리너 전략 탭 메타."""
    return [StrategyTabOut(**t) for t in _STRATEGY_TABS]


@router.get("/screener/{strategy}", response_model=ScreenerResponse)
async def screen_strategy(
    strategy: str,
    symbols: Optional[str] = Query(
        None, description="쉼표구분 종목코드 (지정 시 온디맨드 스캔)"
    ),
    limit: int = Query(50, ge=1, le=200, description="최대 종목 수"),
) -> ScreenerResponse:
    """전략별 종목 스크리닝 — 기준가 사다리 부착.

    - symbols 지정: SignalScanner 온디맨드 스캔
    - symbols 미지정: refined_signals.json 필터 (파일 없으면 status=no_data)
    """
    if strategy not in SUPPORTED_STRATEGIES:
        raise HTTPException(status_code=404, detail=f"지원하지 않는 전략: {strategy}")

    generated_at = datetime.now(timezone.utc).isoformat()
    status = "ok"
    raw_signals: List[dict] = []

    if symbols:
        symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        try:
            raw_signals = await _scan_symbols(strategy, symbol_list)
        except Exception as e:
            logger.error("스크리너 온디맨드 스캔 실패 (%s): %s", strategy, e)
            raise HTTPException(status_code=500, detail=str(e))
        if app_state.market_gateway is None:
            status = "not_ready"
    else:
        data = _load_refined()
        if data is None:
            return ScreenerResponse(
                strategy=strategy,
                generated_at=generated_at,
                count=0,
                items=[],
                status="no_data",
            )
        raw_signals = [
            s for s in data.get("signals", [])
            if s.get("signal_type") == strategy
        ]

    raw_signals = raw_signals[:limit]
    items = [await _build_item(strategy, s) for s in raw_signals]

    return ScreenerResponse(
        strategy=strategy,
        generated_at=generated_at,
        count=len(items),
        items=items,
        status=status,
    )


@router.get("/chart/levels", response_model=ChartLevelsResponse)
async def chart_levels(
    symbol: str = Query(..., description="종목 코드"),
) -> ChartLevelsResponse:
    """종목의 최근 시그널에 기준선 세트를 부착해 반환.

    검색 순서: refined_signals.json → 온디맨드 스캔(4종) → 빈 세트.
    """
    symbol = symbol.strip().upper()

    # 1. refined_signals.json 에서 최신 시그널 검색
    data = _load_refined()
    sig: Optional[dict] = None
    if data is not None:
        for s in data.get("signals", []):
            if s.get("symbol", "").upper() == symbol and s.get("signal_type") in SUPPORTED_STRATEGIES:
                sig = s
                break

    # 2. 없으면 온디맨드 스캔 1회 (4종 전부)
    if sig is None and app_state.market_gateway is not None:
        for strat in SUPPORTED_STRATEGIES:
            try:
                found = await _scan_symbols(strat, [symbol])
            except Exception as e:
                logger.debug("chart/levels 스캔 실패 %s/%s: %s", symbol, strat, e)
                continue
            if found:
                sig = found[0]
                break

    if sig is None:
        return ChartLevelsResponse(symbol=symbol, strategy=None, levels=[])

    strategy = sig.get("signal_type")
    signal_price = float(sig.get("price") or 0.0)
    enrich = await _ticker_enrich(symbol)
    current_price = enrich.get("price") or signal_price

    levels = compute_levels(
        signal_type=strategy,
        signal_price=signal_price,
        current_price=current_price,
    )
    _attach_d_offset(levels, strategy, sig.get("timestamp"))
    from backend.api.schemas.screener import LevelOut

    return ChartLevelsResponse(
        symbol=symbol,
        strategy=strategy,
        levels=[LevelOut(**lv) for lv in levels],
    )


__all__ = ["router"]
