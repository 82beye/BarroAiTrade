"""BAR-OPS-15 — 키움 자체 OpenAPI 계좌·잔고 조회 어댑터.

검증 (mockapi.kiwoom.com, 2026-05-08):
- kt00018 계좌평가현황: top-level 합계 + acnt_evlt_remn_indv_tot[] 종목별 평가
- kt00001 예수금상세현황: 예수금/증거금/보증금 + stk_entr_prst[] 종목별

응답 가격 필드는 등락 부호(`+`/`-`) prefix → abs 정규화.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

import httpx

from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth

logger = logging.getLogger(__name__)


_ACCT_PATH = "/api/dostk/acnt"
_TR_BALANCE = "kt00018"     # 계좌평가현황
_TR_DEPOSIT = "kt00001"     # 예수금상세현황
_TR_REALIZED = "ka10073"    # 종목별실현손익 (BAR-OPS-28)
_TR_DAILY_PNL = "ka10074"   # 일자별실현손익합산
_TR_OPEN_ORDERS = "ka10075" # 미체결요청 oso (2026-06-08: kt00004는 접수상태 미반환 → 교체)


def _abs_decimal(s: str) -> Decimal:
    """`+1000.50` → Decimal('1000.50'). `-` 부호도 abs."""
    if not s:
        return Decimal("0")
    s = s.strip().lstrip("+").lstrip("-")
    try:
        return Decimal(s)
    except (ValueError, ArithmeticError):
        return Decimal("0")


def _signed_decimal(s: str) -> Decimal:
    """등락 부호 보존 — `-1000` → -1000, `+500` → 500."""
    if not s:
        return Decimal("0")
    s = s.strip()
    sign = -1 if s.startswith("-") else 1
    s = s.lstrip("+").lstrip("-")
    try:
        return Decimal(s) * sign
    except (ValueError, ArithmeticError):
        return Decimal("0")


@dataclass(frozen=True)
class HoldingPosition:
    symbol: str
    name: str
    qty: int                       # 보유수량
    avg_buy_price: Decimal         # 평균매입가
    cur_price: Decimal             # 현재가 (abs)
    eval_amount: Decimal           # 평가금액
    pnl: Decimal                   # 평가손익 (signed)
    pnl_rate: Decimal              # 수익률 % (signed)


@dataclass(frozen=True)
class AccountBalance:
    total_purchase: Decimal        # 총 매입금액
    total_eval: Decimal            # 총 평가금액
    total_pnl: Decimal             # 총 평가손익 (signed)
    total_pnl_rate: Decimal        # 총 수익률 % (signed)
    estimated_deposit: Decimal     # 추정예수자산
    holdings: list[HoldingPosition] = field(default_factory=list)


@dataclass(frozen=True)
class AccountDeposit:
    cash: Decimal                  # 예수금
    margin_cash: Decimal           # 증거금현금
    bond_margin_cash: Decimal      # 보증금현금
    next_day_settlement: Decimal   # 익일정산금


@dataclass(frozen=True)
class OpenOrder:
    """미체결 주문 (BAR-OPS-33)."""
    order_no: str               # 주문번호
    symbol: str
    name: str
    side: str                   # "buy" / "sell" / "unknown"
    order_qty: int              # 주문수량
    filled_qty: int             # 체결수량
    pending_qty: int            # 미체결수량 = ord - filled
    order_price: Decimal        # 주문단가
    order_date: str             # YYYYMMDD


@dataclass(frozen=True)
class RealizedPnLEntry:
    """일자별 실현손익 단건 (BAR-OPS-28)."""
    date: str                      # YYYYMMDD
    symbol: str
    name: str
    qty: int                       # 체결수량
    buy_price: Decimal             # 매입단가
    sell_price: Decimal            # 체결가 (매도가)
    pnl: Decimal                   # 매도손익 (signed)
    pnl_rate: Decimal              # 손익률 % (signed)
    commission: Decimal            # 매매수수료
    tax: Decimal                   # 매매세금


@dataclass(frozen=True)
class DailyPnLEntry:
    """일자별 실현손익 합산 (ka10074)."""
    date: str                      # YYYYMMDD
    pnl_amount: Decimal            # 실현손익
    commission: Decimal            # 수수료
    tax: Decimal                   # 세금
    net_pnl: Decimal               # 순손익


class KiwoomNativeAccountFetcher:
    """키움 자체 OpenAPI 계좌·잔고 조회."""

    def __init__(
        self,
        oauth: KiwoomNativeOAuth,
        http_client: Optional[httpx.AsyncClient] = None,
        rate_limit_seconds: float = 0.25,
    ) -> None:
        self._oauth = oauth
        self._http = http_client
        self._rate = rate_limit_seconds

    async def fetch_balance(
        self, exchange: str = "KRX", qry_tp: str = "2",
    ) -> AccountBalance:
        if exchange not in {"KRX", "NXT", "SOR"}:
            raise ValueError(f"invalid exchange: {exchange}")
        data = await self._post(
            _TR_BALANCE,
            {"qry_tp": qry_tp, "dmst_stex_tp": exchange},
        )
        holdings = []
        for r in data.get("acnt_evlt_remn_indv_tot") or []:
            holdings.append(HoldingPosition(
                symbol=(r.get("stk_cd") or "").lstrip("A"),
                name=r.get("stk_nm", ""),
                qty=int(_abs_decimal(r.get("rmnd_qty", "0"))),
                avg_buy_price=_abs_decimal(r.get("pur_pric", "0")),
                cur_price=_abs_decimal(r.get("cur_prc", "0")),
                eval_amount=_abs_decimal(r.get("evlt_amt", "0")),
                pnl=_signed_decimal(r.get("evltv_prft", "0")),
                pnl_rate=_signed_decimal(r.get("prft_rt", "0")),
            ))
        return AccountBalance(
            total_purchase=_abs_decimal(data.get("tot_pur_amt", "0")),
            total_eval=_abs_decimal(data.get("tot_evlt_amt", "0")),
            total_pnl=_signed_decimal(data.get("tot_evlt_pl", "0")),
            total_pnl_rate=_signed_decimal(data.get("tot_prft_rt", "0")),
            estimated_deposit=_abs_decimal(data.get("prsm_dpst_aset_amt", "0")),
            holdings=holdings,
        )

    async def fetch_open_orders(
        self, exchange: str = "KRX", trade_type: str = "0",
    ) -> list[OpenOrder]:
        """미체결 주문 (ka10075 oso). trade_type: 0=전체/1=매수/2=매도.

        2026-06-08: kt00004 는 '접수' 상태 주문을 반환하지 않아(0건) 미체결이
        코드에서 비가시화됐다(접수정체 인시던트). ka10075(oso)로 교체 — 접수/미체결
        주문이 io_tp_nm(+매수/-매도)·oso_qty·cntr_qty 로 노출된다.
        """
        if exchange not in {"KRX", "NXT", "SOR"}:
            raise ValueError(f"invalid exchange: {exchange}")
        if trade_type not in {"0", "1", "2"}:
            raise ValueError(f"invalid trade_type: {trade_type}")
        data = await self._post(_TR_OPEN_ORDERS, {
            "all_stk_tp": "0",
            "trde_tp": "0",
            "stk_cd": "",
            "stex_tp": "0",
        })
        rows = data.get("oso")
        if not isinstance(rows, list):
            # 폴백: 구 kt00004 응답 키
            for key in ("stk_acnt_evlt_prst", "open_ordr", "ndchg_ordr"):
                rows = data.get(key)
                if isinstance(rows, list):
                    break
            else:
                rows = []

        out: list[OpenOrder] = []
        for r in rows:
            ord_qty = int(_abs_decimal(r.get("ord_qty", "0")))
            filled = int(_abs_decimal(r.get("cntr_qty", "0")))
            # ka10075: oso_qty=미체결수량(접수 포함). 없으면 ord-filled 로 보정.
            pending = int(_abs_decimal(r.get("oso_qty", str(max(ord_qty - filled, 0)))))
            io = r.get("io_tp_nm", "")            # "+매수" / "-매도"
            if "매수" in io:
                side = "buy"
            elif "매도" in io:
                side = "sell"
            else:
                tp = r.get("trde_tp", "")
                side = "buy" if tp == "1" else ("sell" if tp == "2" else "unknown")
            if trade_type == "1" and side != "buy":
                continue
            if trade_type == "2" and side != "sell":
                continue
            out.append(OpenOrder(
                order_no=r.get("ord_no", ""),
                symbol=(r.get("stk_cd") or "").lstrip("A"),
                name=r.get("stk_nm", ""),
                side=side,
                order_qty=ord_qty,
                filled_qty=filled,
                pending_qty=pending,
                order_price=_abs_decimal(r.get("ord_pric", r.get("ord_uv", "0"))),
                order_date=r.get("tm", r.get("ord_dt", "")),
            ))
        return out

    async def fetch_realized_pnl(
        self, start_date: str, end_date: str,
    ) -> list[RealizedPnLEntry]:
        """일자별 실현손익 (ka10073). YYYYMMDD."""
        if not (len(start_date) == 8 and start_date.isdigit()):
            raise ValueError(f"invalid start_date: {start_date} (YYYYMMDD)")
        if not (len(end_date) == 8 and end_date.isdigit()):
            raise ValueError(f"invalid end_date: {end_date} (YYYYMMDD)")
        data = await self._post(_TR_REALIZED, {
            "strt_dt": start_date, "end_dt": end_date,
        })
        out: list[RealizedPnLEntry] = []
        for r in data.get("dt_stk_rlzt_pl") or []:
            out.append(RealizedPnLEntry(
                date=r.get("dt", ""),
                symbol=(r.get("stk_cd") or "").lstrip("A"),
                name=r.get("stk_nm", ""),
                qty=int(_abs_decimal(r.get("cntr_qty", "0"))),
                buy_price=_abs_decimal(r.get("buy_uv", "0")),
                sell_price=_abs_decimal(r.get("cntr_pric", "0")),
                pnl=_signed_decimal(r.get("tdy_sel_pl", "0")),
                pnl_rate=_signed_decimal(r.get("pl_rt", "0")),
                commission=_abs_decimal(r.get("tdy_trde_cmsn", "0")),
                tax=_abs_decimal(r.get("tdy_trde_tax", "0")),
            ))
        return out

    async def fetch_daily_pnl(
        self, start_date: str, end_date: str,
    ) -> list[DailyPnLEntry]:
        """일자별 실현손익 합산 (ka10074). YYYYMMDD."""
        if not (len(start_date) == 8 and start_date.isdigit()):
            raise ValueError(f"invalid start_date: {start_date} (YYYYMMDD)")
        if not (len(end_date) == 8 and end_date.isdigit()):
            raise ValueError(f"invalid end_date: {end_date} (YYYYMMDD)")
        all_items: list[DailyPnLEntry] = []
        cont_yn_val = "N"
        next_key_val = ""
        while True:
            data = await self._post(
                _TR_DAILY_PNL,
                {"strt_dt": start_date, "end_dt": end_date},
                cont_yn=cont_yn_val,
                next_key=next_key_val,
            )
            # 응답 리스트 키 탐색
            rows: list = []
            for key in ("list", "output", "dt_rlzt_pl"):
                candidate = data.get(key)
                if isinstance(candidate, list):
                    rows = candidate
                    break
            for r in rows:
                dt = r.get("date", r.get("stdr_dt", ""))
                if not dt:
                    continue
                pnl_amt = _signed_decimal(r.get("pl_amt", r.get("rlzt_pfls", "0")))
                cmsn = _abs_decimal(r.get("cmsn_amt", r.get("cmsn", "0")))
                tax = _abs_decimal(r.get("tax_amt", r.get("tax", "0")))
                raw_net = r.get("net_pl_amt")
                net = _signed_decimal(raw_net) if raw_net is not None else (pnl_amt - cmsn - tax)
                all_items.append(DailyPnLEntry(
                    date=dt, pnl_amount=pnl_amt,
                    commission=cmsn, tax=tax, net_pnl=net,
                ))
            # 연속조회
            resp_cont = data.get("_cont_yn", "N")
            resp_next = data.get("_next_key", "")
            if resp_cont == "Y" and resp_next:
                cont_yn_val = "Y"
                next_key_val = resp_next
            else:
                break
        return all_items

    async def fetch_deposit(self, qry_tp: str = "3") -> AccountDeposit:
        data = await self._post(_TR_DEPOSIT, {"qry_tp": qry_tp})
        return AccountDeposit(
            cash=_abs_decimal(data.get("entr", "0")),
            margin_cash=_abs_decimal(data.get("profa_ch", "0")),
            bond_margin_cash=_abs_decimal(data.get("bncr_profa_ch", "0")),
            next_day_settlement=_abs_decimal(data.get("nxdy_bncr_sell_exct", "0")),
        )

    async def _post(
        self, tr_id: str, body: dict,
        cont_yn: str = "N", next_key: str = "",
    ) -> dict:
        token = await self._oauth.get_token()
        client = self._http or httpx.AsyncClient(timeout=15)
        owns = self._http is None
        url = f"{self._oauth.base_url}{_ACCT_PATH}"
        _retries = 3
        _retry_delay = 1.0
        _auth_retried = False
        try:
            for attempt in range(_retries):
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
                    if resp.status_code == 429 and attempt < _retries - 1:
                        wait = _retry_delay * (attempt + 1)
                        logger.warning("account 429 rate-limit tr=%s — %.1fs 후 재시도 (%d/%d)",
                                       tr_id, wait, attempt + 1, _retries)
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    # rc=3 인증 실패 → 토큰 무효화 후 1회 재시도
                    rc = data.get("return_code")
                    if rc == 3 and not _auth_retried:
                        _auth_retried = True
                        logger.warning("account 인증 실패 tr=%s — 토큰 재발급 후 재시도", tr_id)
                        self._oauth.invalidate_token()
                        token = await self._oauth.get_token()
                        continue
                    # 연속조회 헤더 보존
                    data["_cont_yn"] = resp.headers.get("cont-yn", "N")
                    data["_next_key"] = resp.headers.get("next-key", "")
                    break
                except Exception as exc:
                    if attempt < _retries - 1:
                        await asyncio.sleep(_retry_delay * (attempt + 1))
                        continue
                    logger.error("kiwoom-native account fetch failed: tr=%s err=%s",
                                 tr_id, type(exc).__name__)
                    raise
        finally:
            if owns:
                await client.aclose()
            await asyncio.sleep(self._rate)

        rc = data.get("return_code")
        if rc != 0:
            raise RuntimeError(f"kiwoom-native account error: tr={tr_id} rc={rc} msg={data.get('return_msg')}")
        return data


__all__ = [
    "KiwoomNativeAccountFetcher",
    "AccountBalance", "AccountDeposit", "HoldingPosition",
    "RealizedPnLEntry", "DailyPnLEntry", "OpenOrder",
    "_abs_decimal", "_signed_decimal",
]
