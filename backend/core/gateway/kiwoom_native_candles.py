"""BAR-OPS-10 — 키움 자체 OpenAPI 캔들 다운로더.

검증 (2026-05-08, mockapi.kiwoom.com):
  - ka10081 (주식일봉차트조회): POST /api/dostk/chart, body {stk_cd, base_dt, upd_stkpc_tp}
    응답: stk_dt_pole_chart_qry[] {dt, open_pric, high_pric, low_pric, cur_prc, trde_qty, ...}
  - ka10080 (주식분봉차트조회): POST /api/dostk/chart, body {stk_cd, tic_scope, upd_stkpc_tp}
    응답: stk_min_pole_chart_qry[] {cntr_tm, open_pric, high_pric, low_pric, cur_prc, trde_qty, ...}

⚠️ 응답 가격 필드는 등락 부호(`-`/`+`) prefix — abs 정규화 필수.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, date
from typing import Optional

import httpx
from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.models.market import MarketType, OHLCV

logger = logging.getLogger(__name__)


_TR_DAILY = "ka10081"
_TR_MINUTE = "ka10080"
_CHART_PATH = "/api/dostk/chart"
_VALID_TIC = {"1", "3", "5", "10", "15", "30", "45", "60"}


def _abs_int(s: str) -> int:
    """가격 부호 정규화 — '-268500' → 268500."""
    s = (s or "0").lstrip("+-")
    return int(s) if s else 0


class KiwoomNativeCandleFetcher:
    """키움 자체 OpenAPI 캔들 다운로더."""

    def __init__(
        self,
        oauth: KiwoomNativeOAuth,
        http_client: Optional[httpx.AsyncClient] = None,
        rate_limit_seconds: float = 0.25,
    ) -> None:
        self._oauth = oauth
        self._http = http_client
        self._rate = rate_limit_seconds

    async def fetch_daily(
        self,
        symbol: str,
        base_dt: Optional[str] = None,           # YYYYMMDD (None=오늘)
        adjust_price: bool = True,
    ) -> list[OHLCV]:
        if base_dt is None:
            base_dt = date.today().strftime("%Y%m%d")
        return await self._fetch(
            tr_id=_TR_DAILY,
            body={"stk_cd": symbol, "base_dt": base_dt, "upd_stkpc_tp": "1" if adjust_price else "0"},
            symbol=symbol,
            list_key="stk_dt_pole_chart_qry",
            parse_kind="daily",
        )

    async def fetch_minute(
        self,
        symbol: str,
        tic_scope: str = "1",                     # 1/3/5/10/15/30/45/60 분
        adjust_price: bool = True,
    ) -> list[OHLCV]:
        if tic_scope not in _VALID_TIC:
            raise ValueError(f"invalid tic_scope: {tic_scope}, must be one of {_VALID_TIC}")
        return await self._fetch(
            tr_id=_TR_MINUTE,
            body={"stk_cd": symbol, "tic_scope": tic_scope, "upd_stkpc_tp": "1" if adjust_price else "0"},
            symbol=symbol,
            list_key="stk_min_pole_chart_qry",
            parse_kind="minute",
        )

    async def _fetch(
        self,
        tr_id: str,
        body: dict,
        symbol: str,
        list_key: str,
        parse_kind: str,
    ) -> list[OHLCV]:
        token = await self._oauth.get_token()
        client = self._http or httpx.AsyncClient(timeout=15)
        owns = self._http is None
        url = f"{self._oauth.base_url}{_CHART_PATH}"
        try:
            resp = await client.post(
                url,
                headers={
                    "authorization": f"Bearer {token.access_token.get_secret_value()}",
                    "content-type": "application/json;charset=UTF-8",
                    "cont-yn": "N",
                    "next-key": "",
                    "api-id": tr_id,
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("kiwoom-native chart fetch failed: tr=%s sym=%s err=%s",
                         tr_id, symbol, type(exc).__name__)
            raise
        finally:
            if owns:
                await client.aclose()
            await asyncio.sleep(self._rate)

        if data.get("return_code") != 0:
            raise RuntimeError(
                f"kiwoom-native error: rc={data.get('return_code')} msg={data.get('return_msg')}"
            )

        rows = data.get(list_key) or []
        parser = _parse_daily_row if parse_kind == "daily" else _parse_minute_row
        # 응답 정렬: 최신 → 과거. 시뮬용은 시간 오름차순 필요 → reverse.
        out = [parser(symbol, r) for r in rows]
        out.sort(key=lambda c: c.timestamp)
        return out


def _parse_daily_row(symbol: str, r: dict) -> OHLCV:
    ts = datetime.strptime(r["dt"], "%Y%m%d")
    return OHLCV(
        symbol=symbol, timestamp=ts,
        open=float(_abs_int(r["open_pric"])),
        high=float(_abs_int(r["high_pric"])),
        low=float(_abs_int(r["low_pric"])),
        close=float(_abs_int(r["cur_prc"])),
        volume=float(_abs_int(r.get("trde_qty", "0"))),
        market_type=MarketType.STOCK,
    )


def _parse_minute_row(symbol: str, r: dict) -> OHLCV:
    ts = datetime.strptime(r["cntr_tm"], "%Y%m%d%H%M%S")
    return OHLCV(
        symbol=symbol, timestamp=ts,
        open=float(_abs_int(r["open_pric"])),
        high=float(_abs_int(r["high_pric"])),
        low=float(_abs_int(r["low_pric"])),
        close=float(_abs_int(r["cur_prc"])),
        volume=float(_abs_int(r.get("trde_qty", "0"))),
        market_type=MarketType.STOCK,
    )


__all__ = ["KiwoomNativeCandleFetcher"]
