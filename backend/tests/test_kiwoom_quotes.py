"""kiwoom_quotes 읽기 전용 TR 클라이언트 테스트.

HTTP 는 fake client 로 대체하고, 문서 스펙 기반 모의 응답 파싱을 검증한다.
키 부재(토큰 발급 실패) 시 None degrade 경로도 확인.
"""
from __future__ import annotations

import pytest

from backend.core.gateway.kiwoom_quotes import (
    KiwoomQuotes,
    _num,
    normalize_symbol,
    parse_main_brokers,
    parse_orderbook_levels,
    parse_stock_info,
)


# ══════════════════════════════════════════════════════════
# fakes
# ══════════════════════════════════════════════════════════
class _FakeSecret:
    def get_secret_value(self):
        return "tok"


class _FakeToken:
    access_token = _FakeSecret()


class _FakeOAuth:
    def __init__(self, base_url="https://mockapi.kiwoom.com", fail=False):
        self._base = base_url
        self._fail = fail
        self.invalidated = 0

    @property
    def base_url(self):
        return self._base

    async def get_token(self):
        if self._fail:
            raise RuntimeError("no key")
        return _FakeToken()

    def invalidate_token(self):
        self.invalidated += 1


class _FakeResp:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {"cont-yn": "N", "next-key": ""}

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                "err", request=None, response=self
            )

    def json(self):
        return self._payload


class _FakeClient:
    """api-id 헤더로 큐잉된 응답을 반환. 리스트면 순차 pop."""

    def __init__(self, by_tr):
        self._by_tr = {k: list(v) if isinstance(v, list) else [v] for k, v in by_tr.items()}
        self.calls = []

    async def post(self, url, headers=None, json=None):
        tr = headers.get("api-id")
        self.calls.append((tr, json))
        queue = self._by_tr.get(tr)
        if not queue:
            return _FakeResp({"return_code": 0})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        return item

    async def aclose(self):
        pass


# ══════════════════════════════════════════════════════════
# 순수 파서 (문서 Response Example 기반)
# ══════════════════════════════════════════════════════════
def test_num_signed():
    assert _num("+1.84") == 1.84
    assert _num("-5689") == -5689
    assert _num("--3472") == -3472  # 이중부호 → 음수
    assert _num("") == 0.0
    assert _num("1,234") == 1234


def test_normalize_symbol():
    assert normalize_symbol("005930_AL") == "005930"
    assert normalize_symbol("005930") == "005930"


def test_parse_stock_info():
    # ka10001 필드명 기반 (예제 값은 문서상 정렬 불량이라 필드명만 검증)
    data = {
        "stk_nm": "삼성전자", "mac": "24352", "cap": "1311",
        "dstr_rt": "75.5", "per": "12.3", "pbr": "1.1",
        "cur_prc": "+75000", "flu_rt": "+1.20",
        "base_pric": "74000", "open_pric": "74500",
        "high_pric": "76000", "low_pric": "74000",
        "upl_pric": "96200", "lst_pric": "51800",
    }
    out = parse_stock_info(data, "005930")
    assert out["symbol"] == "005930"
    assert out["name"] == "삼성전자"
    assert out["market_cap"] == 24352
    assert out["float_ratio"] == 75.5
    assert out["price"] == 75000
    assert out["change_pct"] == 1.20
    assert out["base_price"] == 74000
    assert out["upper_limit"] == 96200
    assert out["lower_limit"] == 51800


def test_parse_orderbook_levels():
    # 레벨1=최우선(fpr), 레벨2=2th
    data = {
        "bid_req_base_tm": "162000",
        "sel_fpr_bid": "75100", "sel_fpr_req": "100",
        "sel_2th_pre_bid": "75200", "sel_2th_pre_req": "200",
        "buy_fpr_bid": "75000", "buy_fpr_req": "300",
        "buy_2th_pre_bid": "74900", "buy_2th_pre_req": "400",
        "tot_sel_req": "1000", "tot_buy_req": "2000",
    }
    ob = parse_orderbook_levels(data)
    assert ob["asks"][0] == (75100.0, 100.0)  # 매도 최저가 우선
    assert ob["asks"][1] == (75200.0, 200.0)
    assert ob["bids"][0] == (75000.0, 300.0)  # 매수 최고가 우선
    assert ob["bids"][1] == (74900.0, 400.0)
    assert ob["total_ask_qty"] == 1000
    assert ob["base_time"] == "162000"


def test_parse_main_brokers():
    # ka10040 Response Example
    data = {
        "sel_trde_ori_1": "모건스탠리", "sel_trde_ori_qty_1": "-5689",
        "buy_trde_ori_1": "모건스탠리", "buy_trde_ori_qty_1": "+6305",
        "sel_trde_ori_2": "신 영", "sel_trde_ori_qty_2": "-615",
        "buy_trde_ori_2": "키움증권", "buy_trde_ori_qty_2": "+100",
        "frgn_sel_prsm_sum": "-5689", "frgn_buy_prsm_sum": "+6305",
    }
    b = parse_main_brokers(data)
    assert b["sell"][0] == {"name": "모건스탠리", "qty": 5689}
    assert b["buy"][0] == {"name": "모건스탠리", "qty": 6305}
    assert len(b["sell"]) == 2
    assert b["foreign_sell"] == -5689
    assert b["foreign_buy"] == 6305


# ══════════════════════════════════════════════════════════
# 메서드 (fake client)
# ══════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_stock_info_via_client():
    client = _FakeClient({"ka10001": _FakeResp({
        "return_code": 0, "stk_nm": "삼성전자", "mac": "24352",
        "dstr_rt": "75.5", "cur_prc": "+75000", "flu_rt": "+1.2",
        "base_pric": "74000",
    })})
    q = KiwoomQuotes(oauth=_FakeOAuth(), http_client=client)
    info = await q.stock_info("005930")
    assert info["name"] == "삼성전자"
    assert info["market_cap"] == 24352


@pytest.mark.asyncio
async def test_orderbook_via_client():
    client = _FakeClient({"ka10004": _FakeResp({
        "return_code": 0,
        "sel_fpr_bid": "75100", "sel_fpr_req": "100",
        "buy_fpr_bid": "75000", "buy_fpr_req": "300",
        "tot_sel_req": "1000", "tot_buy_req": "2000",
    })})
    q = KiwoomQuotes(oauth=_FakeOAuth(), http_client=client)
    ob = await q.orderbook("005930")
    assert ob["asks"] == [(75100.0, 100.0)]
    assert ob["bids"] == [(75000.0, 300.0)]


@pytest.mark.asyncio
async def test_index_price_via_client():
    client = _FakeClient({"ka20001": _FakeResp({
        "return_code": 0, "cur_prc": "-2394.49", "pred_pre": "-278.47",
        "flu_rt": "-10.42", "open_pric": "-2669.53",
        "rising": "17", "fall": "130", "stdns": "183",
    })})
    q = KiwoomQuotes(oauth=_FakeOAuth(), http_client=client)
    idx = await q.index_price("0", "001")
    assert idx["value"] == -2394.49
    assert idx["change_pct"] == -10.42
    assert idx["rising"] == 17


@pytest.mark.asyncio
async def test_index_daily_scaling():
    # 지수 100배 정수 → /100
    client = _FakeClient({"ka20006": _FakeResp({
        "return_code": 0, "inds_cd": "001",
        "inds_dt_pole_qry": [
            {"dt": "20250210", "cur_prc": "252127", "open_pric": "251064",
             "high_pric": "252733", "low_pric": "249918", "trde_qty": "393564"},
        ],
    })})
    q = KiwoomQuotes(oauth=_FakeOAuth(), http_client=client)
    rows = await q.index_daily("001", "20250905")
    assert rows[0]["close"] == 2521.27
    assert rows[0]["open"] == 2510.64


@pytest.mark.asyncio
async def test_brokers_via_client():
    client = _FakeClient({"ka10040": _FakeResp({
        "return_code": 0,
        "sel_trde_ori_1": "모건스탠리", "sel_trde_ori_qty_1": "-5689",
        "buy_trde_ori_1": "키움증권", "buy_trde_ori_qty_1": "+6305",
        "frgn_sel_prsm_sum": "-5689", "frgn_buy_prsm_sum": "+6305",
    })})
    q = KiwoomQuotes(oauth=_FakeOAuth(), http_client=client)
    b = await q.brokers("005930")
    assert b["sell"][0]["name"] == "모건스탠리"
    assert b["foreign_buy"] == 6305


@pytest.mark.asyncio
async def test_ticks_and_strength():
    client = _FakeClient({
        "ka10003": _FakeResp({"return_code": 0, "cntr_infr": [
            {"tm": "130429", "cur_prc": "+53500", "cntr_trde_qty": "1010", "cntr_str": "12.99"},
        ]}),
        "ka10046": _FakeResp({"return_code": 0, "cntr_str_tm": [
            {"cntr_tm": "163713", "cntr_str": "172.01", "cntr_str_5min": "172.01",
             "cntr_str_20min": "172.01", "cntr_str_60min": "170.67"},
        ]}),
    })
    q = KiwoomQuotes(oauth=_FakeOAuth(), http_client=client)
    ticks = await q.ticks("005930")
    assert ticks[0] == {"time": "130429", "price": 53500, "qty": 1010, "strength": 12.99}
    s = await q.strength("005930")
    assert s["value"] == 172.01


@pytest.mark.asyncio
async def test_investors_after_via_client():
    client = _FakeClient({"ka10066": _FakeResp({
        "return_code": 0, "opaf_invsr_trde": [
            {"stk_cd": "005930", "ind_invsr": "100", "frgnr_invsr": "-50", "orgn": "-50"},
            {"stk_cd": "000660", "ind_invsr": "200", "frgnr_invsr": "-100", "orgn": "-100"},
        ],
    })})
    q = KiwoomQuotes(oauth=_FakeOAuth(), http_client=client)
    rows = await q.investors_after("001")
    assert len(rows) == 2
    assert rows[0]["ind_invsr"] == "100"


@pytest.mark.asyncio
async def test_stock_names_pagination():
    # 1페이지: cont-yn=Y → 2페이지 cont-yn=N
    page1 = _FakeResp(
        {"return_code": 0, "list": [{"code": "005930", "name": "삼성전자"}]},
        headers={"cont-yn": "Y", "next-key": "K1"},
    )
    page2 = _FakeResp(
        {"return_code": 0, "list": [{"code": "000660", "name": "SK하이닉스"}]},
        headers={"cont-yn": "N", "next-key": ""},
    )
    client = _FakeClient({"ka10099": [page1, page2]})
    q = KiwoomQuotes(oauth=_FakeOAuth(), http_client=client)
    names = await q.stock_names(mrkt_tp="0")
    assert names == {"005930": "삼성전자", "000660": "SK하이닉스"}


@pytest.mark.asyncio
async def test_rc3_auth_retry():
    # 첫 응답 rc=3 → invalidate 후 재요청 rc=0
    r3 = _FakeResp({"return_code": 3, "return_msg": "인증실패"})
    ok = _FakeResp({"return_code": 0, "stk_nm": "삼성전자", "cur_prc": "+100"})
    client = _FakeClient({"ka10001": [r3, ok]})
    oauth = _FakeOAuth()
    q = KiwoomQuotes(oauth=oauth, http_client=client)
    info = await q.stock_info("005930")
    assert info["name"] == "삼성전자"
    assert oauth.invalidated == 1


@pytest.mark.asyncio
async def test_key_absent_returns_none():
    # 토큰 발급 실패(키 부재) → 모든 메서드 None
    q = KiwoomQuotes(oauth=_FakeOAuth(fail=True), http_client=_FakeClient({}))
    assert await q.stock_info("005930") is None
    assert await q.orderbook("005930") is None
    assert await q.index_price("0", "001") is None
    assert await q.stock_names() == {}


@pytest.mark.asyncio
async def test_rc_nonzero_returns_none():
    client = _FakeClient({"ka10001": _FakeResp({"return_code": 900, "return_msg": "err"})})
    q = KiwoomQuotes(oauth=_FakeOAuth(), http_client=client)
    assert await q.stock_info("005930") is None
