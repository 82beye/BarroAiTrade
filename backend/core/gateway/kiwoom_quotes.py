"""키움 자체 OpenAPI 읽기 전용 시세 TR 클라이언트 (티마 대시보드용).

★ 주문/매매와 완전 분리된 조회 전용. 주문 TR·계좌 경로는 절대 건드리지 않는다. ★

기존 kiwoom_native_* 패턴(OAuth 토큰 공유 캐시, rc=3 인증 재시도, 429 백오프)을
그대로 따르며, 모든 메서드는 **실패·키 부재 시 예외를 삼키고 None(또는 빈 값)** 을
반환한다(우아한 degradation). 도메인/키는 호출측이 KiwoomNativeOAuth 로 주입.

대상 TR (문서 확인 완료):
  ka10001 주식기본정보    /api/dostk/stkinfo
  ka10099 종목정보리스트   /api/dostk/stkinfo   (cont-yn 페이지네이션)
  ka10004 주식호가        /api/dostk/mrkcond
  ka10003 체결정보        /api/dostk/stkinfo
  ka10046 체결강도(시간별) /api/dostk/mrkcond
  ka10047 체결강도(일별)   /api/dostk/mrkcond
  ka10054 VI발동종목      /api/dostk/stkinfo
  ka10002 주식거래원      /api/dostk/stkinfo
  ka10040 당일주요거래원   /api/dostk/rkinfo
  ka90004 종목별프로그램매매현황  /api/dostk/stkinfo
  ka90008 종목시간별프로그램매매추이 /api/dostk/mrkcond
  ka90013 종목일별프로그램매매추이  /api/dostk/mrkcond
  ka10063 장중투자자별매매  /api/dostk/mrkcond
  ka10066 장마감후투자자별매매 /api/dostk/mrkcond
  ka20001 업종현재가      /api/dostk/sect
  ka20006 업종일봉        /api/dostk/chart
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import httpx

from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth

logger = logging.getLogger(__name__)

# ── URL 경로 ──────────────────────────────────────────────
_PATH_STKINFO = "/api/dostk/stkinfo"
_PATH_MRKCOND = "/api/dostk/mrkcond"
_PATH_RKINFO = "/api/dostk/rkinfo"
_PATH_SECT = "/api/dostk/sect"
_PATH_CHART = "/api/dostk/chart"

# ── TR ID ────────────────────────────────────────────────
_TR_STOCK_INFO = "ka10001"
_TR_STOCK_LIST = "ka10099"
_TR_ORDERBOOK = "ka10004"
_TR_TICKS = "ka10003"
_TR_STRENGTH_TIME = "ka10046"
_TR_STRENGTH_DAILY = "ka10047"
_TR_VI = "ka10054"
_TR_BROKERS = "ka10002"
_TR_MAIN_BROKERS = "ka10040"
_TR_PROG_STATUS = "ka90004"
_TR_PROG_TIME = "ka90008"
_TR_PROG_DAILY = "ka90013"
_TR_INVESTOR_INTRADAY = "ka10063"
_TR_INVESTOR_AFTER = "ka10066"
_TR_RANK_VALUE = "ka10032"
_TR_RANK_FLU = "ka10027"
_TR_INDEX_PRICE = "ka20001"
_TR_INDEX_DAILY = "ka20006"

_MAX_429_RETRY = 3
_BACKOFF_BASE = 1.0


# ══════════════════════════════════════════════════════════
# 파싱 헬퍼 — 키움 응답은 부호가 앞에 붙은 문자열("+1.84","-5689","--3472")
# ══════════════════════════════════════════════════════════
def normalize_symbol(symbol: str) -> str:
    """'005930_AL' → '005930'."""
    if not symbol:
        return ""
    return re.split(r"_", symbol, maxsplit=1)[0].strip()


def _num(v) -> float:
    """부호 포함 숫자 문자열 → float. '--3472' → -3472, '+1.84' → 1.84, '' → 0.0."""
    if v is None:
        return 0.0
    s = str(v).strip().replace(",", "")
    if not s:
        return 0.0
    # 키움 표기: '-'가 하나라도 있으면 음수('--3472'=−3472, 이중부호는 형식상 잔여).
    neg = "-" in s
    s = s.replace("+", "").replace("-", "")
    try:
        f = float(s)
    except ValueError:
        return 0.0
    return -f if neg else f


def _int(v) -> int:
    return int(_num(v))


def _abs_int(v) -> int:
    return abs(int(_num(v)))


def _opt_num(v) -> Optional[float]:
    """빈 값이면 None, 아니면 float."""
    if v is None or str(v).strip() == "":
        return None
    return _num(v)


def _abs_opt_num(v) -> Optional[float]:
    """빈 값이면 None, 아니면 |float| — 가격류 필드의 등락방향 기호(+/-) 제거."""
    n = _opt_num(v)
    return None if n is None else abs(n)


class KiwoomQuotes:
    """읽기 전용 시세 TR 클라이언트."""

    def __init__(
        self,
        oauth: KiwoomNativeOAuth,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout: float = 10.0,
        rate_limit_seconds: float = 0.0,
    ) -> None:
        self._oauth = oauth
        self._http = http_client
        self._timeout = timeout
        self._rate = rate_limit_seconds

    @property
    def base_url(self) -> str:
        return self._oauth.base_url

    # ── 공통 요청 헬퍼 ─────────────────────────────────────
    async def _post(
        self,
        tr_id: str,
        path: str,
        body: dict,
        cont_yn: str = "N",
        next_key: str = "",
    ) -> Optional[dict]:
        """단일 TR POST. 성공 시 (data, headers) 를 위해 _post_raw 사용.

        실패·키 부재·rc!=0 → None (예외 삼킴 + 로깅).
        """
        res = await self._post_raw(tr_id, path, body, cont_yn, next_key)
        return res[0] if res else None

    async def _post_raw(
        self,
        tr_id: str,
        path: str,
        body: dict,
        cont_yn: str = "N",
        next_key: str = "",
    ) -> Optional[tuple[dict, dict]]:
        """(data, response_headers) 반환. 실패 시 None. cont-yn 페이지네이션용."""
        try:
            token = await self._oauth.get_token()
        except Exception as exc:  # 키 부재/발급 실패 → 조용히 degrade
            logger.info("kiwoom_quotes 토큰 없음 tr=%s: %s", tr_id, type(exc).__name__)
            return None

        client = self._http or httpx.AsyncClient(timeout=self._timeout)
        owns = self._http is None
        url = f"{self._oauth.base_url}{path}"
        _auth_retried = False
        _r429 = 0
        try:
            while True:
                try:
                    resp = await client.post(
                        url,
                        headers={
                            "authorization": f"Bearer {token.access_token.get_secret_value()}",
                            "content-type": "application/json;charset=UTF-8",
                            "cont-yn": cont_yn,
                            "next-key": next_key,
                            "api-id": tr_id,
                        },
                        json=body,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPStatusError as exc:
                    if (
                        exc.response is not None
                        and exc.response.status_code == 429
                        and _r429 < _MAX_429_RETRY
                    ):
                        _r429 += 1
                        await asyncio.sleep(_BACKOFF_BASE * (2 ** (_r429 - 1)))
                        continue
                    logger.warning(
                        "kiwoom_quotes tr=%s http=%s",
                        tr_id,
                        getattr(exc.response, "status_code", "?"),
                    )
                    return None
                except Exception as exc:
                    logger.warning("kiwoom_quotes tr=%s err=%s", tr_id, type(exc).__name__)
                    return None

                rc = data.get("return_code")
                if rc == 3 and not _auth_retried:
                    _auth_retried = True
                    self._oauth.invalidate_token()
                    try:
                        token = await self._oauth.get_token()
                    except Exception:
                        return None
                    continue
                if rc != 0:
                    logger.info(
                        "kiwoom_quotes tr=%s rc=%s msg=%s",
                        tr_id,
                        rc,
                        data.get("return_msg"),
                    )
                    return None
                return data, dict(resp.headers)
        finally:
            if owns:
                await client.aclose()
            if self._rate > 0:
                await asyncio.sleep(self._rate)

    # ══════════════════════════════════════════════════════
    # ka10001 주식기본정보
    # ══════════════════════════════════════════════════════
    async def stock_info(self, symbol: str) -> Optional[dict]:
        data = await self._post(_TR_STOCK_INFO, _PATH_STKINFO, {"stk_cd": symbol})
        if not data:
            return None
        return parse_stock_info(data, symbol)

    # ══════════════════════════════════════════════════════
    # ka10099 종목정보 리스트 (cont-yn 페이지네이션) → {code: name}
    # ══════════════════════════════════════════════════════
    async def stock_names(self, mrkt_tp: str = "0", max_pages: int = 50) -> dict[str, str]:
        """시장별 전 종목 {code: name}. 실패 시 빈 dict.

        mrkt_tp: 0=코스피, 10=코스닥 (문서 기준).
        """
        out: dict[str, str] = {}
        cont_yn = "N"
        next_key = ""
        for _ in range(max_pages):
            res = await self._post_raw(
                _TR_STOCK_LIST, _PATH_STKINFO, {"mrkt_tp": mrkt_tp}, cont_yn, next_key
            )
            if not res:
                break
            data, headers = res
            for row in data.get("list") or []:
                code = normalize_symbol(str(row.get("code", "")))
                name = str(row.get("name", "")).strip()
                if code and name:
                    out[code] = name
            # 헤더 키는 소문자로 정규화됨
            cont = (headers.get("cont-yn") or "").strip()
            nk = (headers.get("next-key") or "").strip()
            if cont == "Y" and nk:
                cont_yn, next_key = "Y", nk
                continue
            break
        return out

    # ══════════════════════════════════════════════════════
    # ka10004 주식호가 (raw dict — 파싱은 라우트/파서에서)
    # ══════════════════════════════════════════════════════
    async def orderbook(self, symbol: str) -> Optional[dict]:
        data = await self._post(_TR_ORDERBOOK, _PATH_MRKCOND, {"stk_cd": symbol})
        if not data:
            return None
        return parse_orderbook_levels(data)

    # ══════════════════════════════════════════════════════
    # ka10003 체결정보 → 최근 체결틱
    # ══════════════════════════════════════════════════════
    async def ticks(self, symbol: str, limit: int = 10) -> Optional[list[dict]]:
        data = await self._post(_TR_TICKS, _PATH_STKINFO, {"stk_cd": symbol})
        if not data:
            return None
        rows = data.get("cntr_infr") or []
        out = []
        for r in rows[:limit]:
            out.append(
                {
                    "time": str(r.get("tm", "")),
                    "price": _abs_int(r.get("cur_prc")),
                    "qty": _int(r.get("cntr_trde_qty")),
                    "strength": _opt_num(r.get("cntr_str")),
                }
            )
        return out

    # ══════════════════════════════════════════════════════
    # ka10046 체결강도(시간별) → 최신값
    # ══════════════════════════════════════════════════════
    async def strength(self, symbol: str) -> Optional[dict]:
        """최신 체결강도 1건 {value, str_5min, str_20min, str_60min, time}."""
        data = await self._post(_TR_STRENGTH_TIME, _PATH_MRKCOND, {"stk_cd": symbol})
        if not data:
            return None
        rows = data.get("cntr_str_tm") or []
        if not rows:
            return None
        r = rows[0]  # 최신(시간 내림차순 가정)
        return {
            "value": _opt_num(r.get("cntr_str")),
            "str_5min": _opt_num(r.get("cntr_str_5min")),
            "str_20min": _opt_num(r.get("cntr_str_20min")),
            "str_60min": _opt_num(r.get("cntr_str_60min")),
            "time": str(r.get("cntr_tm", "")),
        }

    async def strength_daily(self, symbol: str) -> Optional[list[dict]]:
        data = await self._post(_TR_STRENGTH_DAILY, _PATH_MRKCOND, {"stk_cd": symbol})
        if not data:
            return None
        out = []
        for r in data.get("cntr_str_daly") or []:
            out.append(
                {
                    "date": str(r.get("dt", "")),
                    "value": _opt_num(r.get("cntr_str")),
                    "str_5": _opt_num(r.get("cntr_str_5min")),
                    "str_20": _opt_num(r.get("cntr_str_20min")),
                    "str_60": _opt_num(r.get("cntr_str_60min")),
                }
            )
        return out

    # ══════════════════════════════════════════════════════
    # ka10054 VI발동종목
    # ══════════════════════════════════════════════════════
    async def vi_stocks(
        self, mrkt_tp: str = "000", motn_tp: str = "0", stex_tp: str = "3"
    ) -> Optional[list[dict]]:
        body = {
            "mrkt_tp": mrkt_tp,
            "bf_mkrt_tp": "0",
            "stk_cd": "",
            "motn_tp": motn_tp,
            "skip_stk": "000000000",
            "trde_qty_tp": "0",
            "min_trde_qty": "0",
            "max_trde_qty": "0",
            "trde_prica_tp": "0",
            "min_trde_prica": "0",
            "max_trde_prica": "0",
            "motn_drc": "0",
            "stex_tp": stex_tp,
        }
        data = await self._post(_TR_VI, _PATH_STKINFO, body)
        if not data:
            return None
        out = []
        for r in data.get("motn_stk") or []:
            out.append(
                {
                    "symbol": normalize_symbol(str(r.get("stk_cd", ""))),
                    "name": str(r.get("stk_nm", "")),
                    "motion_price": _abs_int(r.get("motn_pric")),
                    "type": str(r.get("viaplc_tp", "")),
                    "release_time": str(r.get("virelis_time", "")),
                    "trigger_time": str(r.get("trde_cntr_proc_time", "")),
                    "dynamic_rate": _opt_num(r.get("dynm_dispty_rt")),
                    "static_rate": _opt_num(r.get("static_dispty_rt")),
                    "count": _int(r.get("vimotn_cnt")),
                }
            )
        return out

    # ══════════════════════════════════════════════════════
    # ka10040 당일주요거래원 (외국계 추정 포함)
    # ══════════════════════════════════════════════════════
    async def brokers(self, symbol: str) -> Optional[dict]:
        data = await self._post(_TR_MAIN_BROKERS, _PATH_RKINFO, {"stk_cd": symbol})
        if not data:
            return None
        return parse_main_brokers(data)

    # ka10002 주식거래원 (폴백 — 외국계 추정 없음)
    async def brokers_basic(self, symbol: str) -> Optional[dict]:
        data = await self._post(_TR_BROKERS, _PATH_STKINFO, {"stk_cd": symbol})
        if not data:
            return None
        sell, buy = [], []
        for n in range(1, 6):
            sn = str(data.get(f"sel_trde_ori_nm_{n}", "")).strip()
            sq = _abs_int(data.get(f"sel_trde_qty_{n}"))
            if sn:
                sell.append({"name": sn, "qty": sq})
            bn = str(data.get(f"buy_trde_ori_nm_{n}", "")).strip()
            bq = _abs_int(data.get(f"buy_trde_qty_{n}"))
            if bn:
                buy.append({"name": bn, "qty": bq})
        return {"sell": sell, "buy": buy, "foreign_sell": None, "foreign_buy": None}

    # ══════════════════════════════════════════════════════
    # ka90008 / ka90013 프로그램매매 추이
    # ══════════════════════════════════════════════════════
    async def program_time(
        self, symbol: str, date: str, amt_qty_tp: str = "1"
    ) -> Optional[list[dict]]:
        body = {"amt_qty_tp": amt_qty_tp, "stk_cd": symbol, "date": date}
        data = await self._post(_TR_PROG_TIME, _PATH_MRKCOND, body)
        if not data:
            return None
        return _parse_program(data.get("stk_tm_prm_trde_trnsn") or [], key="tm")

    async def program_daily(
        self, symbol: str, date: str = "", amt_qty_tp: str = "1"
    ) -> Optional[list[dict]]:
        body = {"amt_qty_tp": amt_qty_tp, "stk_cd": symbol, "date": date}
        data = await self._post(_TR_PROG_DAILY, _PATH_MRKCOND, body)
        if not data:
            return None
        return _parse_program(data.get("stk_daly_prm_trde_trnsn") or [], key="dt")

    # ══════════════════════════════════════════════════════
    # ka10063 장중 / ka10066 장마감후 투자자별매매 (종목별 리스트)
    # ══════════════════════════════════════════════════════
    async def investors_intraday(
        self, mrkt_tp: str, invsr: str, stex_tp: str = "3"
    ) -> Optional[list[dict]]:
        body = {
            "mrkt_tp": mrkt_tp,
            "amt_qty_tp": "1",
            "invsr": invsr,
            "frgn_all": "0",
            "smtm_netprps_tp": "0",
            "stex_tp": stex_tp,
        }
        data = await self._post(_TR_INVESTOR_INTRADAY, _PATH_MRKCOND, body)
        if not data:
            return None
        return data.get("opmr_invsr_trde") or []

    async def investors_after(
        self, mrkt_tp: str, trde_tp: str = "0", stex_tp: str = "3"
    ) -> Optional[list[dict]]:
        body = {
            "mrkt_tp": mrkt_tp,
            "amt_qty_tp": "1",
            "trde_tp": trde_tp,
            "stex_tp": stex_tp,
        }
        data = await self._post(_TR_INVESTOR_AFTER, _PATH_MRKCOND, body)
        if not data:
            return None
        return data.get("opaf_invsr_trde") or []

    # ══════════════════════════════════════════════════════
    # ka20001 업종현재가 (지수)
    # ══════════════════════════════════════════════════════
    async def index_price(self, mrkt_tp: str, inds_cd: str) -> Optional[dict]:
        body = {"mrkt_tp": mrkt_tp, "inds_cd": inds_cd}
        data = await self._post(_TR_INDEX_PRICE, _PATH_SECT, body)
        if not data:
            return None
        return {
            "value": _opt_num(data.get("cur_prc")),
            "change": _opt_num(data.get("pred_pre")),
            "change_pct": _opt_num(data.get("flu_rt")),
            "open": _opt_num(data.get("open_pric")),
            "high": _opt_num(data.get("high_pric")),
            "low": _opt_num(data.get("low_pric")),
            "volume": _opt_num(data.get("trde_qty")),
            "value_traded": _opt_num(data.get("trde_prica")),
            "rising": _int(data.get("rising")),
            "falling": _int(data.get("fall")),
            "unchanged": _int(data.get("stdns")),
        }

    async def index_daily(self, inds_cd: str, base_dt: str) -> Optional[list[dict]]:
        body = {"inds_cd": inds_cd, "base_dt": base_dt}
        data = await self._post(_TR_INDEX_DAILY, _PATH_CHART, body)
        if not data:
            return None
        out = []
        # 지수값은 100배 정수 문자열 → /100
        for r in data.get("inds_dt_pole_qry") or []:
            out.append(
                {
                    "date": str(r.get("dt", "")),
                    "open": _num(r.get("open_pric")) / 100.0,
                    "high": _num(r.get("high_pric")) / 100.0,
                    "low": _num(r.get("low_pric")) / 100.0,
                    "close": _num(r.get("cur_prc")) / 100.0,
                    "volume": _num(r.get("trde_qty")),
                }
            )
        return out

    # ══════════════════════════════════════════════════════
    # ka10032 거래대금상위 / ka10027 등락률상위 — 랭킹 목록
    #   stex_tp: 1=KRX, 2=NXT, 3=통합 (NXT 애프터마켓 목록에 stex_tp="2" 사용)
    # ══════════════════════════════════════════════════════
    async def ranking(
        self,
        filter: str = "value",
        stex_tp: str = "2",
        mrkt_tp: str = "000",
        limit: int = 30,
    ) -> Optional[list[dict]]:
        """랭킹 목록. filter: value(거래대금)|gainers(상승률)|losers(하락률).

        반환 항목: {symbol, name, price, change_pct, value_traded(억원)}.
        실패/키 부재 → None. 문서상 미제공 값(aft_value 등)은 넣지 않는다.
        """
        if filter == "value":
            body = {"mrkt_tp": mrkt_tp, "mang_stk_incls": "0", "stex_tp": stex_tp}
            data = await self._post(_TR_RANK_VALUE, _PATH_RKINFO, body)
            rows = (data or {}).get("trde_prica_upper")
        else:
            body = {
                "mrkt_tp": mrkt_tp,
                "sort_tp": "1" if filter == "gainers" else "3",
                "trde_qty_cnd": "0000",
                "stk_cnd": "0",
                "crd_cnd": "0",
                "updown_incls": "1",
                "pric_cnd": "0",
                "trde_prica_cnd": "0",
                "stex_tp": stex_tp,
            }
            data = await self._post(_TR_RANK_FLU, _PATH_RKINFO, body)
            rows = (data or {}).get("pred_pre_flu_rt_upper")
        if data is None:
            return None
        return parse_ranking(rows or [], limit)


# ══════════════════════════════════════════════════════════
# 순수 파서 (테스트에서 HTTP 없이 검증)
# ══════════════════════════════════════════════════════════
def parse_ranking(rows: list, limit: int = 30) -> list[dict]:
    """ka10032/ka10027 랭킹 행 → 공통 목록 항목."""
    out = []
    for r in rows[:limit]:
        prica = _opt_num(r.get("trde_prica"))
        out.append(
            {
                "symbol": str(r.get("stk_cd", "")).strip(),
                "name": str(r.get("stk_nm", "")).strip(),
                "price": abs(_num(r.get("cur_prc"))),
                "change_pct": _opt_num(r.get("flu_rt")),
                # trde_prica 단위: 백만원 → 억원
                "value_traded": round(abs(prica) / 100.0, 2) if prica is not None else None,
            }
        )
    return out


def parse_stock_info(data: dict, symbol: str) -> dict:
    """ka10001 응답 → fundamental dict."""
    return {
        "symbol": normalize_symbol(symbol),
        "name": str(data.get("stk_nm", "")).strip(),
        "market_cap": _opt_num(data.get("mac")),          # 시가총액(억원)
        "capital": _opt_num(data.get("cap")),             # 자본금(억원)
        "listed_shares": _opt_num(data.get("flo_stk")),   # 상장주식(천주)
        "float_ratio": _opt_num(data.get("dstr_rt")),     # 유통비율(%)
        "float_shares": _opt_num(data.get("dstr_stk")),   # 유통주식(주)
        "foreign_ratio": _opt_num(data.get("for_exh_rt")),  # 외인소진율(%)
        "per": _opt_num(data.get("per")),
        "pbr": _opt_num(data.get("pbr")),
        "eps": _opt_num(data.get("eps")),
        "roe": _opt_num(data.get("roe")),
        "bps": _opt_num(data.get("bps")),
        "price": _abs_opt_num(data.get("cur_prc")),
        "change_pct": _opt_num(data.get("flu_rt")),
        # ref 블록 재료 — 가격류는 부호(등락방향 표기) 제거, change_pct 만 방향 유지
        "base_price": _abs_opt_num(data.get("base_pric")),
        "open": _abs_opt_num(data.get("open_pric")),
        "high": _abs_opt_num(data.get("high_pric")),
        "low": _abs_opt_num(data.get("low_pric")),
        "upper_limit": _abs_opt_num(data.get("upl_pric")),
        "lower_limit": _abs_opt_num(data.get("lst_pric")),
        "high_52w": _abs_opt_num(data.get("250hgst")),
        "low_52w": _abs_opt_num(data.get("250lwst")),
    }


def parse_orderbook_levels(data: dict, levels: int = 10) -> dict:
    """ka10004 응답 → {asks, bids, ref, base_time}.

    레벨1 = sel_fpr_bid/buy_fpr_bid(최우선), 레벨2~10 = sel_{n}th/buy_{n}th.
    asks: (price, qty) 매도 오름차순, bids: 매수 내림차순.
    ka10004 응답에는 기준가/시가/고저/상하한/예상VI 필드가 없다 → ref 는 여기서 채우지 않음.
    """
    asks: list[tuple[float, float]] = []
    bids: list[tuple[float, float]] = []

    # 레벨1 (최우선)
    ap1 = _abs_int(data.get("sel_fpr_bid"))
    aq1 = _abs_int(data.get("sel_fpr_req"))
    if ap1 > 0 and aq1 > 0:
        asks.append((float(ap1), float(aq1)))
    bp1 = _abs_int(data.get("buy_fpr_bid"))
    bq1 = _abs_int(data.get("buy_fpr_req"))
    if bp1 > 0 and bq1 > 0:
        bids.append((float(bp1), float(bq1)))

    for i in range(2, levels + 1):
        ap = _abs_int(data.get(f"sel_{i}th_pre_bid"))
        aq = _abs_int(data.get(f"sel_{i}th_pre_req"))
        if ap > 0 and aq > 0:
            asks.append((float(ap), float(aq)))
        bp = _abs_int(data.get(f"buy_{i}th_pre_bid"))
        bq = _abs_int(data.get(f"buy_{i}th_pre_req"))
        if bp > 0 and bq > 0:
            bids.append((float(bp), float(bq)))

    asks.sort(key=lambda x: x[0])
    bids.sort(key=lambda x: -x[0])
    return {
        "asks": asks,
        "bids": bids,
        "total_ask_qty": _abs_int(data.get("tot_sel_req")),
        "total_bid_qty": _abs_int(data.get("tot_buy_req")),
        "base_time": str(data.get("bid_req_base_tm", "")),
    }


def parse_main_brokers(data: dict) -> dict:
    """ka10040 응답 → {sell, buy, foreign_sell, foreign_buy}."""
    sell, buy = [], []
    for n in range(1, 6):
        sn = str(data.get(f"sel_trde_ori_{n}", "")).strip()
        sq = _abs_int(data.get(f"sel_trde_ori_qty_{n}"))
        if sn:
            sell.append({"name": sn, "qty": sq})
        bn = str(data.get(f"buy_trde_ori_{n}", "")).strip()
        bq = _abs_int(data.get(f"buy_trde_ori_qty_{n}"))
        if bn:
            buy.append({"name": bn, "qty": bq})
    return {
        "sell": sell,
        "buy": buy,
        "foreign_sell": _opt_num(data.get("frgn_sel_prsm_sum")),
        "foreign_buy": _opt_num(data.get("frgn_buy_prsm_sum")),
    }


def _parse_program(rows: list, key: str) -> list[dict]:
    """ka90008(key='tm') / ka90013(key='dt') 공통 파서."""
    out = []
    for r in rows:
        out.append(
            {
                "time_or_date": str(r.get(key, "")),
                "price": _abs_int(r.get("cur_prc")),
                "change_pct": _opt_num(r.get("flu_rt")),
                "volume": _int(r.get("trde_qty")),
                # 금액(백만원) 우선, 없으면 수량
                "net_buy": _num(r.get("prm_netprps_amt")),
                "net_buy_delta": _num(r.get("prm_netprps_amt_irds")),
                "net_buy_qty": _num(r.get("prm_netprps_qty")),
                "sell_amt": _num(r.get("prm_sell_amt")),
                "buy_amt": _num(r.get("prm_buy_amt")),
            }
        )
    return out


__all__ = [
    "KiwoomQuotes",
    "normalize_symbol",
    "parse_stock_info",
    "parse_orderbook_levels",
    "parse_main_brokers",
]
