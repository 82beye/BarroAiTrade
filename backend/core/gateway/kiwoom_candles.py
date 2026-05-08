"""BAR-OPS-09 — KiwoomCandleFetcher.

키움 OpenAPI (KIS Open Trading API) 일봉/분봉 다운로드.

엔드포인트:
- 일봉: /uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice (TR_ID: FHKST03010100)
- 분봉: /uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice (TR_ID: FHKST03010200)

레이트 리밋:
- 일봉: 1초 5건
- 분봉: 1초 2건 (모의), 1초 5건 (실전)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx
from pydantic import SecretStr

from backend.core.gateway.kiwoom_oauth import KiwoomOAuth2Manager
from backend.models.market import MarketType, OHLCV

logger = logging.getLogger(__name__)


# TR_ID 분류
_TR_DAILY = "FHKST03010100"      # 국내주식 기간별 시세 (일/주/월/년)
_TR_MINUTE = "FHKST03010200"     # 국내주식 당일분봉

_DAILY_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
_MINUTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"


class KiwoomCandleFetcher:
    """키움 OpenAPI 캔들 다운로더."""

    # 일봉/주봉/월봉/년봉 period_div_code
    PERIOD_DAILY = "D"
    PERIOD_WEEKLY = "W"
    PERIOD_MONTHLY = "M"
    PERIOD_YEARLY = "Y"

    def __init__(
        self,
        oauth: KiwoomOAuth2Manager,
        app_key: SecretStr,
        app_secret: SecretStr,
        http_client: Optional[httpx.AsyncClient] = None,
        rate_limit_seconds: float = 0.25,   # 1초 4건 (안전)
    ) -> None:
        if not isinstance(app_key, SecretStr) or not isinstance(app_secret, SecretStr):
            raise TypeError("credentials must be SecretStr (CWE-798)")
        self._oauth = oauth
        self._app_key = app_key
        self._app_secret = app_secret
        self._http = http_client
        self._rate = rate_limit_seconds

    async def _headers(self, tr_id: str) -> dict:
        token = await self._oauth.get_token()
        return {
            "Authorization": f"Bearer {token.access_token.get_secret_value()}",
            "appkey": self._app_key.get_secret_value(),
            "appsecret": self._app_secret.get_secret_value(),
            "tr_id": tr_id,
            "Content-Type": "application/json; charset=utf-8",
        }

    async def fetch_daily(
        self,
        symbol: str,
        start_date: str,                   # YYYYMMDD
        end_date: str,                     # YYYYMMDD
        period: str = "D",                 # D/W/M/Y
        adjust_price: bool = True,         # 수정주가 적용
    ) -> list[OHLCV]:
        """국내주식 일봉/주봉/월봉/년봉 다운로드."""
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",          # 시장 분류 (J=주식)
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date,
            "FID_PERIOD_DIV_CODE": period,
            "FID_ORG_ADJ_PRC": "0" if adjust_price else "1",
        }
        return await self._fetch(
            path=_DAILY_PATH,
            tr_id=_TR_DAILY,
            params=params,
            symbol=symbol,
            parse_kind="daily",
        )

    async def fetch_minute(
        self,
        symbol: str,
        target_date: Optional[str] = None,    # YYYYMMDD (None=오늘)
        time_unit: str = "1",                  # 1/3/5/10/15/30/60 분
    ) -> list[OHLCV]:
        """국내주식 당일분봉. target_date 지정 시 해당일."""
        if time_unit not in {"1", "3", "5", "10", "15", "30", "60"}:
            raise ValueError(f"invalid time_unit: {time_unit}")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_HOUR_1": "1530",   # 마지막 조회 시각 (15:30)
            "FID_PW_DATA_INCU_YN": "Y",   # 포함 여부
        }
        if target_date:
            params["FID_INPUT_DATE_1"] = target_date
        return await self._fetch(
            path=_MINUTE_PATH,
            tr_id=_TR_MINUTE,
            params=params,
            symbol=symbol,
            parse_kind="minute",
        )

    async def _fetch(
        self,
        path: str,
        tr_id: str,
        params: dict,
        symbol: str,
        parse_kind: str,
    ) -> list[OHLCV]:
        client = self._http or httpx.AsyncClient(timeout=15)
        owns = self._http is None
        try:
            headers = await self._headers(tr_id)
            url = f"{self._oauth._base_url}{path}"
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error(
                "kiwoom candle fetch failed: tr_id=%s symbol=%s err=%s",
                tr_id, symbol, type(exc).__name__,
            )
            raise
        finally:
            if owns:
                await client.aclose()
            await asyncio.sleep(self._rate)

        if data.get("rt_cd") != "0":
            raise RuntimeError(
                f"kiwoom error: rt_cd={data.get('rt_cd')} msg={data.get('msg1')}"
            )

        rows = data.get("output2") or data.get("output") or []
        if parse_kind == "daily":
            return [_parse_daily_row(symbol, r) for r in rows if r.get("stck_bsop_date")]
        return [_parse_minute_row(symbol, r) for r in rows if r.get("stck_bsop_date")]


def _parse_daily_row(symbol: str, r: dict) -> OHLCV:
    """일봉 응답 row → OHLCV."""
    date_str = r["stck_bsop_date"]   # YYYYMMDD
    ts = datetime.strptime(date_str, "%Y%m%d")
    return OHLCV(
        symbol=symbol, timestamp=ts,
        open=float(r["stck_oprc"]),
        high=float(r["stck_hgpr"]),
        low=float(r["stck_lwpr"]),
        close=float(r["stck_clpr"]),
        volume=float(r.get("acml_vol", 0)),
        market_type=MarketType.STOCK,
    )


def _parse_minute_row(symbol: str, r: dict) -> OHLCV:
    """분봉 응답 row → OHLCV."""
    date_str = r["stck_bsop_date"]                 # YYYYMMDD
    time_str = r.get("stck_cntg_hour", "090000")   # HHMMSS
    ts = datetime.strptime(date_str + time_str, "%Y%m%d%H%M%S")
    return OHLCV(
        symbol=symbol, timestamp=ts,
        open=float(r["stck_oprc"]),
        high=float(r["stck_hgpr"]),
        low=float(r["stck_lwpr"]),
        close=float(r["stck_prpr"]),
        volume=float(r.get("cntg_vol", 0)),
        market_type=MarketType.STOCK,
    )


__all__ = ["KiwoomCandleFetcher"]
