"""
시장 데이터 API 라우터

엔드포인트:
  GET  /api/market/ohlcv?symbol=005930&timeframe=5m&limit=100  - OHLCV 차트 데이터
  GET  /api/market/ticker/:symbol                               - 종목 시세 조회
  GET  /api/market/order-book/:symbol                          - 호가 조회(+ref/strength/ticks)
  GET  /api/market/universe                                     - 전종목 목록
  GET  /api/market/nxt?filter=value|gainers|losers&limit=30    - NXT 시세 목록 (스텁)
  GET  /api/market/indices                                     - 코스피/코스닥 지수
  GET  /api/market/brokers/:symbol                             - 당일 주요 거래원
  GET  /api/market/program/:symbol?mode=time|daily             - 프로그램 매매 추이
  GET  /api/market/investors                                   - 시장별 투자자 순매수

시세 조달 우선순위(읽기 전용): market_gateway → kiwoom_quotes(키움 REST) → ohlcv 캐시.
게이트웨이/키가 없으면 우아하게 캐시 또는 unsupported 로 degrade 한다(주문 경로 무관).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Path, Query, HTTPException
from pydantic import SecretStr

from backend.core.state import app_state
from backend.core.market_data import cache_quotes, stock_names

logger = logging.getLogger(__name__)
router = APIRouter()

_KST = timezone(timedelta(hours=9))


def _today_kst() -> str:
    return datetime.now(_KST).strftime("%Y%m%d")


# ── kiwoom_quotes lazy 싱글턴 (키 부재 시 None) ─────────────
_quotes = None
_quotes_tried = False


def _get_quotes():
    """KiwoomQuotes 인스턴스 반환. 키 없으면 None(조회는 캐시로 degrade)."""
    global _quotes, _quotes_tried
    if _quotes_tried:
        return _quotes
    _quotes_tried = True
    app_key = os.environ.get("KIWOOM_APP_KEY", "").strip()
    app_secret = os.environ.get("KIWOOM_APP_SECRET", "").strip()
    if not app_key or not app_secret:
        logger.info("KIWOOM 키 부재 — kiwoom_quotes 비활성(캐시 폴백)")
        _quotes = None
        return None
    try:
        from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
        from backend.core.gateway.kiwoom_quotes import KiwoomQuotes

        oauth = KiwoomNativeOAuth(
            app_key=SecretStr(app_key),
            app_secret=SecretStr(app_secret),
            base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
        )
        _quotes = KiwoomQuotes(oauth=oauth)
    except Exception as exc:
        logger.warning("kiwoom_quotes 초기화 실패: %s", type(exc).__name__)
        _quotes = None
    return _quotes


def _yyyymmdd_to_iso(d: str) -> str:
    """'20260618' → '2026-06-18T00:00:00'. 실패 시 원본."""
    if d and len(d) == 8 and d.isdigit():
        return f"{d[0:4]}-{d[4:6]}-{d[6:8]}T00:00:00"
    return d


# ══════════════════════════════════════════════════════════
# OHLCV
# ══════════════════════════════════════════════════════════
@router.get("/market/ohlcv")
async def get_ohlcv(
    symbol: str = Query(..., description="종목 코드"),
    timeframe: str = Query("5m", description="봉 주기: 1m, 5m, 15m, 1h, 1d"),
    limit: int = Query(300, ge=1, le=1000, description="캔들 수"),
) -> dict:
    """OHLCV 차트 데이터 조회. 게이트웨이 우선, 일봉은 캐시 폴백."""
    gateway = app_state.market_gateway
    if gateway:
        try:
            candles = await gateway.get_ohlcv(symbol, timeframe, limit)
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "limit": len(candles),
                "source": "gateway",
                "data": [
                    {
                        "timestamp": c.timestamp.isoformat(),
                        "open": c.open,
                        "high": c.high,
                        "low": c.low,
                        "close": c.close,
                        "volume": c.volume,
                    }
                    for c in candles
                ],
            }
        except Exception as e:
            logger.error(f"OHLCV 게이트웨이 조회 실패: {symbol}, {e}")
            if timeframe != "1d":
                raise HTTPException(status_code=500, detail=str(e))
            # 일봉이면 캐시로 폴백

    # 폴백: 일봉 캐시 (지연 시세, source=cache)
    if timeframe == "1d":
        rows = cache_quotes.get_daily_candles(symbol, limit)
        if rows:
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "limit": len(rows),
                "source": "cache",
                "as_of": rows[-1]["date"],
                "data": [
                    {
                        "timestamp": _yyyymmdd_to_iso(r["date"]),
                        "open": r["open"],
                        "high": r["high"],
                        "low": r["low"],
                        "close": r["close"],
                        "volume": r["volume"],
                    }
                    for r in rows
                ],
            }
        raise HTTPException(status_code=404, detail=f"캐시 없음: {symbol}")

    raise HTTPException(status_code=503, detail="마켓 게이트웨이 미초기화")


# ══════════════════════════════════════════════════════════
# Ticker
# ══════════════════════════════════════════════════════════
@router.get("/market/ticker/{symbol}")
async def get_ticker(symbol: str = Path(..., description="종목 코드")) -> dict:
    """종목 시세 조회. gateway → kiwoom_quotes → 캐시 폴백."""
    gateway = app_state.market_gateway
    if gateway:
        try:
            ticker = await gateway.get_ticker(symbol)
            return {
                "symbol": ticker.symbol,
                "name": ticker.name or stock_names.resolve(symbol),
                "price": ticker.price,
                "volume": ticker.volume,
                "change_pct": ticker.change_pct,
                "timestamp": ticker.timestamp.isoformat(),
                "source": "gateway",
            }
        except Exception as e:
            logger.warning(f"Ticker 게이트웨이 실패, 폴백: {symbol}, {e}")

    # kiwoom_quotes (ka10001)
    quotes = _get_quotes()
    if quotes:
        info = await quotes.stock_info(symbol)
        if info and info.get("price") is not None:
            return {
                "symbol": info["symbol"],
                "name": info.get("name") or stock_names.resolve(symbol),
                "price": info.get("price"),
                "volume": None,
                "change_pct": info.get("change_pct"),
                "timestamp": datetime.now(_KST).isoformat(),
                "source": "kiwoom",
            }

    # 캐시 폴백 (지연 시세)
    q = cache_quotes.get_quote(symbol)
    if q:
        return {
            "symbol": q["symbol"],
            "name": stock_names.resolve(symbol),
            "price": q["price"],
            "volume": q["volume"],
            "change_pct": q["change_pct"],
            "timestamp": _yyyymmdd_to_iso(q["date"]),
            "as_of": q["as_of"],
            "source": "cache",
        }
    raise HTTPException(status_code=404, detail=f"시세 없음: {symbol}")


# ══════════════════════════════════════════════════════════
# Order Book (+ ref / strength / ticks)
# ══════════════════════════════════════════════════════════
@router.get("/market/order-book/{symbol}")
async def get_order_book(symbol: str = Path(..., description="종목 코드")) -> dict:
    """호가 조회. gateway 우선, 실패 시 kiwoom_quotes(ka10004).

    확장(옵션, 하위호환): ref(기준가/시가/고저/상하한/VI), strength(체결강도),
    ticks(최근 체결). 조달 불가 항목은 null.
    """
    base = {
        "symbol": symbol,
        "asks": [],
        "bids": [],
        "timestamp": datetime.now(_KST).isoformat(),
        "ref": None,
        "strength": None,
        "ticks": None,
    }

    gateway = app_state.market_gateway
    if gateway:
        try:
            ob = await gateway.get_order_book(symbol)
            base.update(
                {
                    "symbol": ob.symbol,
                    "asks": ob.asks,
                    "bids": ob.bids,
                    "timestamp": ob.timestamp.isoformat(),
                    "source": "gateway",
                }
            )
        except Exception as e:
            logger.warning(f"Order book 게이트웨이 실패, 폴백: {symbol}, {e}")

    quotes = _get_quotes()
    if not base["asks"] and not base["bids"] and quotes:
        ob = await quotes.orderbook(symbol)
        if ob:
            base.update(
                {
                    "asks": ob["asks"],
                    "bids": ob["bids"],
                    "total_ask_qty": ob.get("total_ask_qty"),
                    "total_bid_qty": ob.get("total_bid_qty"),
                    "source": "kiwoom",
                }
            )

    # ref / strength / ticks (kiwoom_quotes 있을 때만)
    if quotes:
        info = await quotes.stock_info(symbol)
        if info:
            base["ref"] = {
                "base_price": info.get("base_price"),
                "open": info.get("open"),
                "high": info.get("high"),
                "low": info.get("low"),
                "upper_limit": info.get("upper_limit"),
                "lower_limit": info.get("lower_limit"),
                # ka10004/ka10001 응답에 예상 VI 발동가 필드가 없어 미제공
                "vi_up_expected": None,
                "vi_down_expected": None,
            }
        strength = await quotes.strength(symbol)
        if strength:
            base["strength"] = strength.get("value")
        ticks = await quotes.ticks(symbol, limit=10)
        if ticks:
            base["ticks"] = [
                {"time": t["time"], "price": t["price"], "qty": t["qty"]}
                for t in ticks
            ]

    return base


# ══════════════════════════════════════════════════════════
# Universe
# ══════════════════════════════════════════════════════════
@router.get("/market/universe")
async def get_universe() -> dict:
    """전종목 목록 조회."""
    gateway = app_state.market_gateway
    if not gateway:
        raise HTTPException(status_code=503, detail="마켓 게이트웨이 미초기화")
    try:
        universe = await gateway.get_universe()
        return {"symbols": universe, "count": len(universe)}
    except Exception as e:
        logger.error(f"Universe 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── NXT (대체거래소) 시세 목록 ──────────────────────────────────────────────
#
# 조사 결론 (2026-07-04):
#   현행 NXT 게이트웨이(backend/core/gateway/nxt.py 의 INxtGateway/NxtGatewayManager)
#   는 **스트리밍 subscribe/callback** 인터페이스만 제공한다
#   (subscribe_ticker/orderbook/trade + on_tick 콜백 — nxt.py:49-67).
#   거래대금/등락률 기준 '시세 목록(랭킹) 조회' 메서드가 없고, app_state 에 NXT
#   매니저가 배선돼 있지도 않다(grep: nxt.py 외 참조 0건).
#   따라서 NXT 애프터마켓 종목 목록 API 는 현재 **미지원**이며, 정직하게
#   status="unsupported" + 빈 items 를 반환한다.
#
# 활성화에 필요한 gateway 확장 (후속 BAR 과제):
#   1) INxtGateway 에 목록 조회 시그니처 추가, 예:
#        async def get_ranking(self, filter: str, limit: int) -> list[NxtQuote]
#      (filter: "value"=거래대금 | "gainers"=상승 | "losers"=하락;
#       NxtQuote: symbol, name, nxt_price, vs_close_pct, day_close,
#                 day_change_pct, aft_value, cum_value)
#      — 키움 자체 OpenAPI ka10032/ka10027 랭킹 TR(kiwoom_native_rank.py 참고)을
#        stex_tp NXT 옵션으로 조회하거나, NXT/KOSCOM 시세 API 를 연동.
#   2) 애플리케이션 기동 시 NxtGatewayManager 를 app_state.nxt_gateway 로 주입.
#   3) 본 핸들러에서 app_state.nxt_gateway.get_ranking(...) 호출로 교체.
_NXT_FILTERS = ("value", "gainers", "losers")


@router.get("/market/nxt")
async def get_nxt_quotes(
    filter: str = Query("value", description="정렬 기준: value|gainers|losers"),
    limit: int = Query(30, ge=1, le=100, description="종목 수"),
) -> dict:
    """
    NXT(대체거래소) 시세/애프터마켓 목록 조회 — 현재 미지원 스텁.

    현행 NxtGateway 는 스트리밍 구독 전용이라 목록 조회를 지원하지 않는다.
    지수 API 패턴과 동일하게 status 로 가용성을 정직 반환한다:
      - "unsupported": gateway 인터페이스가 목록 조회를 지원하지 않음(현 상태)
      - "not_ready"  : 지원하지만 NXT 게이트웨이 미초기화(후속 배선 시)

    응답:
    ```json
    {
      "filter": "value",
      "limit": 30,
      "items": [],
      "status": "unsupported"
    }
    ```
    items 스키마(활성화 시):
      {symbol, name, nxt_price, vs_close_pct, day_close, day_change_pct,
       aft_value, cum_value}
    """
    if filter not in _NXT_FILTERS:
        raise HTTPException(
            status_code=422,
            detail=f"filter 는 {list(_NXT_FILTERS)} 중 하나여야 합니다",
        )

    nxt_gateway = getattr(app_state, "nxt_gateway", None)
    status = "unsupported" if nxt_gateway is None else "not_ready"

    return {
        "filter": filter,
        "limit": limit,
        "items": [],
        "status": status,
    }


# ══════════════════════════════════════════════════════════
# Indices — 코스피/코스닥 지수 (ka20001)
# ══════════════════════════════════════════════════════════
@router.get("/market/indices")
async def get_indices() -> dict:
    """코스피(0/001)·코스닥(1/101) 지수. 키/실패 시 unsupported."""
    quotes = _get_quotes()
    if not quotes:
        return {"items": [], "status": "unsupported"}
    targets = [
        ("KOSPI", "코스피", "0", "001"),
        ("KOSDAQ", "코스닥", "1", "101"),
    ]
    items = []
    for code, name, mrkt_tp, inds_cd in targets:
        idx = await quotes.index_price(mrkt_tp, inds_cd)
        if idx and idx.get("value") is not None:
            items.append(
                {
                    "code": code,
                    "name": name,
                    "value": idx.get("value"),
                    "change": idx.get("change"),
                    "change_pct": idx.get("change_pct"),
                }
            )
    if not items:
        return {"items": [], "status": "unsupported"}
    return {"items": items, "status": "ok"}


# ══════════════════════════════════════════════════════════
# Brokers — 당일 주요 거래원 (ka10040, 폴백 ka10002)
# ══════════════════════════════════════════════════════════
@router.get("/market/brokers/{symbol}")
async def get_brokers(symbol: str = Path(..., description="종목 코드")) -> dict:
    quotes = _get_quotes()
    if not quotes:
        return {"sell": [], "buy": [], "foreign_sell": None,
                "foreign_buy": None, "status": "unsupported"}
    data = await quotes.brokers(symbol)
    if not data:
        data = await quotes.brokers_basic(symbol)
    if not data:
        return {"sell": [], "buy": [], "foreign_sell": None,
                "foreign_buy": None, "status": "no_data"}
    return {
        "sell": data.get("sell", []),
        "buy": data.get("buy", []),
        "foreign_sell": data.get("foreign_sell"),
        "foreign_buy": data.get("foreign_buy"),
        "status": "ok",
    }


# ══════════════════════════════════════════════════════════
# Program — 프로그램 매매 추이 (ka90008 시간별 / ka90013 일별)
# ══════════════════════════════════════════════════════════
@router.get("/market/program/{symbol}")
async def get_program(
    symbol: str = Path(..., description="종목 코드"),
    mode: str = Query("time", description="time|daily"),
    date: str = Query("", description="YYYYMMDD (미지정 시 오늘)"),
) -> dict:
    quotes = _get_quotes()
    if not quotes:
        return {"items": [], "status": "unsupported"}
    d = date.strip() or _today_kst()
    if mode == "daily":
        rows = await quotes.program_daily(symbol, date=date.strip())
    else:
        rows = await quotes.program_time(symbol, date=d)
    if rows is None:
        return {"items": [], "status": "no_data"}
    return {"items": rows, "mode": mode, "status": "ok"}


# ══════════════════════════════════════════════════════════
# Investors — 시장별 투자자 순매수 (ka10066 마감후 / ka10063 장중)
# 종목별 리스트를 시장 단위로 합산하여 개인/외국인/기관 순매수(억원) 산출.
# ══════════════════════════════════════════════════════════
def _sum_after_market(rows: list) -> dict:
    """ka10066 종목별 리스트 → 개인/외국인/기관 순매수 합(백만원)."""
    from backend.core.gateway.kiwoom_quotes import _num

    ind = frgn = orgn = 0.0
    for r in rows:
        ind += _num(r.get("ind_invsr"))
        frgn += _num(r.get("frgnr_invsr"))
        orgn += _num(r.get("orgn"))
    # 백만원 → 억원 (1억 = 100백만)
    return {
        "individual": round(ind / 100.0, 1),
        "foreign": round(frgn / 100.0, 1),
        "institution": round(orgn / 100.0, 1),
    }


@router.get("/market/investors")
async def get_investors() -> dict:
    """코스피/코스닥 투자자별 순매수(억원). 마감후 ka10066 종목합산."""
    quotes = _get_quotes()
    if not quotes:
        return {"kospi": None, "kosdaq": None, "status": "unsupported"}
    kospi_rows = await quotes.investors_after("001")
    kosdaq_rows = await quotes.investors_after("101")
    if kospi_rows is None and kosdaq_rows is None:
        return {"kospi": None, "kosdaq": None, "status": "no_data"}
    return {
        "kospi": _sum_after_market(kospi_rows) if kospi_rows else None,
        "kosdaq": _sum_after_market(kosdaq_rows) if kosdaq_rows else None,
        "unit": "억원",
        "status": "ok",
    }
