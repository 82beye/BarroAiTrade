"""BAR-OPS-11 — 키움 자체 OpenAPI 주도주(Leader) 선정.

당일 시장 주도주를 거래대금 + 등락률 ranking 결합 점수로 자동 선정.

검증 (2026-05-08, mockapi.kiwoom.com):
- ka10032 거래대금상위 → POST /api/dostk/rkinfo, list_key=trde_prica_upper
- ka10027 전일대비등락률상위 → POST /api/dostk/rkinfo, list_key=pred_pre_flu_rt_upper

점수: 0.6 × (1 - 거래대금 rank/N) + 0.4 × (1 - 등락률 rank/N)
필터: 등락률 ≥ +1.0% (양봉 강세만)
종목코드 정규화: '005930_AL' → '005930' (`_AL`/`_NX` 통합거래소 마커 strip)
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx

from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth

logger = logging.getLogger(__name__)


_RANK_PATH = "/api/dostk/rkinfo"
_TR_TRADING_VALUE = "ka10032"   # 거래대금상위
_TR_FLUCT_RATE = "ka10027"      # 전일대비등락률상위
_TR_VOLUME = "ka10030"          # 당일거래량상위 (BAR-OPS-12)
_KEY_TRADING_VALUE = "trde_prica_upper"
_KEY_FLUCT_RATE = "pred_pre_flu_rt_upper"
_KEY_VOLUME = "tdy_trde_qty_upper"

_BODY_TRADING_VALUE = {
    "mrkt_tp": "000", "sort_tp": "1", "mang_stk_incls": "0", "crd_tp": "0",
    "trde_qty_tp": "0", "pric_tp": "0", "trde_prica_tp": "0",
    "mrkt_open_tp": "0", "stex_tp": "3",
}
_BODY_FLUCT_RATE = {
    "mrkt_tp": "000", "sort_tp": "1", "stk_cnd": "0", "trde_qty_cnd": "0000",
    "crd_cnd": "0", "updown_incls": "1", "pric_cnd": "0", "trde_prica_cnd": "0",
    "stex_tp": "3",
}
_BODY_VOLUME = {
    "mrkt_tp": "000", "sort_tp": "1", "mang_stk_incls": "0", "crd_tp": "0",
    "trde_qty_tp": "0", "pric_tp": "0", "trde_prica_tp": "0",
    "mrkt_open_tp": "0", "stex_tp": "3",
}


def _normalize_symbol(stk_cd: str) -> str:
    """`005930_AL` → `005930`."""
    return re.split(r"_", stk_cd, maxsplit=1)[0]


def _parse_signed_pct(s: str) -> float:
    """`+1.84` → 1.84, `-2.00` → -2.0, `0.00` → 0.0."""
    if not s:
        return 0.0
    try:
        return float(s.replace("+", ""))
    except ValueError:
        return 0.0


@dataclass(frozen=True)
class LeaderCandidate:
    symbol: str
    name: str
    cur_price: float
    flu_rate: float                       # 등락률 (%)
    rank_trade_value: Optional[int]
    rank_flu_rate: Optional[int]
    rank_volume: Optional[int]            # BAR-OPS-12 — 거래량 순위
    score: float


class KiwoomNativeLeaderPicker:
    """주도주 선정 — 거래대금 + 등락률 + 거래량 결합 ranking (3-factor)."""

    def __init__(
        self,
        oauth: KiwoomNativeOAuth,
        http_client: Optional[httpx.AsyncClient] = None,
        rate_limit_seconds: float = 0.25,
        weight_trade_value: float = 0.4,
        weight_flu_rate: float = 0.3,
        weight_volume: float = 0.3,        # BAR-OPS-12 — 거래량 가중
        min_flu_rate: float = 1.0,
        min_score: float = 0.0,            # BAR-OPS-12 — 절대 점수 threshold
    ) -> None:
        if abs(weight_trade_value + weight_flu_rate + weight_volume - 1.0) > 1e-6:
            raise ValueError("weights must sum to 1.0")
        self._oauth = oauth
        self._http = http_client
        self._rate = rate_limit_seconds
        self._w_tv = weight_trade_value
        self._w_fr = weight_flu_rate
        self._w_vol = weight_volume
        self._min_fr = min_flu_rate
        self._min_score = min_score

    async def pick(self, top_n: int = 5) -> list[LeaderCandidate]:
        tv_rows = await self._fetch_rank(_TR_TRADING_VALUE, _BODY_TRADING_VALUE, _KEY_TRADING_VALUE)
        fr_rows = await self._fetch_rank(_TR_FLUCT_RATE, _BODY_FLUCT_RATE, _KEY_FLUCT_RATE)
        vol_rows = await self._fetch_rank(_TR_VOLUME, _BODY_VOLUME, _KEY_VOLUME)

        def _build_rank(rows: list[dict]) -> tuple[dict[str, int], dict[str, dict]]:
            r_map: dict[str, int] = {}
            m_map: dict[str, dict] = {}
            for i, r in enumerate(rows, start=1):
                sym = _normalize_symbol(r.get("stk_cd", ""))
                if sym:
                    r_map[sym] = i
                    m_map[sym] = r
            return r_map, m_map

        tv_rank, tv_meta = _build_rank(tv_rows)
        fr_rank, fr_meta = _build_rank(fr_rows)
        vol_rank, vol_meta = _build_rank(vol_rows)

        n_tv = max(len(tv_rank), 1)
        n_fr = max(len(fr_rank), 1)
        n_vol = max(len(vol_rank), 1)
        symbols = set(tv_rank) | set(fr_rank) | set(vol_rank)

        out: list[LeaderCandidate] = []
        for sym in symbols:
            meta = tv_meta.get(sym) or fr_meta.get(sym) or vol_meta.get(sym, {})
            flu_rate = _parse_signed_pct(meta.get("flu_rt", "0"))
            if flu_rate < self._min_fr:
                continue
            tv_score = (1 - (tv_rank[sym] - 1) / n_tv) if sym in tv_rank else 0.0
            fr_score = (1 - (fr_rank[sym] - 1) / n_fr) if sym in fr_rank else 0.0
            vol_score = (1 - (vol_rank[sym] - 1) / n_vol) if sym in vol_rank else 0.0
            score = self._w_tv * tv_score + self._w_fr * fr_score + self._w_vol * vol_score
            if score < self._min_score:
                continue
            cur_price = abs(_parse_signed_pct(meta.get("cur_prc", "0")))
            out.append(LeaderCandidate(
                symbol=sym,
                name=meta.get("stk_nm", ""),
                cur_price=cur_price,
                flu_rate=flu_rate,
                rank_trade_value=tv_rank.get(sym),
                rank_flu_rate=fr_rank.get(sym),
                rank_volume=vol_rank.get(sym),
                score=score,
            ))

        out.sort(key=lambda c: c.score, reverse=True)
        return out[:top_n]

    async def _fetch_rank(self, tr_id: str, body: dict, list_key: str) -> list[dict]:
        token = await self._oauth.get_token()
        client = self._http or httpx.AsyncClient(timeout=15)
        owns = self._http is None
        url = f"{self._oauth.base_url}{_RANK_PATH}"
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
            logger.error("kiwoom-native rank failed: tr=%s err=%s", tr_id, type(exc).__name__)
            raise
        finally:
            if owns:
                await client.aclose()
            await asyncio.sleep(self._rate)

        if data.get("return_code") != 0:
            raise RuntimeError(f"kiwoom-native rank error: rc={data.get('return_code')} msg={data.get('return_msg')}")
        return data.get(list_key) or []


__all__ = ["KiwoomNativeLeaderPicker", "LeaderCandidate", "_normalize_symbol", "_parse_signed_pct"]
