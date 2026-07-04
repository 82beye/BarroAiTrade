"""
시장 데이터 API 라우터

엔드포인트:
  GET  /api/market/ohlcv?symbol=005930&timeframe=5m&limit=100  - OHLCV 차트 데이터
  GET  /api/market/ticker/:symbol                               - 종목 시세 조회
  GET  /api/market/order-book/:symbol                          - 호가 조회
  GET  /api/market/universe                                     - 전종목 목록
  GET  /api/market/nxt?filter=value|gainers|losers&limit=30    - NXT 시세 목록 (스텁)
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Path, Query, HTTPException

from backend.core.gateway.base import MarketGateway
from backend.models.market import OHLCV, Ticker, OrderBook
from backend.core.state import app_state

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_gateway() -> MarketGateway:
    """마켓 게이트웨이 인스턴스 반환"""
    gateway = app_state.market_gateway
    if not gateway:
        raise HTTPException(
            status_code=503,
            detail="마켓 게이트웨이 미초기화"
        )
    return gateway


@router.get("/market/ohlcv")
async def get_ohlcv(
    symbol: str = Query(..., description="종목 코드"),
    timeframe: str = Query("5m", description="봉 주기: 1m, 5m, 15m, 1h, 1d"),
    limit: int = Query(300, ge=1, le=1000, description="캔들 수"),
) -> dict:
    """
    OHLCV 차트 데이터 조회

    응답:
    ```json
    {
      "symbol": "005930",
      "timeframe": "5m",
      "limit": 300,
      "data": [
        {
          "timestamp": "2026-04-11T10:00:00Z",
          "open": 75000,
          "high": 75500,
          "low": 74500,
          "close": 75250,
          "volume": 1000000
        }
      ]
    }
    ```
    """
    try:
        gateway = _get_gateway()
        candles = await gateway.get_ohlcv(symbol, timeframe, limit)

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "limit": len(candles),
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
            ]
        }
    except Exception as e:
        logger.error(f"OHLCV 조회 실패: {symbol}, {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/market/ticker/{symbol}")
async def get_ticker(
    symbol: str = Path(..., description="종목 코드"),
) -> dict:
    """
    종목 시세 조회

    응답:
    ```json
    {
      "symbol": "005930",
      "name": "삼성전자",
      "price": 75000,
      "volume": 1000000,
      "change_pct": 0.5,
      "timestamp": "2026-04-11T10:00:00Z"
    }
    ```
    """
    try:
        gateway = _get_gateway()
        ticker = await gateway.get_ticker(symbol)

        return {
            "symbol": ticker.symbol,
            "name": ticker.name,
            "price": ticker.price,
            "volume": ticker.volume,
            "change_pct": ticker.change_pct,
            "timestamp": ticker.timestamp.isoformat(),
        }
    except Exception as e:
        logger.error(f"Ticker 조회 실패: {symbol}, {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/market/order-book/{symbol}")
async def get_order_book(
    symbol: str = Path(..., description="종목 코드"),
) -> dict:
    """
    호가 조회

    응답:
    ```json
    {
      "symbol": "005930",
      "asks": [[75500, 1000], [75600, 2000]],
      "bids": [[75000, 1000], [74900, 2000]],
      "timestamp": "2026-04-11T10:00:00Z"
    }
    ```
    """
    try:
        gateway = _get_gateway()
        order_book = await gateway.get_order_book(symbol)

        return {
            "symbol": order_book.symbol,
            "asks": order_book.asks,
            "bids": order_book.bids,
            "timestamp": order_book.timestamp.isoformat(),
        }
    except Exception as e:
        logger.error(f"Order book 조회 실패: {symbol}, {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/market/indices")
async def get_indices() -> dict:
    """
    지수(KOSPI/KOSDAQ) 시세 조회 (티마 앱 벤치마킹 P1, PRD §5 시장종합·하단 지수 바).

    현 MarketGateway 인터페이스는 개별 종목 ticker 만 제공하며 지수 심볼 조회를
    지원하지 않는다. 지원 전까지는 items:[] + status:"unsupported" 로 정직하게 반환
    (지수 값 날조 금지). gateway 미초기화 시에도 동일.

    응답:
    ```json
    { "items": [], "status": "unsupported" }
    ```

    조사 결론 (2026-07-04, 티마 P1 운영 배선):
      키움 native gateway(backend/core/gateway/kiwoom_native_*.py)에 랩핑된 TR 은
      랭킹(ka10032/ka10027/ka10030)·차트(ka10080/ka10081)·계좌(kt00001)·주문
      (kt10001/kt10002)·호가뿐이며, **업종/지수 시세 TR 은 랩핑돼 있지 않다**.
      따라서 실데이터 활성화는 실거래 gateway 코드에 새 TR 을 추가해야 하므로
      리스크가 있어 이번 범위에서 구현하지 않고 unsupported 를 유지한다.

    활성화에 필요한 gateway 확장 (후속 BAR 과제):
      1) 신규 fetcher(예: KiwoomNativeIndexFetcher)를 kiwoom_native_candles.py 패턴으로
         추가 — 업종지수 TR(키움 OpenAPI 업종 계열: 업종현재가/업종지수 ka20xxx,
         POST /api/dostk/... , body {inds_cd}) 조회. KOSPI=001, KOSDAQ=101 코드 매핑.
         ※ 정확한 api-id 는 키움 REST '업종' 문서로 확정 필요(TR 코드 미검증 상태).
      2) MarketGateway/base.py 에 get_index(code) 시그니처 추가 + app_state 주입.
      3) 본 핸들러에서 gateway.get_index("001"|"101") 호출로 교체 후 status:"ok".
    """
    return {"items": [], "status": "unsupported"}


@router.get("/market/universe")
async def get_universe() -> dict:
    """
    전종목 목록 조회

    응답:
    ```json
    {
      "symbols": ["005930", "000660", "051910", ...],
      "count": 100
    }
    ```
    """
    try:
        gateway = _get_gateway()
        universe = await gateway.get_universe()

        return {
            "symbols": universe,
            "count": len(universe),
        }
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
