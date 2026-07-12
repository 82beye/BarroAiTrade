from __future__ import annotations

import json
import sqlite3
import asyncio
from datetime import date
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeToken
from backend.core.gateway.kiwoom_trade_history import (
    ApiPage,
    KiwoomHistoryClient,
    KiwoomHistoryError,
    TradeHistoryStore,
    account_fingerprint,
    business_days,
    create_snapshot,
    date_windows,
    execution_key,
    normalize_kst_timestamp,
    normalize_side,
    normalize_symbol,
    one_year_ago,
    verify_database,
)


def make_page(
    api_id: str,
    scope: str,
    data: dict,
    *,
    page_no: int = 1,
    cont_yn: str = "N",
    next_key: str = "",
) -> ApiPage:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    return ApiPage(
        api_id=api_id,
        request_body={"scope": scope},
        request_scope=scope,
        page_no=page_no,
        response_body=body,
        data=data,
        cont_yn=cont_yn,
        next_key=next_key,
        http_status=200,
        received_at_utc="2026-07-12T12:00:00+00:00",
    )


def fill_row(**overrides):
    row = {
        "ord_no": "1234567",
        "orig_ord_no": "0000000",
        "stk_cd": "A005930",
        "stk_nm": "삼성전자",
        "trde_tp": "현금",
        "io_tp_nm": "+매수",
        "ord_qty": "+0000000010",
        "ord_uv": "+0000070000",
        "cnfm_qty": "+0000000010",
        "cntr_no": "7654321",
        "cntr_qty": "+0000000010",
        "cntr_uv": "+0000069900",
        "cntr_tm": "09:01:02",
        "dmst_stex_tp": "KRX",
        "mdfy_cncl_tp": "",
    }
    row.update(overrides)
    return row


@pytest.fixture
def store(tmp_path: Path):
    with TradeHistoryStore(tmp_path / "history.db") as opened:
        opened.register_account("acct-test", "primary", "mock")
        yield opened


def test_date_helpers_are_calendar_and_business_day_aware():
    assert one_year_ago(date(2024, 2, 29)) == date(2023, 2, 28)
    assert list(business_days(date(2026, 7, 10), date(2026, 7, 13))) == [
        date(2026, 7, 10),
        date(2026, 7, 13),
    ]
    assert list(date_windows(date(2026, 1, 1), date(2026, 2, 2), 31)) == [
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 2)),
    ]


def test_normalizers_preserve_money_and_kst():
    assert normalize_symbol("A005930") == "005930"
    assert normalize_symbol("A0193T0") == "0193T0"
    assert normalize_symbol("005930_NX") == "005930_NX"
    assert normalize_side("+매수") == "BUY"
    assert normalize_side("-매도") == "SELL"
    assert normalize_side("", "unknown") == "UNKNOWN"
    assert normalize_kst_timestamp("20260710", "09:01:02") == "2026-07-10T09:01:02+09:00"


def test_account_id_is_stable_and_pseudonymous():
    first = account_fingerprint(
        app_key="key", app_secret="secret", base_url="https://mockapi.kiwoom.com",
        account_no="1234567890",
    )
    second = account_fingerprint(
        app_key="key", app_secret="secret", base_url="https://mockapi.kiwoom.com",
        account_no="1234567890",
    )
    assert first == second
    assert "1234567890" not in first
    assert "secret" not in first


def test_execution_key_prefers_broker_fill_number():
    row = fill_row()
    assert execution_key("20260710", row) == "1234567:7654321"
    row["cntr_no"] = "0000000"
    with pytest.raises(KiwoomHistoryError, match="no stable execution number"):
        execution_key("20260710", row)


def test_fill_store_is_idempotent_and_views_are_strategy_ready(store: TradeHistoryStore):
    run_id = store.begin_run("acct-test", date(2026, 7, 10), date(2026, 7, 10), 1)
    buy = fill_row()
    sell = fill_row(
        ord_no="1234568",
        cntr_no="7654322",
        io_tp_nm="-매도",
        cntr_qty="5",
        cntr_uv="72000",
        cntr_tm="14:01:02",
    )
    page = make_page(
        "kt00009", "20260710",
        {"return_code": 0, "acnt_ord_cntr_prst_array": [buy, sell]},
    )

    assert store.store_fill_day(run_id, "acct-test", date(2026, 7, 10), [page]) == (2, 2)
    assert store.store_fill_day(run_id, "acct-test", date(2026, 7, 10), [page]) == (2, 2)
    store.finish_run(run_id, succeeded=True)
    assert store.connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2
    assert store.connection.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 2

    daily = store.connection.execute("SELECT * FROM v_daily_trades").fetchone()
    assert daily["buy_qty"] == 10
    assert daily["sell_qty"] == 5
    assert daily["buy_gross_krw"] == 699_000
    assert daily["sell_gross_krw"] == 360_000
    assert daily["buy_vwap_krw"] == 69_900


def test_same_fill_in_later_run_updates_last_seen_without_duplication(store: TradeHistoryStore):
    first_run = store.begin_run("acct-test", date(2026, 7, 10), date(2026, 7, 10), 1)
    page = make_page(
        "kt00009", "20260710",
        {"return_code": 0, "acnt_ord_cntr_prst_array": [fill_row()]},
    )
    store.store_fill_day(first_run, "acct-test", date(2026, 7, 10), [page])
    store.finish_run(first_run, succeeded=True)

    second_run = store.begin_run("acct-test", date(2026, 7, 10), date(2026, 7, 10), 1)
    store.store_fill_day(second_run, "acct-test", date(2026, 7, 10), [page])
    row = store.connection.execute(
        "SELECT first_seen_run_id, last_seen_run_id FROM executions"
    ).fetchone()
    assert row["first_seen_run_id"] == first_run
    assert row["last_seen_run_id"] == second_run
    assert store.connection.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1


def test_realized_pnl_rows_are_aggregated_by_day_and_symbol(store: TradeHistoryStore):
    run_id = store.begin_run("acct-test", date(2026, 7, 1), date(2026, 7, 10), 8)
    rows = [
        {
            "dt": "20260710", "stk_cd": "005930", "stk_nm": "삼성전자",
            "cntr_qty": "3", "buy_uv": "69000", "cntr_pric": "70000",
            "tdy_sel_pl": "3000", "tdy_trde_cmsn": "30", "tdy_trde_tax": "50",
            "pl_rt": "1.45",
        },
        {
            "dt": "20260710", "stk_cd": "005930", "stk_nm": "삼성전자",
            "cntr_qty": "2", "buy_uv": "69500", "cntr_pric": "71000",
            "tdy_sel_pl": "3000", "tdy_trde_cmsn": "20", "tdy_trde_tax": "40",
            "pl_rt": "2.16",
        },
    ]
    page = make_page(
        "ka10073", "20260701:20260710",
        {"return_code": 0, "dt_stk_rlzt_pl": rows},
    )
    assert store.store_realized_window(
        run_id, "acct-test", date(2026, 7, 1), date(2026, 7, 10), [page]
    ) == 2
    result = store.connection.execute("SELECT * FROM realized_pnl").fetchone()
    assert result["sold_qty"] == 5
    assert result["buy_amount_krw"] == 346_000
    assert result["sell_amount_krw"] == 352_000
    assert result["broker_realized_pnl_krw"] == 6_000
    assert result["commission_krw"] == 50
    assert result["tax_krw"] == 90
    assert result["source_row_count"] == 2


def test_realized_pnl_preserves_fractional_won_exactly(store: TradeHistoryStore):
    run_id = store.begin_run("acct-test", date(2026, 7, 10), date(2026, 7, 10), 1)
    row = {
        "dt": "20260710", "stk_cd": "005930", "stk_nm": "삼성전자",
        "cntr_qty": "1", "buy_uv": "100.25", "cntr_pric": "112.50",
        "tdy_sel_pl": "12.25", "tdy_trde_cmsn": "0.22", "tdy_trde_tax": "0.03",
        "pl_rt": "12.22",
    }
    page = make_page(
        "ka10073", "20260710:20260710",
        {"return_code": 0, "dt_stk_rlzt_pl": [row]},
    )
    store.store_realized_window(
        run_id, "acct-test", date(2026, 7, 10), date(2026, 7, 10), [page]
    )
    result = store.connection.execute("SELECT * FROM realized_pnl").fetchone()
    assert result["buy_amount_krw_exact"] == "100.25"
    assert result["sell_amount_krw_exact"] == "112.5"
    assert result["broker_realized_pnl_krw_exact"] == "12.25"
    assert result["commission_krw_exact"] == "0.22"
    assert result["tax_krw_exact"] == "0.03"


def test_daily_pnl_uses_official_field_names(store: TradeHistoryStore):
    run_id = store.begin_run("acct-test", date(2026, 7, 1), date(2026, 7, 10), 8)
    row = {
        "dt": "20260710",
        "buy_amt": "1000000",
        "sell_amt": "1020000",
        "tdy_sel_pl": "+20000",
        "tdy_trde_cmsn": "100",
        "tdy_trde_tax": "200",
    }
    page = make_page(
        "ka10074", "20260701:20260710",
        {"return_code": 0, "dt_rlzt_pl": [row]},
    )
    assert store.store_daily_pnl_window(
        run_id, "acct-test", date(2026, 7, 1), date(2026, 7, 10), [page]
    ) == 1
    result = store.connection.execute("SELECT * FROM daily_pnl").fetchone()
    assert result["broker_realized_pnl_krw"] == 20_000
    assert result["net_pnl_after_costs_krw"] == 19_700


def test_cash_ledger_preserves_broker_transaction_and_costs(store: TradeHistoryStore):
    run_id = store.begin_run("acct-test", date(2026, 7, 1), date(2026, 7, 10), 8)
    row = {
        "trde_dt": "20260710",
        "trde_no": "123456789",
        "orig_deal_no": "000000000",
        "stk_cd": "A005930",
        "stk_nm": "삼성전자",
        "rmrk_nm": "현금매수",
        "io_tp_nm": "매수",
        "trde_kind_nm": "주식매매",
        "trde_qty_jwa_cnt": "10",
        "trde_amt": "-699000",
        "exct_amt": "-699100",
        "cmsn": "100",
        "trde_agri_tax": "0",
        "proc_tm": "09:01:02",
    }
    page = make_page(
        "kt00015", "20260701:20260710",
        {"return_code": 0, "trst_ovrl_trde_prps_array": [row]},
    )
    assert store.store_cash_ledger_window(
        run_id, "acct-test", date(2026, 7, 1), date(2026, 7, 10), [page]
    ) == 1
    result = store.connection.execute("SELECT * FROM cash_ledger").fetchone()
    assert result["transaction_no"] == "123456789"
    assert result["symbol"] == "005930"
    assert result["direction"] == "BUY"
    assert result["quantity"] == 10
    assert result["trade_amount_krw"] == -699_000
    assert result["commission_krw"] == 100
    assert result["processing_at_kst"] == "2026-07-10T09:01:02+09:00"


def test_finish_verify_and_snapshot(tmp_path: Path):
    db_path = tmp_path / "history.db"
    with TradeHistoryStore(db_path) as opened:
        opened.register_account("acct-test", "primary", "mock")
        run_id = opened.begin_run("acct-test", date(2026, 7, 10), date(2026, 7, 10), 1)
        page = make_page(
            "kt00009", "20260710",
            {"return_code": 0, "acnt_ord_cntr_prst_array": [fill_row()]},
        )
        opened.store_fill_day(run_id, "acct-test", date(2026, 7, 10), [page])
        realized = make_page(
            "ka10073", "20260710:20260710",
            {"return_code": 0, "dt_stk_rlzt_pl": []},
        )
        daily = make_page(
            "ka10074", "20260710:20260710",
            {"return_code": 0, "dt_rlzt_pl": []},
        )
        opened.store_realized_window(
            run_id, "acct-test", date(2026, 7, 10), date(2026, 7, 10), [realized]
        )
        opened.store_daily_pnl_window(
            run_id, "acct-test", date(2026, 7, 10), date(2026, 7, 10), [daily]
        )
        opened.finish_run(run_id, succeeded=True)
        opened.set_success_metadata(
            environment="mock", start=date(2026, 7, 10), end=date(2026, 7, 10),
            account_id="acct-test",
        )

    verified = verify_database(db_path)
    assert verified.ok
    assert verified.details["integrity_check"] == "ok"
    assert verified.details["counts"]["executions"] == 1
    assert verified.details["raw_hash_mismatches"] == 0

    snapshot_path = tmp_path / "snapshot.db"
    snap = create_snapshot(db_path, snapshot_path)
    assert snap.ok
    assert snap.details["counts"]["executions"] == 1
    assert snapshot_path.stat().st_mode & 0o777 == 0o600


def test_empty_schema_does_not_verify_or_snapshot(tmp_path: Path):
    db_path = tmp_path / "empty.db"
    with TradeHistoryStore(db_path):
        pass
    verified = verify_database(db_path)
    assert not verified.ok
    with pytest.raises(KiwoomHistoryError, match="completeness"):
        create_snapshot(db_path, tmp_path / "bad-snapshot.db")


def test_all_zero_fill_numbers_fail_closed(store: TradeHistoryStore):
    run_id = store.begin_run("acct-test", date(2026, 7, 10), date(2026, 7, 10), 1)
    rows = [
        fill_row(cntr_no="0000000", cntr_tm="09:01:01", cntr_qty="1"),
        fill_row(cntr_no="0000000", cntr_tm="09:01:02", cntr_qty="2"),
    ]
    page = make_page(
        "kt00009", "20260710",
        {"return_code": 0, "acnt_ord_cntr_prst_array": rows},
    )
    with pytest.raises(KiwoomHistoryError, match="no stable execution number"):
        store.store_fill_day(run_id, "acct-test", date(2026, 7, 10), [page])
    assert store.connection.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0


class FakeOAuth:
    base_url = "https://mockapi.kiwoom.com"

    def __init__(self):
        self.invalidations = 0

    async def get_token(self):
        from datetime import datetime, timezone

        return KiwoomNativeToken(
            access_token=SecretStr("token-value"),
            token_type="Bearer",
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )

    def invalidate_token(self):
        self.invalidations += 1


def test_client_paginates_with_hashed_continuation_not_exposed():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                headers={"cont-yn": "Y", "next-key": "secret-continuation"},
                json={"return_code": 0, "acnt_ord_cntr_prst_array": [fill_row()]},
            )
        return httpx.Response(
            200,
            headers={"cont-yn": "N"},
            json={"return_code": 0, "acnt_ord_cntr_prst_array": []},
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = KiwoomHistoryClient(FakeOAuth(), http_client=http, rate_limit_seconds=0)  # type: ignore[arg-type]
            async with client:
                return await client.fetch_fills_for_day(date(2026, 7, 10))

    pages = asyncio.run(run())

    assert len(pages) == 2
    assert requests[0].url.path == "/api/dostk/acnt"
    assert requests[0].headers["api-id"] == "kt00009"
    assert requests[1].headers["cont-yn"] == "Y"
    assert requests[1].headers["next-key"] == "secret-continuation"
    assert "authorization" not in pages[0].request_body


def test_client_retries_same_page_once_after_auth_failure():
    calls = 0
    oauth = FakeOAuth()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"return_code": 3, "return_msg": "expired"})
        return httpx.Response(
            200,
            headers={"cont-yn": "N"},
            json={"return_code": 0, "acnt_ord_cntr_prst_array": []},
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = KiwoomHistoryClient(oauth, http_client=http, rate_limit_seconds=0)  # type: ignore[arg-type]
            async with client:
                return await client.fetch_fills_for_day(date(2026, 7, 10))

    pages = asyncio.run(run())

    assert calls == 2
    assert oauth.invalidations == 1
    assert [page.page_no for page in pages] == [1]


def test_client_rejects_non_readonly_tr():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as http:
            client = KiwoomHistoryClient(FakeOAuth(), http_client=http, rate_limit_seconds=0)  # type: ignore[arg-type]
            async with client:
                await client.fetch_pages("kt10000", {}, request_scope="unsafe")

    with pytest.raises(ValueError, match="not allowed"):
        asyncio.run(run())


@pytest.mark.parametrize(
    "payload,headers,error",
    [
        ({"acnt_ord_cntr_prst_array": []}, {}, "return_code is missing"),
        ({"return_code": 0}, {}, "expected response list"),
        (
            {"return_code": 0, "acnt_ord_cntr_prst_array": []},
            {"cont-yn": "Y"},
            "without next-key",
        ),
    ],
)
def test_client_fails_closed_on_schema_or_pagination_drift(payload, headers, error):
    async def run():
        transport = httpx.MockTransport(
            lambda _: httpx.Response(200, headers=headers, json=payload)
        )
        async with httpx.AsyncClient(transport=transport) as http:
            client = KiwoomHistoryClient(FakeOAuth(), http_client=http, rate_limit_seconds=0)  # type: ignore[arg-type]
            async with client:
                await client.fetch_fills_for_day(date(2026, 7, 10))

    with pytest.raises(KiwoomHistoryError, match=error):
        asyncio.run(run())


def test_database_contains_no_order_endpoint_or_secret_columns(tmp_path: Path):
    db_path = tmp_path / "history.db"
    with TradeHistoryStore(db_path):
        pass
    conn = sqlite3.connect(db_path)
    try:
        schema = "\n".join(row[0] for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
        ))
        assert "/api/dostk/ordr" not in schema
        assert "app_secret" not in schema.lower()
        assert "access_token" not in schema.lower()
    finally:
        conn.close()
