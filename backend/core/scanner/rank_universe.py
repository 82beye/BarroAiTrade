"""당일 순위정보 기반 스캔 유니버스 Provider — 2026-05-31 신규.

키움 REST 순위정보(POST /api/dostk/rkinfo) 의 "당일" 순위 API 다종을 호출해
**스캔 대상 종목 유니버스(종목코드 합집합)** 를 구성한다. SupertrendScanner 등
`scan(symbols)` 형 스캐너에 공급하는 종목 리스트 생성기.

KiwoomNativeLeaderPicker 와의 차이:
  - LeaderPicker: 순위 3종을 결합 점수로 **주도주 소수(N개)** 선정 (집중).
  - RankUniverseProvider: 순위 4종을 **합집합으로 넓은 유니버스** 구성 (커버리지).
    → 스캐너가 이 유니버스 전체를 훑어 전략 신호를 탐색.

포함 순위 API (당일 기준):
  - ka10032 거래대금상위   (list_key=trde_prica_upper)   — 자금 유입
  - ka10030 당일거래량상위 (list_key=tdy_trde_qty_upper)  — 거래 활발
  - ka10027 전일대비등락률상위 (list_key=pred_pre_flu_rt_upper) — 강세
  - ka10023 거래량급증     (list_key=trde_qty_sdnin)      — 신규 모멘텀

검증: 단위 테스트(mock 응답)만. 실 API 호출은 운영 머신에서 (개발 머신 무송출).
종목코드 정규화: '005930_AL' → '005930' (kiwoom_native_rank._normalize_symbol 재사용).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.gateway.kiwoom_native_rank import _normalize_symbol

logger = logging.getLogger(__name__)


_RANK_PATH = "/api/dostk/rkinfo"


@dataclass(frozen=True)
class RankSource:
    """순위 API 1종 정의 — TR ID + 요청 body + 응답 LIST key."""

    tr_id: str
    list_key: str
    body: dict
    label: str = ""


# ─── 당일 순위 4종 기본 정의 (키움 문서 스펙) ────────────────────────────────
# mrkt_tp 000=전체, stex_tp 3=통합(KRX+NXT). sort_tp 1=상위/급증.
SRC_TRADING_VALUE = RankSource(
    tr_id="ka10032", list_key="trde_prica_upper", label="거래대금상위",
    body={
        "mrkt_tp": "000", "mang_stk_incls": "0", "stex_tp": "3",
    },
)
SRC_VOLUME = RankSource(
    tr_id="ka10030", list_key="tdy_trde_qty_upper", label="당일거래량상위",
    body={
        "mrkt_tp": "000", "sort_tp": "1", "mang_stk_incls": "0", "crd_tp": "0",
        "trde_qty_tp": "0", "pric_tp": "0", "trde_prica_tp": "0",
        "mrkt_open_tp": "0", "stex_tp": "3",
    },
)
SRC_FLUCT_RATE = RankSource(
    tr_id="ka10027", list_key="pred_pre_flu_rt_upper", label="전일대비등락률상위",
    body={
        "mrkt_tp": "000", "sort_tp": "1", "stk_cnd": "0", "trde_qty_cnd": "0000",
        "crd_cnd": "0", "updown_incls": "1", "pric_cnd": "0", "trde_prica_cnd": "0",
        "stex_tp": "3",
    },
)
# ka10023 거래량급증 — tm_tp 2=전일대비(tm 불필요), trde_qty_tp 5=50만주이상, sort_tp 1=급증량.
SRC_VOLUME_SURGE = RankSource(
    tr_id="ka10023", list_key="trde_qty_sdnin", label="거래량급증",
    body={
        "mrkt_tp": "000", "sort_tp": "1", "tm_tp": "2", "trde_qty_tp": "5",
        "stk_cnd": "0", "pric_tp": "0", "stex_tp": "3",
    },
)

DEFAULT_SOURCES: tuple[RankSource, ...] = (
    SRC_TRADING_VALUE, SRC_VOLUME, SRC_FLUCT_RATE, SRC_VOLUME_SURGE,
)


@dataclass
class RankUniverseProvider:
    """당일 순위 API 합집합으로 스캔 유니버스(종목코드 리스트) 생성.

    사용법:
        provider = RankUniverseProvider(oauth)
        symbols = await provider.fetch_universe(max_symbols=80)
        signals = await supertrend_scanner.scan(symbols)

    Args:
        oauth: 키움 네이티브 OAuth (토큰 발급/갱신).
        http_client: 외부 주입 httpx.AsyncClient (테스트/재사용). None 시 호출마다 생성.
        rate_limit_seconds: 순위 API 호출 간 최소 간격(키움 rate limit 보호).
        sources: 사용할 RankSource 목록. None 시 DEFAULT_SOURCES (당일 4종).
        per_source_limit: 소스별 상위 N개만 채택 (None=전체). 노이즈/과대 유니버스 방지.
    """

    oauth: KiwoomNativeOAuth
    http_client: Optional[httpx.AsyncClient] = None
    rate_limit_seconds: float = 0.25
    sources: tuple[RankSource, ...] = field(default=DEFAULT_SOURCES)
    per_source_limit: Optional[int] = 100

    async def fetch_universe(self, max_symbols: Optional[int] = None) -> list[str]:
        """순위 4종 합집합 종목코드 리스트. 소스 정의 순서(거래대금>거래량>등락률>급증) 우선.

        한 소스가 실패해도 나머지로 진행 (부분 실패 허용 — 유니버스 가용성 우선).
        반환 순서: 소스 우선순위 → 소스 내 순위. 중복 제거(첫 등장 유지).
        max_symbols 지정 시 앞에서 컷.
        """
        seen: set[str] = set()
        universe: list[str] = []
        for src in self.sources:
            try:
                rows = await self._fetch_rank(src)
            except Exception as exc:  # 부분 실패 허용
                logger.warning("rank universe 소스 실패 tr=%s(%s): %s",
                               src.tr_id, src.label, type(exc).__name__)
                continue
            if self.per_source_limit is not None:
                rows = rows[: self.per_source_limit]
            added = 0
            for r in rows:
                sym = _normalize_symbol(r.get("stk_cd", ""))
                if sym and sym not in seen:
                    seen.add(sym)
                    universe.append(sym)
                    added += 1
            logger.info("rank universe 소스 %s(%s): %d종목 → 누적 %d",
                        src.tr_id, src.label, added, len(universe))
        if max_symbols is not None:
            universe = universe[:max_symbols]
        logger.info("rank universe 확정: %d종목 (소스 %d종)", len(universe), len(self.sources))
        return universe

    async def _fetch_rank(self, src: RankSource) -> list[dict]:
        """단일 순위 API 호출 — 토큰 발급 + rc=3 인증실패 1회 재시도 + rate limit.

        KiwoomNativeLeaderPicker._fetch_rank 와 동일 패턴 (검증됨, 2026-05-08).
        """
        token = await self.oauth.get_token()
        client = self.http_client or httpx.AsyncClient(timeout=15)
        owns = self.http_client is None
        url = f"{self.oauth.base_url}{_RANK_PATH}"
        _auth_retried = False
        try:
            while True:
                try:
                    resp = await client.post(
                        url,
                        headers={
                            "authorization": f"Bearer {token.access_token.get_secret_value()}",
                            "content-type": "application/json;charset=UTF-8",
                            "cont-yn": "N",
                            "next-key": "",
                            "api-id": src.tr_id,
                        },
                        json=src.body,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    logger.error("rank universe fetch failed: tr=%s err=%s",
                                 src.tr_id, type(exc).__name__)
                    raise
                rc = data.get("return_code")
                if rc == 3 and not _auth_retried:
                    _auth_retried = True
                    logger.warning("rank universe 인증 실패 tr=%s — 토큰 재발급 후 재시도", src.tr_id)
                    self.oauth.invalidate_token()
                    token = await self.oauth.get_token()
                    continue
                break
        finally:
            if owns:
                await client.aclose()
            await asyncio.sleep(self.rate_limit_seconds)

        if data.get("return_code") != 0:
            raise RuntimeError(
                f"rank universe error: tr={src.tr_id} rc={data.get('return_code')} "
                f"msg={data.get('return_msg')}")
        return data.get(src.list_key) or []


__all__ = [
    "RankUniverseProvider",
    "RankSource",
    "DEFAULT_SOURCES",
    "SRC_TRADING_VALUE",
    "SRC_VOLUME",
    "SRC_FLUCT_RATE",
    "SRC_VOLUME_SURGE",
]
