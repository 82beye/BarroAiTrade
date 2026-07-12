"""Read-only Kiwoom REST trade-history exporter.

This module deliberately exposes only account-query TRs.  It has no order URL or
order method, so importing it from an operations host cannot place, change, or
cancel an order.

The SQLite file is intended to be copied to a development machine and opened
read-only for strategy research.  Exact API response bytes are retained alongside
normalized orders, executions, and realized-P&L summaries.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import httpx

from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth


KST = ZoneInfo("Asia/Seoul")
ACCOUNT_PATH = "/api/dostk/acnt"
ALLOWED_BASE_URLS = {
    "https://api.kiwoom.com": "real",
    "https://mockapi.kiwoom.com": "mock",
}
ALLOWED_TR_IDS = frozenset({"kt00009", "ka10073", "ka10074", "kt00015"})
EXPECTED_RESPONSE_LIST_KEYS = {
    "kt00009": "acnt_ord_cntr_prst_array",
    "ka10073": "dt_stk_rlzt_pl",
    "ka10074": "dt_rlzt_pl",
    "kt00015": "trst_ovrl_trde_prps_array",
}
SCHEMA_VERSION = 3
DEFAULT_OVERLAP_DAYS = 7


class KiwoomHistoryError(RuntimeError):
    """Safe-to-display history export error (never contains credentials)."""


@dataclass(frozen=True)
class ApiPage:
    api_id: str
    request_body: dict[str, Any]
    request_scope: str
    page_no: int
    response_body: bytes
    data: dict[str, Any]
    cont_yn: str
    next_key: str
    http_status: int
    received_at_utc: str


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    details: dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_yyyymmdd(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid date {value!r}; expected YYYYMMDD") from exc


def one_year_ago(day: date) -> date:
    """Calendar-year subtraction, with 29 February mapped to 28 February."""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def business_days(start: date, end: date) -> Iterator[date]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def date_windows(start: date, end: date, days: int = 31) -> Iterator[tuple[date, date]]:
    if days < 1:
        raise ValueError("window size must be positive")
    current = start
    while current <= end:
        window_end = min(current + timedelta(days=days - 1), end)
        yield current, window_end
        current = window_end + timedelta(days=1)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def parse_int(value: Any, *, absolute: bool = False) -> int:
    if value is None or value == "":
        return 0
    text = str(value).strip().replace(",", "")
    if not text:
        return 0
    try:
        parsed = int(text)
    except ValueError:
        try:
            parsed = int(float(text))
        except (ValueError, OverflowError):
            return 0
    return abs(parsed) if absolute else parsed


def parse_decimal(value: Any, *, absolute: bool = False) -> Decimal:
    if value is None or value == "":
        parsed = Decimal("0")
    else:
        text = str(value).strip().replace(",", "")
        try:
            parsed = Decimal(text or "0")
        except InvalidOperation as exc:
            raise KiwoomHistoryError(f"invalid numeric value from Kiwoom: {text!r}") from exc
    return abs(parsed) if absolute else parsed


def decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def decimal_to_won(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_float_text(value: Any) -> Optional[str]:
    """Keep decimal API values as text rather than binary floating point."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    return text or None


def normalize_symbol(value: Any) -> str:
    text = str(value or "").strip()
    # kt00009 documents a one-character product prefix plus a six-character
    # symbol.  The symbol itself can be alphanumeric (for example 0193T0).
    if len(text) == 7 and text[0] in {"A", "J", "Q"}:
        return text[1:]
    return text


def normalize_side(io_type: Any, trade_type: Any = "") -> str:
    combined = f"{io_type or ''} {trade_type or ''}".strip().lower()
    if "매수" in combined or "buy" in combined:
        return "BUY"
    if "매도" in combined or "sell" in combined:
        return "SELL"
    return "UNKNOWN"


def normalize_kst_timestamp(trade_day: str, raw_time: Any) -> Optional[str]:
    text = str(raw_time or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) != 6 or len(trade_day) != 8:
        return None
    try:
        parsed = datetime.strptime(trade_day + digits, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError:
        return None
    return parsed.isoformat(timespec="seconds")


def account_fingerprint(
    *, app_key: str, app_secret: str, base_url: str, account_no: str = "",
    identity_key: Optional[str] = None,
) -> str:
    """Stable pseudonymous account id; no account number or credential is stored."""
    material = f"{base_url}|{account_no.strip() or app_key}".encode("utf-8")
    key = (identity_key or app_secret).encode("utf-8")
    digest = hmac.new(key, material, hashlib.sha256).hexdigest()
    return f"kiwoom-{digest[:24]}"


def execution_key(trade_day: str, row: Mapping[str, Any]) -> str:
    order_no = str(row.get("ord_no") or "").strip()
    fill_no = str(row.get("cntr_no") or "").strip()
    if fill_no and fill_no.strip("0"):
        return f"{order_no or 'no-order'}:{fill_no}"
    raise KiwoomHistoryError(
        f"kt00009: filled row has no stable execution number on {trade_day}"
    )


class KiwoomHistoryClient:
    """Allowlisted read-only account-history HTTP client."""

    def __init__(
        self,
        oauth: KiwoomNativeOAuth,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
        rate_limit_seconds: float = 0.55,
        max_retries: int = 4,
        max_pages: int = 200,
    ) -> None:
        if oauth.base_url not in ALLOWED_BASE_URLS:
            raise ValueError("Kiwoom history export only permits official Kiwoom base URLs")
        if rate_limit_seconds < 0:
            raise ValueError("rate_limit_seconds cannot be negative")
        self._oauth = oauth
        self._http = http_client
        self._owns_http = http_client is None
        self._rate = rate_limit_seconds
        self._max_retries = max_retries
        self._max_pages = max_pages
        self._last_request = 0.0

    async def __aenter__(self) -> "KiwoomHistoryClient":
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _wait_for_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._rate:
            await asyncio.sleep(self._rate - elapsed)

    async def fetch_pages(
        self, api_id: str, body: Mapping[str, Any], *, request_scope: str
    ) -> list[ApiPage]:
        if api_id not in ALLOWED_TR_IDS:
            raise ValueError(f"TR {api_id!r} is not allowed by the read-only exporter")
        if self._http is None:
            raise RuntimeError("KiwoomHistoryClient must be used as an async context manager")

        pages: list[ApiPage] = []
        cont_yn = "N"
        next_key = ""
        seen_continuations: set[str] = set()
        token = await self._oauth.get_token()
        auth_retried = False

        page_no = 1
        while page_no <= self._max_pages:
            response: Optional[httpx.Response] = None
            data: Optional[dict[str, Any]] = None
            for attempt in range(self._max_retries):
                await self._wait_for_rate_limit()
                try:
                    response = await self._http.post(
                        f"{self._oauth.base_url}{ACCOUNT_PATH}",
                        headers={
                            "authorization": f"Bearer {token.access_token.get_secret_value()}",
                            "content-type": "application/json;charset=UTF-8",
                            "api-id": api_id,
                            "cont-yn": cont_yn,
                            "next-key": next_key,
                        },
                        json=dict(body),
                    )
                    self._last_request = time.monotonic()
                    if response.status_code == 429:
                        if attempt == self._max_retries - 1:
                            raise KiwoomHistoryError(f"{api_id}: rate limit after retries")
                        await asyncio.sleep(min(2.0 ** attempt, 8.0))
                        continue
                    if response.status_code >= 500:
                        if attempt == self._max_retries - 1:
                            response.raise_for_status()
                        await asyncio.sleep(min(2.0 ** attempt, 8.0))
                        continue
                    response.raise_for_status()
                    parsed = response.json()
                    if not isinstance(parsed, dict):
                        raise KiwoomHistoryError(f"{api_id}: response is not a JSON object")
                    data = parsed
                    break
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    self._last_request = time.monotonic()
                    if attempt == self._max_retries - 1:
                        raise KiwoomHistoryError(
                            f"{api_id}: network failure after {self._max_retries} attempts"
                        ) from exc
                    await asyncio.sleep(min(2.0 ** attempt, 8.0))

            if response is None or data is None:
                raise KiwoomHistoryError(f"{api_id}: no response received")

            if "return_code" not in data:
                raise KiwoomHistoryError(f"{api_id}: return_code is missing")
            try:
                return_code = int(str(data["return_code"]).strip())
            except (TypeError, ValueError) as exc:
                raise KiwoomHistoryError(f"{api_id}: invalid return_code") from exc
            if return_code == 3 and not auth_retried:
                auth_retried = True
                self._oauth.invalidate_token()
                token = await self._oauth.get_token()
                # Retry the same page with the same continuation values.
                continue
            if return_code != 0:
                message = str(data.get("return_msg") or "unknown API error").strip()
                raise KiwoomHistoryError(f"{api_id}: return_code={return_code}, message={message}")

            expected_key = EXPECTED_RESPONSE_LIST_KEYS[api_id]
            if expected_key not in data or not isinstance(data[expected_key], list):
                raise KiwoomHistoryError(
                    f"{api_id}: expected response list {expected_key!r} is missing"
                )

            response_cont = response.headers.get("cont-yn", "N").upper()
            response_next = response.headers.get("next-key", "")
            pages.append(ApiPage(
                api_id=api_id,
                request_body=dict(body),
                request_scope=request_scope,
                page_no=page_no,
                response_body=response.content,
                data=data,
                cont_yn=response_cont,
                next_key=response_next,
                http_status=response.status_code,
                received_at_utc=utc_now_iso(),
            ))

            if response_cont == "Y" and not response_next:
                raise KiwoomHistoryError(f"{api_id}: cont-yn=Y without next-key")
            if response_cont == "Y" and response_next:
                continuation_hash = sha256_hex(response_next)
                if continuation_hash in seen_continuations:
                    raise KiwoomHistoryError(f"{api_id}: repeated continuation key")
                seen_continuations.add(continuation_hash)
                cont_yn = "Y"
                next_key = response_next
                page_no += 1
                continue
            return pages

        raise KiwoomHistoryError(f"{api_id}: exceeded {self._max_pages} pages")

    async def fetch_fills_for_day(self, trade_day: date) -> list[ApiPage]:
        ymd = trade_day.strftime("%Y%m%d")
        return await self.fetch_pages(
            "kt00009",
            {
                "ord_dt": ymd,
                "stk_bond_tp": "1",
                "mrkt_tp": "0",
                "sell_tp": "0",
                "qry_tp": "1",
                "stk_cd": "",
                "fr_ord_no": "",
                "dmst_stex_tp": "%",
            },
            request_scope=ymd,
        )

    async def fetch_realized_pnl(self, start: date, end: date) -> list[ApiPage]:
        scope = f"{start:%Y%m%d}:{end:%Y%m%d}"
        return await self.fetch_pages(
            "ka10073",
            {"stk_cd": "", "strt_dt": f"{start:%Y%m%d}", "end_dt": f"{end:%Y%m%d}"},
            request_scope=scope,
        )

    async def fetch_daily_pnl(self, start: date, end: date) -> list[ApiPage]:
        scope = f"{start:%Y%m%d}:{end:%Y%m%d}"
        return await self.fetch_pages(
            "ka10074",
            {"strt_dt": f"{start:%Y%m%d}", "end_dt": f"{end:%Y%m%d}"},
            request_scope=scope,
        )

    async def fetch_cash_ledger(self, start: date, end: date) -> list[ApiPage]:
        """Entrusted transaction ledger (trades only), including fees and taxes."""
        scope = f"{start:%Y%m%d}:{end:%Y%m%d}"
        return await self.fetch_pages(
            "kt00015",
            {
                "strt_dt": f"{start:%Y%m%d}",
                "end_dt": f"{end:%Y%m%d}",
                "tp": "3",
                "stk_cd": "",
                "crnc_cd": "",
                "gds_tp": "1",
                "frgn_stex_code": "",
                "dmst_stex_tp": "%",
                "qry_sort_tp": "2",
            },
            request_scope=scope,
        )


SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    alias TEXT NOT NULL,
    broker TEXT NOT NULL DEFAULT 'KIWOOM',
    environment TEXT NOT NULL CHECK(environment IN ('real', 'mock')),
    base_currency TEXT NOT NULL DEFAULT 'KRW',
    created_at_utc TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS sync_runs (
    run_id INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL,
    requested_from_kst TEXT NOT NULL,
    requested_to_kst TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    status TEXT NOT NULL CHECK(status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'ABORTED')),
    requested_days INTEGER NOT NULL DEFAULT 0,
    api_calls INTEGER NOT NULL DEFAULT 0,
    source_rows INTEGER NOT NULL DEFAULT 0,
    execution_rows INTEGER NOT NULL DEFAULT 0,
    realized_rows INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    FOREIGN KEY(account_id) REFERENCES accounts(account_id)
) STRICT;

CREATE TABLE IF NOT EXISTS sync_days (
    run_id INTEGER NOT NULL,
    trade_date_kst TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('SUCCEEDED', 'FAILED')),
    page_count INTEGER NOT NULL,
    source_row_count INTEGER NOT NULL,
    execution_row_count INTEGER NOT NULL,
    error_message TEXT,
    PRIMARY KEY(run_id, trade_date_kst),
    FOREIGN KEY(run_id) REFERENCES sync_runs(run_id)
) STRICT;

CREATE TABLE IF NOT EXISTS sync_windows (
    run_id INTEGER NOT NULL,
    api_id TEXT NOT NULL,
    start_date_kst TEXT NOT NULL,
    end_date_kst TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('SUCCEEDED', 'FAILED')),
    page_count INTEGER NOT NULL,
    source_row_count INTEGER NOT NULL,
    error_message TEXT,
    PRIMARY KEY(run_id, api_id, start_date_kst, end_date_kst),
    FOREIGN KEY(run_id) REFERENCES sync_runs(run_id)
) STRICT;

CREATE TABLE IF NOT EXISTS raw_responses (
    raw_response_id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    api_id TEXT NOT NULL,
    request_scope TEXT NOT NULL,
    page_no INTEGER NOT NULL,
    request_json TEXT NOT NULL,
    response_sha256 TEXT NOT NULL CHECK(length(response_sha256) = 64),
    response_body BLOB NOT NULL,
    byte_length INTEGER NOT NULL,
    http_status INTEGER NOT NULL,
    cont_yn TEXT NOT NULL,
    next_key_sha256 TEXT,
    received_at_utc TEXT NOT NULL,
    UNIQUE(run_id, api_id, request_scope, page_no),
    FOREIGN KEY(run_id) REFERENCES sync_runs(run_id)
) STRICT;

CREATE TABLE IF NOT EXISTS orders (
    account_id TEXT NOT NULL,
    trade_date_kst TEXT NOT NULL,
    order_no TEXT NOT NULL,
    original_order_no TEXT,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL', 'UNKNOWN')),
    trade_type TEXT,
    order_type TEXT,
    exchange TEXT,
    order_qty INTEGER NOT NULL CHECK(order_qty >= 0),
    order_price_krw INTEGER NOT NULL CHECK(order_price_krw >= 0),
    confirm_qty INTEGER NOT NULL CHECK(confirm_qty >= 0),
    accept_type TEXT,
    settlement_type TEXT,
    credit_type TEXT,
    modify_cancel_type TEXT,
    communication_order_type TEXT,
    stop_price_krw INTEGER NOT NULL DEFAULT 0 CHECK(stop_price_krw >= 0),
    first_seen_run_id INTEGER NOT NULL,
    last_seen_run_id INTEGER NOT NULL,
    last_raw_response_id INTEGER NOT NULL,
    revision_sha256 TEXT NOT NULL CHECK(length(revision_sha256) = 64),
    raw_json TEXT NOT NULL,
    PRIMARY KEY(account_id, trade_date_kst, order_no),
    FOREIGN KEY(account_id) REFERENCES accounts(account_id),
    FOREIGN KEY(first_seen_run_id) REFERENCES sync_runs(run_id),
    FOREIGN KEY(last_seen_run_id) REFERENCES sync_runs(run_id),
    FOREIGN KEY(last_raw_response_id) REFERENCES raw_responses(raw_response_id)
) STRICT;

CREATE TABLE IF NOT EXISTS executions (
    account_id TEXT NOT NULL,
    trade_date_kst TEXT NOT NULL,
    execution_key TEXT NOT NULL,
    order_no TEXT NOT NULL,
    source_execution_no TEXT,
    original_order_no TEXT,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL', 'UNKNOWN')),
    executed_at_kst TEXT,
    executed_at_raw TEXT,
    execution_qty INTEGER NOT NULL CHECK(execution_qty > 0),
    execution_price_krw INTEGER NOT NULL CHECK(execution_price_krw >= 0),
    gross_amount_krw INTEGER NOT NULL CHECK(gross_amount_krw >= 0),
    exchange TEXT,
    modify_cancel_type TEXT,
    first_seen_run_id INTEGER NOT NULL,
    last_seen_run_id INTEGER NOT NULL,
    last_raw_response_id INTEGER NOT NULL,
    revision_sha256 TEXT NOT NULL CHECK(length(revision_sha256) = 64),
    raw_json TEXT NOT NULL,
    PRIMARY KEY(account_id, trade_date_kst, execution_key),
    FOREIGN KEY(account_id, trade_date_kst, order_no)
        REFERENCES orders(account_id, trade_date_kst, order_no),
    FOREIGN KEY(first_seen_run_id) REFERENCES sync_runs(run_id),
    FOREIGN KEY(last_seen_run_id) REFERENCES sync_runs(run_id),
    FOREIGN KEY(last_raw_response_id) REFERENCES raw_responses(raw_response_id)
) STRICT;

CREATE TABLE IF NOT EXISTS realized_pnl (
    account_id TEXT NOT NULL,
    trade_date_kst TEXT NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    sold_qty INTEGER NOT NULL CHECK(sold_qty >= 0),
    buy_amount_krw INTEGER NOT NULL,
    sell_amount_krw INTEGER NOT NULL,
    broker_realized_pnl_krw INTEGER NOT NULL,
    commission_krw INTEGER NOT NULL CHECK(commission_krw >= 0),
    tax_krw INTEGER NOT NULL CHECK(tax_krw >= 0),
    buy_amount_krw_exact TEXT NOT NULL,
    sell_amount_krw_exact TEXT NOT NULL,
    broker_realized_pnl_krw_exact TEXT NOT NULL,
    commission_krw_exact TEXT NOT NULL,
    tax_krw_exact TEXT NOT NULL,
    pnl_rate_text TEXT,
    source_row_count INTEGER NOT NULL,
    last_seen_run_id INTEGER NOT NULL,
    last_raw_response_id INTEGER NOT NULL,
    raw_rows_json TEXT NOT NULL,
    PRIMARY KEY(account_id, trade_date_kst, symbol),
    FOREIGN KEY(account_id) REFERENCES accounts(account_id),
    FOREIGN KEY(last_seen_run_id) REFERENCES sync_runs(run_id),
    FOREIGN KEY(last_raw_response_id) REFERENCES raw_responses(raw_response_id)
) STRICT;

CREATE TABLE IF NOT EXISTS daily_pnl (
    account_id TEXT NOT NULL,
    trade_date_kst TEXT NOT NULL,
    buy_amount_krw INTEGER NOT NULL,
    sell_amount_krw INTEGER NOT NULL,
    broker_realized_pnl_krw INTEGER NOT NULL,
    commission_krw INTEGER NOT NULL CHECK(commission_krw >= 0),
    tax_krw INTEGER NOT NULL CHECK(tax_krw >= 0),
    net_pnl_after_costs_krw INTEGER NOT NULL,
    buy_amount_krw_exact TEXT NOT NULL,
    sell_amount_krw_exact TEXT NOT NULL,
    broker_realized_pnl_krw_exact TEXT NOT NULL,
    commission_krw_exact TEXT NOT NULL,
    tax_krw_exact TEXT NOT NULL,
    net_pnl_after_costs_krw_exact TEXT NOT NULL,
    last_seen_run_id INTEGER NOT NULL,
    last_raw_response_id INTEGER NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY(account_id, trade_date_kst),
    FOREIGN KEY(account_id) REFERENCES accounts(account_id),
    FOREIGN KEY(last_seen_run_id) REFERENCES sync_runs(run_id),
    FOREIGN KEY(last_raw_response_id) REFERENCES raw_responses(raw_response_id)
) STRICT;

CREATE TABLE IF NOT EXISTS cash_ledger (
    account_id TEXT NOT NULL,
    trade_date_kst TEXT NOT NULL,
    transaction_key TEXT NOT NULL,
    transaction_no TEXT,
    original_transaction_no TEXT,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    transaction_type TEXT,
    direction TEXT,
    credit_type TEXT,
    quantity INTEGER NOT NULL CHECK(quantity >= 0),
    trade_amount_krw INTEGER NOT NULL,
    settlement_amount_krw INTEGER NOT NULL,
    commission_krw INTEGER NOT NULL CHECK(commission_krw >= 0),
    transaction_tax_krw INTEGER NOT NULL CHECK(transaction_tax_krw >= 0),
    trade_amount_krw_exact TEXT NOT NULL,
    settlement_amount_krw_exact TEXT NOT NULL,
    commission_krw_exact TEXT NOT NULL,
    transaction_tax_krw_exact TEXT NOT NULL,
    processing_time_raw TEXT,
    processing_at_kst TEXT,
    last_seen_run_id INTEGER NOT NULL,
    last_raw_response_id INTEGER NOT NULL,
    revision_sha256 TEXT NOT NULL CHECK(length(revision_sha256) = 64),
    raw_json TEXT NOT NULL,
    PRIMARY KEY(account_id, trade_date_kst, transaction_key),
    FOREIGN KEY(account_id) REFERENCES accounts(account_id),
    FOREIGN KEY(last_seen_run_id) REFERENCES sync_runs(run_id),
    FOREIGN KEY(last_raw_response_id) REFERENCES raw_responses(raw_response_id)
) STRICT;

CREATE INDEX IF NOT EXISTS idx_orders_symbol_date
    ON orders(symbol, trade_date_kst);
CREATE INDEX IF NOT EXISTS idx_orders_original
    ON orders(account_id, trade_date_kst, original_order_no);
CREATE INDEX IF NOT EXISTS idx_executions_order
    ON executions(account_id, trade_date_kst, order_no);
CREATE INDEX IF NOT EXISTS idx_executions_symbol_time
    ON executions(symbol, trade_date_kst, executed_at_kst);
CREATE UNIQUE INDEX IF NOT EXISTS idx_executions_source_no
    ON executions(account_id, trade_date_kst, order_no, source_execution_no)
    WHERE source_execution_no IS NOT NULL AND source_execution_no != '';
CREATE INDEX IF NOT EXISTS idx_realized_symbol_date
    ON realized_pnl(symbol, trade_date_kst);
CREATE INDEX IF NOT EXISTS idx_cash_ledger_symbol_date
    ON cash_ledger(symbol, trade_date_kst);
CREATE INDEX IF NOT EXISTS idx_raw_api_scope
    ON raw_responses(api_id, request_scope);
CREATE INDEX IF NOT EXISTS idx_sync_runs_status
    ON sync_runs(account_id, status, started_at_utc);

CREATE VIEW IF NOT EXISTS v_trade_fills AS
SELECT
    e.trade_date_kst,
    e.executed_at_kst,
    e.symbol,
    e.name,
    e.side,
    e.execution_qty AS qty,
    e.execution_price_krw AS price_krw,
    e.gross_amount_krw,
    e.order_no,
    e.source_execution_no,
    o.order_type,
    e.exchange
FROM executions e
JOIN orders o
  ON o.account_id = e.account_id
 AND o.trade_date_kst = e.trade_date_kst
 AND o.order_no = e.order_no;

CREATE VIEW IF NOT EXISTS v_daily_trades AS
WITH fill_totals AS (
    SELECT
        account_id,
        trade_date_kst,
        symbol,
        MAX(name) AS name,
        SUM(CASE WHEN side = 'BUY' THEN execution_qty ELSE 0 END) AS buy_qty,
        SUM(CASE WHEN side = 'SELL' THEN execution_qty ELSE 0 END) AS sell_qty,
        SUM(CASE WHEN side = 'BUY' THEN gross_amount_krw ELSE 0 END) AS buy_gross_krw,
        SUM(CASE WHEN side = 'SELL' THEN gross_amount_krw ELSE 0 END) AS sell_gross_krw,
        SUM(CASE WHEN side = 'BUY' THEN 1 ELSE 0 END) AS buy_fill_count,
        SUM(CASE WHEN side = 'SELL' THEN 1 ELSE 0 END) AS sell_fill_count
    FROM executions
    GROUP BY account_id, trade_date_kst, symbol
)
SELECT
    f.trade_date_kst,
    f.symbol,
    f.name,
    f.buy_qty,
    f.sell_qty,
    CASE WHEN f.buy_qty > 0 THEN 1.0 * f.buy_gross_krw / f.buy_qty END AS buy_vwap_krw,
    CASE WHEN f.sell_qty > 0 THEN 1.0 * f.sell_gross_krw / f.sell_qty END AS sell_vwap_krw,
    f.buy_gross_krw,
    f.sell_gross_krw,
    f.sell_gross_krw - f.buy_gross_krw AS net_cashflow_before_costs_krw,
    f.buy_fill_count,
    f.sell_fill_count,
    r.broker_realized_pnl_krw,
    r.commission_krw,
    r.tax_krw,
    CASE WHEN r.broker_realized_pnl_krw IS NOT NULL
         THEN r.broker_realized_pnl_krw - r.commission_krw - r.tax_krw END
         AS net_realized_pnl_after_costs_krw
FROM fill_totals f
LEFT JOIN realized_pnl r
  ON r.account_id = f.account_id
 AND r.trade_date_kst = f.trade_date_kst
 AND r.symbol = f.symbol;

CREATE VIEW IF NOT EXISTS v_cash_ledger AS
SELECT
    trade_date_kst,
    processing_at_kst,
    symbol,
    name,
    direction,
    quantity,
    trade_amount_krw,
    settlement_amount_krw,
    commission_krw,
    transaction_tax_krw,
    transaction_no,
    description
FROM cash_ledger;
"""

EXACT_COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    "realized_pnl": {
        "buy_amount_krw_exact": "buy_amount_krw",
        "sell_amount_krw_exact": "sell_amount_krw",
        "broker_realized_pnl_krw_exact": "broker_realized_pnl_krw",
        "commission_krw_exact": "commission_krw",
        "tax_krw_exact": "tax_krw",
    },
    "daily_pnl": {
        "buy_amount_krw_exact": "buy_amount_krw",
        "sell_amount_krw_exact": "sell_amount_krw",
        "broker_realized_pnl_krw_exact": "broker_realized_pnl_krw",
        "commission_krw_exact": "commission_krw",
        "tax_krw_exact": "tax_krw",
        "net_pnl_after_costs_krw_exact": "net_pnl_after_costs_krw",
    },
    "cash_ledger": {
        "trade_amount_krw_exact": "trade_amount_krw",
        "settlement_amount_krw_exact": "settlement_amount_krw",
        "commission_krw_exact": "commission_krw",
        "transaction_tax_krw_exact": "transaction_tax_krw",
    },
}

REFRESH_VIEWS_SQL = """
DROP VIEW IF EXISTS v_trade_fills;
DROP VIEW IF EXISTS v_daily_trades;
DROP VIEW IF EXISTS v_cash_ledger;

CREATE VIEW v_trade_fills AS
SELECT
    e.account_id,
    e.trade_date_kst,
    e.executed_at_kst,
    e.symbol,
    e.name,
    e.side,
    e.execution_qty AS qty,
    e.execution_price_krw AS price_krw,
    e.gross_amount_krw,
    e.order_no,
    e.source_execution_no,
    o.order_type,
    e.exchange
FROM executions e
JOIN sync_runs er ON er.run_id=e.last_seen_run_id AND er.status='SUCCEEDED'
JOIN orders o
  ON o.account_id = e.account_id
 AND o.trade_date_kst = e.trade_date_kst
 AND o.order_no = e.order_no
JOIN sync_runs obr ON obr.run_id=o.last_seen_run_id AND obr.status='SUCCEEDED';

CREATE VIEW v_daily_trades AS
WITH fill_totals AS (
    SELECT
        e.account_id,
        e.trade_date_kst,
        e.symbol,
        MAX(e.name) AS name,
        SUM(CASE WHEN e.side = 'BUY' THEN e.execution_qty ELSE 0 END) AS buy_qty,
        SUM(CASE WHEN e.side = 'SELL' THEN e.execution_qty ELSE 0 END) AS sell_qty,
        SUM(CASE WHEN e.side = 'BUY' THEN e.gross_amount_krw ELSE 0 END) AS buy_gross_krw,
        SUM(CASE WHEN e.side = 'SELL' THEN e.gross_amount_krw ELSE 0 END) AS sell_gross_krw,
        SUM(CASE WHEN e.side = 'BUY' THEN 1 ELSE 0 END) AS buy_fill_count,
        SUM(CASE WHEN e.side = 'SELL' THEN 1 ELSE 0 END) AS sell_fill_count
    FROM executions e
    JOIN sync_runs sr ON sr.run_id=e.last_seen_run_id AND sr.status='SUCCEEDED'
    GROUP BY e.account_id, e.trade_date_kst, e.symbol
), successful_realized AS (
    SELECT r.*
    FROM realized_pnl r
    JOIN sync_runs sr ON sr.run_id=r.last_seen_run_id AND sr.status='SUCCEEDED'
)
SELECT
    f.account_id,
    f.trade_date_kst,
    f.symbol,
    f.name,
    f.buy_qty,
    f.sell_qty,
    CASE WHEN f.buy_qty > 0 THEN 1.0 * f.buy_gross_krw / f.buy_qty END AS buy_vwap_krw,
    CASE WHEN f.sell_qty > 0 THEN 1.0 * f.sell_gross_krw / f.sell_qty END AS sell_vwap_krw,
    f.buy_gross_krw,
    f.sell_gross_krw,
    f.sell_gross_krw - f.buy_gross_krw AS net_cashflow_before_costs_krw,
    f.buy_fill_count,
    f.sell_fill_count,
    r.broker_realized_pnl_krw,
    r.broker_realized_pnl_krw_exact,
    r.commission_krw,
    r.commission_krw_exact,
    r.tax_krw,
    r.tax_krw_exact,
    CASE WHEN r.broker_realized_pnl_krw IS NOT NULL
         THEN r.broker_realized_pnl_krw - r.commission_krw - r.tax_krw END
         AS net_realized_pnl_after_costs_krw
FROM fill_totals f
LEFT JOIN successful_realized r
  ON r.account_id = f.account_id
 AND r.trade_date_kst = f.trade_date_kst
 AND r.symbol = f.symbol;

CREATE VIEW v_cash_ledger AS
SELECT
    c.account_id,
    c.trade_date_kst,
    c.processing_at_kst,
    c.symbol,
    c.name,
    c.direction,
    c.quantity,
    c.trade_amount_krw,
    c.trade_amount_krw_exact,
    c.settlement_amount_krw,
    c.settlement_amount_krw_exact,
    c.commission_krw,
    c.commission_krw_exact,
    c.transaction_tax_krw,
    c.transaction_tax_krw_exact,
    c.transaction_no,
    c.description
FROM cash_ledger c
JOIN sync_runs sr ON sr.run_id=c.last_seen_run_id AND sr.status='SUCCEEDED';
"""


class TradeHistoryStore:
    def __init__(self, path: Path | str) -> None:
        if sqlite3.sqlite_version_info < (3, 37, 0):
            raise RuntimeError("SQLite 3.37 or newer is required for STRICT tables")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = FULL")
        self._conn.executescript(SCHEMA_SQL)
        self._ensure_exact_columns()
        self._conn.executescript(REFRESH_VIEWS_SQL)
        self._conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
        self._conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('timezone', 'Asia/Seoul') "
            "ON CONFLICT(key) DO NOTHING"
        )
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _ensure_exact_columns(self) -> None:
        """Forward-migrate pre-v3 DBs without discarding their raw audit trail."""
        for table, columns in EXACT_COLUMN_MIGRATIONS.items():
            existing = {
                str(row[1]) for row in self._conn.execute(f"PRAGMA table_info({table})")
            }
            for exact_column, integer_column in columns.items():
                if exact_column not in existing:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {exact_column} "
                        "TEXT NOT NULL DEFAULT '0'"
                    )
                    self._conn.execute(
                        f"UPDATE {table} SET {exact_column}=CAST({integer_column} AS TEXT)"
                    )

    def __enter__(self) -> "TradeHistoryStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        if self._conn is None:  # type: ignore[comparison-overlap]
            return
        try:
            self._conn.commit()
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._conn.execute("PRAGMA journal_mode = DELETE")
        finally:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def register_account(self, account_id: str, alias: str, environment: str) -> None:
        self._conn.execute(
            """
            INSERT INTO accounts(account_id, alias, environment, created_at_utc)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                alias=excluded.alias,
                environment=excluded.environment
            """,
            (account_id, alias, environment, utc_now_iso()),
        )
        self._conn.commit()

    def begin_run(self, account_id: str, start: date, end: date, requested_days: int) -> int:
        now = utc_now_iso()
        self._conn.execute(
            """
            UPDATE sync_runs
               SET status='ABORTED', finished_at_utc=?,
                   error_message=COALESCE(error_message, 'process ended before completion')
             WHERE status='RUNNING' AND account_id=?
            """,
            (now, account_id),
        )
        cursor = self._conn.execute(
            """
            INSERT INTO sync_runs(
                account_id, requested_from_kst, requested_to_kst,
                started_at_utc, status, requested_days
            ) VALUES(?, ?, ?, ?, 'RUNNING', ?)
            """,
            (account_id, start.isoformat(), end.isoformat(), now, requested_days),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, *, succeeded: bool, error: Optional[str] = None) -> None:
        counts = self._conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM raw_responses WHERE run_id=?) AS api_calls,
                (SELECT COALESCE(SUM(source_row_count), 0) FROM sync_days WHERE run_id=?)
                  + (SELECT COALESCE(SUM(source_row_count), 0) FROM sync_windows WHERE run_id=?)
                  AS source_rows,
                (SELECT COALESCE(SUM(execution_row_count), 0) FROM sync_days WHERE run_id=?)
                  AS execution_rows,
                (SELECT COALESCE(SUM(source_row_count), 0) FROM sync_windows
                  WHERE run_id=? AND api_id='ka10073') AS realized_rows
            """,
            (run_id, run_id, run_id, run_id, run_id),
        ).fetchone()
        self._conn.execute(
            """
            UPDATE sync_runs SET
                finished_at_utc=?, status=?, api_calls=?, source_rows=?,
                execution_rows=?, realized_rows=?, error_message=?
            WHERE run_id=?
            """,
            (
                utc_now_iso(),
                "SUCCEEDED" if succeeded else "FAILED",
                int(counts["api_calls"]),
                int(counts["source_rows"]),
                int(counts["execution_rows"]),
                int(counts["realized_rows"]),
                error,
                run_id,
            ),
        )
        self._conn.commit()

    def _store_raw_page(self, run_id: int, page: ApiPage) -> int:
        digest = sha256_hex(page.response_body)
        cursor = self._conn.execute(
            """
            INSERT INTO raw_responses(
                run_id, api_id, request_scope, page_no, request_json,
                response_sha256, response_body, byte_length, http_status,
                cont_yn, next_key_sha256, received_at_utc
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, api_id, request_scope, page_no) DO UPDATE SET
                request_json=excluded.request_json,
                response_sha256=excluded.response_sha256,
                response_body=excluded.response_body,
                byte_length=excluded.byte_length,
                http_status=excluded.http_status,
                cont_yn=excluded.cont_yn,
                next_key_sha256=excluded.next_key_sha256,
                received_at_utc=excluded.received_at_utc
            RETURNING raw_response_id
            """,
            (
                run_id,
                page.api_id,
                page.request_scope,
                page.page_no,
                canonical_json(page.request_body),
                digest,
                page.response_body,
                len(page.response_body),
                page.http_status,
                page.cont_yn,
                sha256_hex(page.next_key) if page.next_key else None,
                page.received_at_utc,
            ),
        )
        return int(cursor.fetchone()[0])

    @staticmethod
    def _rows(page: ApiPage, keys: Sequence[str]) -> list[dict[str, Any]]:
        for key in keys:
            rows = page.data.get(key)
            if isinstance(rows, list):
                return [dict(row) for row in rows if isinstance(row, Mapping)]
        return []

    def store_fill_day(
        self, run_id: int, account_id: str, trade_day: date, pages: Sequence[ApiPage]
    ) -> tuple[int, int]:
        ymd = trade_day.strftime("%Y%m%d")
        source_rows = 0
        execution_rows = 0
        with self._conn:
            for page in pages:
                raw_id = self._store_raw_page(run_id, page)
                rows = self._rows(page, ("acnt_ord_cntr_prst_array", "list"))
                source_rows += len(rows)
                for row_index, row in enumerate(rows, start=1):
                    raw_json = canonical_json(row)
                    revision = sha256_hex(raw_json)
                    order_no = str(row.get("ord_no") or "").strip()
                    if not order_no:
                        order_no = f"missing:{sha256_hex(raw_json)[:24]}"
                    symbol = normalize_symbol(row.get("stk_cd"))
                    side = normalize_side(row.get("io_tp_nm"), row.get("trde_tp"))
                    self._conn.execute(
                        """
                        INSERT INTO orders(
                            account_id, trade_date_kst, order_no, original_order_no,
                            symbol, name, side, trade_type, order_type, exchange,
                            order_qty, order_price_krw, confirm_qty, accept_type,
                            settlement_type, credit_type, modify_cancel_type,
                            communication_order_type, stop_price_krw,
                            first_seen_run_id, last_seen_run_id, last_raw_response_id,
                            revision_sha256, raw_json
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                 ?, ?, ?, ?, ?)
                        ON CONFLICT(account_id, trade_date_kst, order_no) DO UPDATE SET
                            original_order_no=excluded.original_order_no,
                            symbol=excluded.symbol,
                            name=excluded.name,
                            side=excluded.side,
                            trade_type=excluded.trade_type,
                            order_type=excluded.order_type,
                            exchange=excluded.exchange,
                            order_qty=excluded.order_qty,
                            order_price_krw=excluded.order_price_krw,
                            confirm_qty=excluded.confirm_qty,
                            accept_type=excluded.accept_type,
                            settlement_type=excluded.settlement_type,
                            credit_type=excluded.credit_type,
                            modify_cancel_type=excluded.modify_cancel_type,
                            communication_order_type=excluded.communication_order_type,
                            stop_price_krw=excluded.stop_price_krw,
                            last_seen_run_id=excluded.last_seen_run_id,
                            last_raw_response_id=excluded.last_raw_response_id,
                            revision_sha256=excluded.revision_sha256,
                            raw_json=excluded.raw_json
                        """,
                        (
                            account_id,
                            trade_day.isoformat(),
                            order_no,
                            str(row.get("orig_ord_no") or "").strip() or None,
                            symbol,
                            str(row.get("stk_nm") or "").strip(),
                            side,
                            str(row.get("trde_tp") or "").strip() or None,
                            str(row.get("io_tp_nm") or "").strip() or None,
                            str(row.get("dmst_stex_tp") or "").strip() or None,
                            parse_int(row.get("ord_qty"), absolute=True),
                            parse_int(row.get("ord_uv"), absolute=True),
                            parse_int(row.get("cnfm_qty"), absolute=True),
                            str(row.get("acpt_tp") or "").strip() or None,
                            str(row.get("setl_tp") or "").strip() or None,
                            str(row.get("crd_deal_tp") or "").strip() or None,
                            str(row.get("mdfy_cncl_tp") or "").strip() or None,
                            str(row.get("comm_ord_tp") or "").strip() or None,
                            parse_int(row.get("cond_uv"), absolute=True),
                            run_id,
                            run_id,
                            raw_id,
                            revision,
                            raw_json,
                        ),
                    )

                    fill_qty = parse_int(row.get("cntr_qty"), absolute=True)
                    if fill_qty <= 0:
                        continue
                    fill_price = parse_int(row.get("cntr_uv"), absolute=True)
                    key = execution_key(ymd, row)
                    executed_raw = str(row.get("cntr_tm") or "").strip() or None
                    source_execution_no = str(row.get("cntr_no") or "").strip()
                    if not source_execution_no or not source_execution_no.strip("0"):
                        source_execution_no = None
                    self._conn.execute(
                        """
                        INSERT INTO executions(
                            account_id, trade_date_kst, execution_key, order_no,
                            source_execution_no, original_order_no, symbol, name, side,
                            executed_at_kst, executed_at_raw, execution_qty,
                            execution_price_krw, gross_amount_krw, exchange,
                            modify_cancel_type, first_seen_run_id, last_seen_run_id,
                            last_raw_response_id, revision_sha256, raw_json
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(account_id, trade_date_kst, execution_key) DO UPDATE SET
                            order_no=excluded.order_no,
                            source_execution_no=excluded.source_execution_no,
                            original_order_no=excluded.original_order_no,
                            symbol=excluded.symbol,
                            name=excluded.name,
                            side=excluded.side,
                            executed_at_kst=excluded.executed_at_kst,
                            executed_at_raw=excluded.executed_at_raw,
                            execution_qty=excluded.execution_qty,
                            execution_price_krw=excluded.execution_price_krw,
                            gross_amount_krw=excluded.gross_amount_krw,
                            exchange=excluded.exchange,
                            modify_cancel_type=excluded.modify_cancel_type,
                            last_seen_run_id=excluded.last_seen_run_id,
                            last_raw_response_id=excluded.last_raw_response_id,
                            revision_sha256=excluded.revision_sha256,
                            raw_json=excluded.raw_json
                        """,
                        (
                            account_id,
                            trade_day.isoformat(),
                            key,
                            order_no,
                            source_execution_no,
                            str(row.get("orig_ord_no") or "").strip() or None,
                            symbol,
                            str(row.get("stk_nm") or "").strip(),
                            side,
                            normalize_kst_timestamp(ymd, executed_raw),
                            executed_raw,
                            fill_qty,
                            fill_price,
                            fill_qty * fill_price,
                            str(row.get("dmst_stex_tp") or "").strip() or None,
                            str(row.get("mdfy_cncl_tp") or "").strip() or None,
                            run_id,
                            run_id,
                            raw_id,
                            revision,
                            raw_json,
                        ),
                    )
                    execution_rows += 1

            self._conn.execute(
                """
                INSERT INTO sync_days(
                    run_id, trade_date_kst, status, page_count,
                    source_row_count, execution_row_count
                ) VALUES(?, ?, 'SUCCEEDED', ?, ?, ?)
                ON CONFLICT(run_id, trade_date_kst) DO UPDATE SET
                    status='SUCCEEDED', page_count=excluded.page_count,
                    source_row_count=excluded.source_row_count,
                    execution_row_count=excluded.execution_row_count,
                    error_message=NULL
                """,
                (run_id, trade_day.isoformat(), len(pages), source_rows, execution_rows),
            )
        return source_rows, execution_rows

    def store_realized_window(
        self, run_id: int, account_id: str, start: date, end: date,
        pages: Sequence[ApiPage],
    ) -> int:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        source_rows = 0
        last_raw_by_key: dict[tuple[str, str], int] = {}
        with self._conn:
            for page in pages:
                raw_id = self._store_raw_page(run_id, page)
                rows = self._rows(page, ("dt_stk_rlzt_pl", "list", "output"))
                source_rows += len(rows)
                for row in rows:
                    day = str(row.get("dt") or row.get("date") or "").strip()
                    symbol = normalize_symbol(row.get("stk_cd"))
                    if len(day) != 8 or not symbol:
                        continue
                    parsed_day = datetime.strptime(day, "%Y%m%d").date()
                    if parsed_day < start or parsed_day > end:
                        raise KiwoomHistoryError(
                            f"ka10073: response date {day} is outside requested window"
                        )
                    key = (day, symbol)
                    qty = parse_int(row.get("cntr_qty"), absolute=True)
                    aggregate = grouped.setdefault(key, {
                        "name": "", "sold_qty": 0, "buy_amount": Decimal("0"),
                        "sell_amount": Decimal("0"), "pnl": Decimal("0"),
                        "commission": Decimal("0"), "tax": Decimal("0"), "rows": [],
                    })
                    aggregate["name"] = str(row.get("stk_nm") or aggregate["name"]).strip()
                    aggregate["sold_qty"] += qty
                    aggregate["buy_amount"] += Decimal(qty) * parse_decimal(
                        row.get("buy_uv"), absolute=True
                    )
                    aggregate["sell_amount"] += Decimal(qty) * parse_decimal(
                        row.get("cntr_pric"), absolute=True
                    )
                    aggregate["pnl"] += parse_decimal(row.get("tdy_sel_pl"))
                    aggregate["commission"] += parse_decimal(
                        row.get("tdy_trde_cmsn"), absolute=True
                    )
                    aggregate["tax"] += parse_decimal(
                        row.get("tdy_trde_tax"), absolute=True
                    )
                    aggregate["rows"].append(row)
                    last_raw_by_key[key] = raw_id

            for (day, symbol), aggregate in grouped.items():
                buy_amount: Decimal = aggregate["buy_amount"]
                sell_amount: Decimal = aggregate["sell_amount"]
                pnl: Decimal = aggregate["pnl"]
                commission: Decimal = aggregate["commission"]
                tax: Decimal = aggregate["tax"]
                aggregate_rate = (
                    decimal_text((pnl / buy_amount) * Decimal("100"))
                    if buy_amount
                    else None
                )
                self._conn.execute(
                    """
                    INSERT INTO realized_pnl(
                        account_id, trade_date_kst, symbol, name, sold_qty,
                        buy_amount_krw, sell_amount_krw, broker_realized_pnl_krw,
                        commission_krw, tax_krw,
                        buy_amount_krw_exact, sell_amount_krw_exact,
                        broker_realized_pnl_krw_exact, commission_krw_exact,
                        tax_krw_exact, pnl_rate_text, source_row_count,
                        last_seen_run_id, last_raw_response_id, raw_rows_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_id, trade_date_kst, symbol) DO UPDATE SET
                        name=excluded.name,
                        sold_qty=excluded.sold_qty,
                        buy_amount_krw=excluded.buy_amount_krw,
                        sell_amount_krw=excluded.sell_amount_krw,
                        broker_realized_pnl_krw=excluded.broker_realized_pnl_krw,
                        commission_krw=excluded.commission_krw,
                        tax_krw=excluded.tax_krw,
                        buy_amount_krw_exact=excluded.buy_amount_krw_exact,
                        sell_amount_krw_exact=excluded.sell_amount_krw_exact,
                        broker_realized_pnl_krw_exact=excluded.broker_realized_pnl_krw_exact,
                        commission_krw_exact=excluded.commission_krw_exact,
                        tax_krw_exact=excluded.tax_krw_exact,
                        pnl_rate_text=excluded.pnl_rate_text,
                        source_row_count=excluded.source_row_count,
                        last_seen_run_id=excluded.last_seen_run_id,
                        last_raw_response_id=excluded.last_raw_response_id,
                        raw_rows_json=excluded.raw_rows_json
                    """,
                    (
                        account_id,
                        datetime.strptime(day, "%Y%m%d").date().isoformat(),
                        symbol,
                        aggregate["name"],
                        aggregate["sold_qty"],
                        decimal_to_won(buy_amount),
                        decimal_to_won(sell_amount),
                        decimal_to_won(pnl),
                        decimal_to_won(commission),
                        decimal_to_won(tax),
                        decimal_text(buy_amount),
                        decimal_text(sell_amount),
                        decimal_text(pnl),
                        decimal_text(commission),
                        decimal_text(tax),
                        aggregate_rate,
                        len(aggregate["rows"]),
                        run_id,
                        last_raw_by_key[(day, symbol)],
                        canonical_json(aggregate["rows"]),
                    ),
                )

            self._conn.execute(
                """
                INSERT INTO sync_windows(
                    run_id, api_id, start_date_kst, end_date_kst,
                    status, page_count, source_row_count
                ) VALUES(?, 'ka10073', ?, ?, 'SUCCEEDED', ?, ?)
                ON CONFLICT(run_id, api_id, start_date_kst, end_date_kst) DO UPDATE SET
                    status='SUCCEEDED', page_count=excluded.page_count,
                    source_row_count=excluded.source_row_count, error_message=NULL
                """,
                (run_id, start.isoformat(), end.isoformat(), len(pages), source_rows),
            )
        return source_rows

    def store_daily_pnl_window(
        self, run_id: int, account_id: str, start: date, end: date,
        pages: Sequence[ApiPage],
    ) -> int:
        source_rows = 0
        with self._conn:
            for page in pages:
                raw_id = self._store_raw_page(run_id, page)
                rows = self._rows(page, ("dt_rlzt_pl", "list", "output"))
                source_rows += len(rows)
                for row in rows:
                    day = str(row.get("dt") or row.get("date") or row.get("stdr_dt") or "").strip()
                    if len(day) != 8:
                        continue
                    parsed_day = datetime.strptime(day, "%Y%m%d").date()
                    if parsed_day < start or parsed_day > end:
                        raise KiwoomHistoryError(
                            f"ka10074: response date {day} is outside requested window"
                        )
                    buy = parse_decimal(row.get("buy_amt"), absolute=True)
                    sell = parse_decimal(row.get("sell_amt"), absolute=True)
                    pnl = parse_decimal(row.get("tdy_sel_pl"))
                    commission = parse_decimal(row.get("tdy_trde_cmsn"), absolute=True)
                    tax = parse_decimal(row.get("tdy_trde_tax"), absolute=True)
                    net = pnl - commission - tax
                    self._conn.execute(
                        """
                        INSERT INTO daily_pnl(
                            account_id, trade_date_kst, buy_amount_krw,
                            sell_amount_krw, broker_realized_pnl_krw,
                            commission_krw, tax_krw, net_pnl_after_costs_krw,
                            buy_amount_krw_exact, sell_amount_krw_exact,
                            broker_realized_pnl_krw_exact, commission_krw_exact,
                            tax_krw_exact, net_pnl_after_costs_krw_exact,
                            last_seen_run_id, last_raw_response_id, raw_json
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(account_id, trade_date_kst) DO UPDATE SET
                            buy_amount_krw=excluded.buy_amount_krw,
                            sell_amount_krw=excluded.sell_amount_krw,
                            broker_realized_pnl_krw=excluded.broker_realized_pnl_krw,
                            commission_krw=excluded.commission_krw,
                            tax_krw=excluded.tax_krw,
                            net_pnl_after_costs_krw=excluded.net_pnl_after_costs_krw,
                            buy_amount_krw_exact=excluded.buy_amount_krw_exact,
                            sell_amount_krw_exact=excluded.sell_amount_krw_exact,
                            broker_realized_pnl_krw_exact=excluded.broker_realized_pnl_krw_exact,
                            commission_krw_exact=excluded.commission_krw_exact,
                            tax_krw_exact=excluded.tax_krw_exact,
                            net_pnl_after_costs_krw_exact=excluded.net_pnl_after_costs_krw_exact,
                            last_seen_run_id=excluded.last_seen_run_id,
                            last_raw_response_id=excluded.last_raw_response_id,
                            raw_json=excluded.raw_json
                        """,
                        (
                            account_id,
                            parsed_day.isoformat(),
                            decimal_to_won(buy),
                            decimal_to_won(sell),
                            decimal_to_won(pnl),
                            decimal_to_won(commission),
                            decimal_to_won(tax),
                            decimal_to_won(net),
                            decimal_text(buy),
                            decimal_text(sell),
                            decimal_text(pnl),
                            decimal_text(commission),
                            decimal_text(tax),
                            decimal_text(net),
                            run_id,
                            raw_id,
                            canonical_json(row),
                        ),
                    )

            self._conn.execute(
                """
                INSERT INTO sync_windows(
                    run_id, api_id, start_date_kst, end_date_kst,
                    status, page_count, source_row_count
                ) VALUES(?, 'ka10074', ?, ?, 'SUCCEEDED', ?, ?)
                ON CONFLICT(run_id, api_id, start_date_kst, end_date_kst) DO UPDATE SET
                    status='SUCCEEDED', page_count=excluded.page_count,
                    source_row_count=excluded.source_row_count, error_message=NULL
                """,
                (run_id, start.isoformat(), end.isoformat(), len(pages), source_rows),
            )
        return source_rows

    def store_cash_ledger_window(
        self, run_id: int, account_id: str, start: date, end: date,
        pages: Sequence[ApiPage],
    ) -> int:
        source_rows = 0
        with self._conn:
            for page in pages:
                raw_id = self._store_raw_page(run_id, page)
                rows = self._rows(page, ("trst_ovrl_trde_prps_array", "list", "output"))
                source_rows += len(rows)
                for row in rows:
                    day = str(row.get("trde_dt") or "").strip()
                    if len(day) != 8 or not day.isdigit():
                        continue
                    parsed_day = datetime.strptime(day, "%Y%m%d").date()
                    if parsed_day < start or parsed_day > end:
                        raise KiwoomHistoryError(
                            f"kt00015: response date {day} is outside requested window"
                        )
                    raw_json = canonical_json(row)
                    revision = sha256_hex(raw_json)
                    transaction_no = str(row.get("trde_no") or "").strip()
                    transaction_key = (
                        transaction_no
                        if transaction_no and transaction_no.strip("0")
                        else f"hash:{sha256_hex(raw_json)}"
                    )
                    processing_time = str(row.get("proc_tm") or "").strip() or None
                    direction = normalize_side(
                        row.get("io_tp_nm"),
                        f"{row.get('rmrk_nm') or ''} {row.get('trde_kind_nm') or ''}",
                    )
                    trade_amount = parse_decimal(row.get("trde_amt"))
                    settlement_amount = parse_decimal(row.get("exct_amt"))
                    commission = parse_decimal(row.get("cmsn"), absolute=True)
                    transaction_tax = parse_decimal(
                        row.get("trde_agri_tax"), absolute=True
                    )
                    self._conn.execute(
                        """
                        INSERT INTO cash_ledger(
                            account_id, trade_date_kst, transaction_key,
                            transaction_no, original_transaction_no, symbol, name,
                            description, transaction_type, direction, credit_type,
                            quantity, trade_amount_krw, settlement_amount_krw,
                            commission_krw, transaction_tax_krw,
                            trade_amount_krw_exact, settlement_amount_krw_exact,
                            commission_krw_exact, transaction_tax_krw_exact,
                            processing_time_raw, processing_at_kst,
                            last_seen_run_id, last_raw_response_id,
                            revision_sha256, raw_json
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(account_id, trade_date_kst, transaction_key) DO UPDATE SET
                            transaction_no=excluded.transaction_no,
                            original_transaction_no=excluded.original_transaction_no,
                            symbol=excluded.symbol,
                            name=excluded.name,
                            description=excluded.description,
                            transaction_type=excluded.transaction_type,
                            direction=excluded.direction,
                            credit_type=excluded.credit_type,
                            quantity=excluded.quantity,
                            trade_amount_krw=excluded.trade_amount_krw,
                            settlement_amount_krw=excluded.settlement_amount_krw,
                            commission_krw=excluded.commission_krw,
                            transaction_tax_krw=excluded.transaction_tax_krw,
                            trade_amount_krw_exact=excluded.trade_amount_krw_exact,
                            settlement_amount_krw_exact=excluded.settlement_amount_krw_exact,
                            commission_krw_exact=excluded.commission_krw_exact,
                            transaction_tax_krw_exact=excluded.transaction_tax_krw_exact,
                            processing_time_raw=excluded.processing_time_raw,
                            processing_at_kst=excluded.processing_at_kst,
                            last_seen_run_id=excluded.last_seen_run_id,
                            last_raw_response_id=excluded.last_raw_response_id,
                            revision_sha256=excluded.revision_sha256,
                            raw_json=excluded.raw_json
                        """,
                        (
                            account_id,
                            parsed_day.isoformat(),
                            transaction_key,
                            transaction_no or None,
                            str(row.get("orig_deal_no") or "").strip() or None,
                            normalize_symbol(row.get("stk_cd")),
                            str(row.get("stk_nm") or "").strip(),
                            str(row.get("rmrk_nm") or "").strip() or None,
                            str(row.get("trde_kind_nm") or "").strip() or None,
                            direction,
                            str(row.get("crd_deal_tp_nm") or "").strip() or None,
                            parse_int(row.get("trde_qty_jwa_cnt"), absolute=True),
                            decimal_to_won(trade_amount),
                            decimal_to_won(settlement_amount),
                            decimal_to_won(commission),
                            decimal_to_won(transaction_tax),
                            decimal_text(trade_amount),
                            decimal_text(settlement_amount),
                            decimal_text(commission),
                            decimal_text(transaction_tax),
                            processing_time,
                            normalize_kst_timestamp(day, processing_time),
                            run_id,
                            raw_id,
                            revision,
                            raw_json,
                        ),
                    )

            self._conn.execute(
                """
                INSERT INTO sync_windows(
                    run_id, api_id, start_date_kst, end_date_kst,
                    status, page_count, source_row_count
                ) VALUES(?, 'kt00015', ?, ?, 'SUCCEEDED', ?, ?)
                ON CONFLICT(run_id, api_id, start_date_kst, end_date_kst) DO UPDATE SET
                    status='SUCCEEDED', page_count=excluded.page_count,
                    source_row_count=excluded.source_row_count, error_message=NULL
                """,
                (run_id, start.isoformat(), end.isoformat(), len(pages), source_rows),
            )
        return source_rows

    def set_success_metadata(
        self, *, environment: str, start: date, end: date, account_id: str
    ) -> None:
        existing = dict(self._conn.execute("SELECT key, value FROM schema_meta"))
        existing_start = existing.get("coverage_requested_from")
        existing_end = existing.get("coverage_requested_to")
        coverage_start = min(date.fromisoformat(existing_start), start) if existing_start else start
        coverage_end = max(date.fromisoformat(existing_end), end) if existing_end else end
        values = {
            "broker": "KIWOOM",
            "environment": environment,
            "coverage_requested_from": coverage_start.isoformat(),
            "coverage_requested_to": coverage_end.isoformat(),
            "account_id": account_id,
            "last_success_at_utc": utc_now_iso(),
            "api_tr_ids": (
                "kt00009,ka10073,ka10074,kt00015"
                if environment == "real"
                else "kt00009,ka10073,ka10074"
            ),
            "kt00015_status": (
                "collected" if environment == "real" else "unsupported_by_mock_server"
            ),
        }
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO schema_meta(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                values.items(),
            )

    def latest_successful_day(self, account_id: str) -> Optional[date]:
        row = self._conn.execute(
            """
            SELECT MAX(d.trade_date_kst) AS max_day
            FROM sync_days d
            JOIN sync_runs r ON r.run_id=d.run_id
            WHERE r.account_id=? AND r.status='SUCCEEDED' AND d.status='SUCCEEDED'
            """,
            (account_id,),
        ).fetchone()
        return date.fromisoformat(row["max_day"]) if row and row["max_day"] else None

    def last_run_status(self, account_id: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT status FROM sync_runs WHERE account_id=? ORDER BY run_id DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        return str(row["status"]) if row else None


async def sync_history(
    *,
    store: TradeHistoryStore,
    client: KiwoomHistoryClient,
    account_id: str,
    environment: str,
    start: date,
    end: date,
    alias: str = "primary",
    progress_every: int = 10,
) -> int:
    days = list(business_days(start, end))
    store.register_account(account_id, alias, environment)
    run_id = store.begin_run(account_id, start, end, len(days))
    print(
        f"[sync] run={run_id} source={environment} range={start}..{end} "
        f"business_days={len(days)}",
        flush=True,
    )
    total_fills = 0
    total_source = 0
    try:
        for index, trade_day in enumerate(days, start=1):
            pages = await client.fetch_fills_for_day(trade_day)
            source_count, fill_count = store.store_fill_day(
                run_id, account_id, trade_day, pages
            )
            total_source += source_count
            total_fills += fill_count
            if index == 1 or index % progress_every == 0 or index == len(days):
                print(
                    f"[fills] {index}/{len(days)} date={trade_day} "
                    f"source_rows={total_source} executions={total_fills}",
                    flush=True,
                )

        windows = list(date_windows(start, end, 31))
        for index, (window_start, window_end) in enumerate(windows, start=1):
            realized_pages = await client.fetch_realized_pnl(window_start, window_end)
            realized_count = store.store_realized_window(
                run_id, account_id, window_start, window_end, realized_pages
            )
            daily_pages = await client.fetch_daily_pnl(window_start, window_end)
            daily_count = store.store_daily_pnl_window(
                run_id, account_id, window_start, window_end, daily_pages
            )
            if environment == "real":
                ledger_pages = await client.fetch_cash_ledger(window_start, window_end)
                ledger_count: int | str = store.store_cash_ledger_window(
                    run_id, account_id, window_start, window_end, ledger_pages
                )
            else:
                # Kiwoom mock returns rc=20 / RC9000 for kt00015.
                ledger_count = "unsupported"
            print(
                f"[pnl] {index}/{len(windows)} range={window_start}..{window_end} "
                f"realized_rows={realized_count} daily_rows={daily_count} "
                f"ledger_rows={ledger_count}",
                flush=True,
            )

        store.finish_run(run_id, succeeded=True)
        store.set_success_metadata(
            environment=environment, start=start, end=end, account_id=account_id
        )
        return run_id
    except Exception as exc:
        safe_error = str(exc)
        store.finish_run(run_id, succeeded=False, error=safe_error)
        raise


def verify_database(
    path: Path | str, *, expected_environment: Optional[str] = None
) -> VerificationResult:
    db_path = Path(path).expanduser().resolve()
    if not db_path.is_file():
        return VerificationResult(False, {"error": f"database not found: {db_path}"})
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "orders", "executions", "realized_pnl", "daily_pnl", "cash_ledger",
                "raw_responses", "sync_runs", "sync_days", "sync_windows",
            )
        }
        execution_range = conn.execute(
            "SELECT MIN(trade_date_kst), MAX(trade_date_kst) FROM executions"
        ).fetchone()
        metadata = dict(conn.execute("SELECT key, value FROM schema_meta"))
        schema_version = int(metadata.get("schema_version", "0"))
        environment_match = (
            expected_environment is None
            or metadata.get("environment") == expected_environment
        )

        last_run = conn.execute(
            "SELECT run_id, status, requested_from_kst, requested_to_kst "
            "FROM sync_runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        successful_runs = conn.execute(
            "SELECT COUNT(*) FROM sync_runs WHERE status='SUCCEEDED'"
        ).fetchone()[0]

        bad_raw_hashes = 0
        for row in conn.execute("SELECT response_sha256, response_body FROM raw_responses"):
            if sha256_hex(bytes(row["response_body"])) != row["response_sha256"]:
                bad_raw_hashes += 1

        bad_terminal_pages = conn.execute(
            """
            SELECT COUNT(*)
            FROM raw_responses r
            JOIN sync_runs sr ON sr.run_id=r.run_id AND sr.status='SUCCEEDED'
            WHERE r.page_no = (
                SELECT MAX(r2.page_no)
                FROM raw_responses r2
                WHERE r2.run_id=r.run_id
                  AND r2.api_id=r.api_id
                  AND r2.request_scope=r.request_scope
            )
              AND r.cont_yn != 'N'
            """
        ).fetchone()[0]

        stale_failed_rows: dict[str, int] = {}
        for table in ("orders", "executions", "realized_pnl", "daily_pnl", "cash_ledger"):
            stale_failed_rows[table] = conn.execute(
                f"""
                SELECT COUNT(*) FROM {table} t
                LEFT JOIN sync_runs sr ON sr.run_id=t.last_seen_run_id
                WHERE sr.status IS NULL OR sr.status!='SUCCEEDED'
                """
            ).fetchone()[0]

        coverage_start_text = metadata.get("coverage_requested_from")
        coverage_end_text = metadata.get("coverage_requested_to")
        coverage_error: Optional[str] = None
        expected_business_days: set[str] = set()
        covered_business_days: set[str] = set()
        uncovered_window_days: dict[str, int] = {}
        if not coverage_start_text or not coverage_end_text:
            coverage_error = "coverage metadata is missing"
        else:
            try:
                coverage_start = date.fromisoformat(coverage_start_text)
                coverage_end = date.fromisoformat(coverage_end_text)
                expected_business_days = {
                    item.isoformat() for item in business_days(coverage_start, coverage_end)
                }
                covered_business_days = {
                    str(row[0])
                    for row in conn.execute(
                        """
                        SELECT DISTINCT d.trade_date_kst
                        FROM sync_days d
                        JOIN sync_runs sr ON sr.run_id=d.run_id
                        WHERE sr.status='SUCCEEDED' AND d.status='SUCCEEDED'
                          AND d.trade_date_kst BETWEEN ? AND ?
                        """,
                        (coverage_start.isoformat(), coverage_end.isoformat()),
                    )
                }

                required_window_apis = ["ka10073", "ka10074"]
                if metadata.get("environment") == "real":
                    required_window_apis.append("kt00015")
                all_dates: list[date] = []
                cursor_day = coverage_start
                while cursor_day <= coverage_end:
                    all_dates.append(cursor_day)
                    cursor_day += timedelta(days=1)
                for api_id in required_window_apis:
                    intervals = [
                        (date.fromisoformat(row[0]), date.fromisoformat(row[1]))
                        for row in conn.execute(
                            """
                            SELECT w.start_date_kst, w.end_date_kst
                            FROM sync_windows w
                            JOIN sync_runs sr ON sr.run_id=w.run_id
                            WHERE sr.status='SUCCEEDED' AND w.status='SUCCEEDED'
                              AND w.api_id=?
                            """,
                            (api_id,),
                        )
                    ]
                    uncovered_window_days[api_id] = sum(
                        1 for item in all_dates
                        if not any(left <= item <= right for left, right in intervals)
                    )
            except ValueError as exc:
                coverage_error = f"invalid coverage metadata: {exc}"

        missing_business_days = expected_business_days - covered_business_days
        extra_business_days = covered_business_days - expected_business_days
        failed_days_in_success_runs = conn.execute(
            """
            SELECT COUNT(*) FROM sync_days d
            JOIN sync_runs sr ON sr.run_id=d.run_id
            WHERE sr.status='SUCCEEDED' AND d.status!='SUCCEEDED'
            """
        ).fetchone()[0]
        duplicate_source_fill_keys = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT account_id, trade_date_kst, order_no, source_execution_no
                FROM executions
                WHERE source_execution_no IS NOT NULL
                GROUP BY account_id, trade_date_kst, order_no, source_execution_no
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]

        realized_by_day: dict[str, dict[str, Decimal]] = {}
        for row in conn.execute(
            """
            SELECT trade_date_kst, sell_amount_krw_exact,
                   broker_realized_pnl_krw_exact, tax_krw_exact
            FROM realized_pnl
            """
        ):
            values = realized_by_day.setdefault(
                str(row["trade_date_kst"]),
                {"sell": Decimal("0"), "pnl": Decimal("0"), "tax": Decimal("0")},
            )
            values["sell"] += Decimal(str(row["sell_amount_krw_exact"]))
            values["pnl"] += Decimal(str(row["broker_realized_pnl_krw_exact"]))
            values["tax"] += Decimal(str(row["tax_krw_exact"]))
        daily_by_day = {
            str(row["trade_date_kst"]): {
                "sell": Decimal(str(row["sell_amount_krw_exact"])),
                "pnl": Decimal(str(row["broker_realized_pnl_krw_exact"])),
                "tax": Decimal(str(row["tax_krw_exact"])),
            }
            for row in conn.execute(
                """
                SELECT trade_date_kst, sell_amount_krw_exact,
                       broker_realized_pnl_krw_exact, tax_krw_exact
                FROM daily_pnl
                """
            )
        }
        common_pnl_days = set(realized_by_day) & set(daily_by_day)
        pnl_date_mismatches = set(realized_by_day) ^ set(daily_by_day)
        tolerance = Decimal("0.01")
        sell_reconciliation_mismatches = 0
        tax_reconciliation_mismatches = 0
        max_pnl_rounding_difference = Decimal("0")
        for day in common_pnl_days:
            realized = realized_by_day[day]
            daily = daily_by_day[day]
            if abs(realized["sell"] - daily["sell"]) > tolerance:
                sell_reconciliation_mismatches += 1
            if abs(realized["tax"] - daily["tax"]) > tolerance:
                tax_reconciliation_mismatches += 1
            max_pnl_rounding_difference = max(
                max_pnl_rounding_difference,
                abs(realized["pnl"] - daily["pnl"]),
            )

        details = {
            "path": str(db_path),
            "size_bytes": db_path.stat().st_size,
            "sha256": sha256_file(db_path),
            "sqlite_version": sqlite3.sqlite_version,
            "expected_environment": expected_environment,
            "environment_match": environment_match,
            "integrity_check": integrity,
            "foreign_key_issues": len(fk_issues),
            "raw_hash_mismatches": bad_raw_hashes,
            "bad_terminal_pages": bad_terminal_pages,
            "successful_runs": successful_runs,
            "last_run_id": int(last_run["run_id"]) if last_run else None,
            "last_run_status": str(last_run["status"]) if last_run else None,
            "coverage_error": coverage_error,
            "expected_business_days": len(expected_business_days),
            "covered_business_days": len(covered_business_days),
            "missing_business_days": len(missing_business_days),
            "extra_business_days": len(extra_business_days),
            "uncovered_window_days": uncovered_window_days,
            "failed_days_in_success_runs": failed_days_in_success_runs,
            "stale_failed_rows": stale_failed_rows,
            "duplicate_source_fill_keys": duplicate_source_fill_keys,
            "pnl_reconciliation": {
                "compared_days": len(common_pnl_days),
                "date_mismatches": len(pnl_date_mismatches),
                "sell_amount_mismatch_days": sell_reconciliation_mismatches,
                "tax_mismatch_days": tax_reconciliation_mismatches,
                "max_realized_pnl_rounding_difference_krw": decimal_text(
                    max_pnl_rounding_difference
                ),
                "semantics": (
                    "ka10073 buy_uv is sold-position cost basis; ka10074 buy_amt is "
                    "same-day buy activity, so buy amount and commission are not equal-scope totals"
                ),
            },
            "counts": counts,
            "execution_date_min": execution_range[0],
            "execution_date_max": execution_range[1],
            "sync_date_min": min(covered_business_days) if covered_business_days else None,
            "sync_date_max": max(covered_business_days) if covered_business_days else None,
            "metadata": metadata,
        }
        ok = all((
            integrity == "ok",
            not fk_issues,
            bad_raw_hashes == 0,
            bad_terminal_pages == 0,
            successful_runs > 0,
            bool(last_run and last_run["status"] == "SUCCEEDED"),
            coverage_error is None,
            bool(expected_business_days),
            not missing_business_days,
            not extra_business_days,
            not any(uncovered_window_days.values()),
            failed_days_in_success_runs == 0,
            not any(stale_failed_rows.values()),
            duplicate_source_fill_keys == 0,
            not pnl_date_mismatches,
            sell_reconciliation_mismatches == 0,
            tax_reconciliation_mismatches == 0,
            counts["raw_responses"] > 0,
            schema_version == SCHEMA_VERSION,
            environment_match,
        ))
        return VerificationResult(ok, details)
    except sqlite3.DatabaseError as exc:
        return VerificationResult(False, {"path": str(db_path), "error": str(exc)})
    finally:
        conn.close()


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_snapshot(source: Path | str, output: Path | str) -> VerificationResult:
    source_path = Path(source).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()
    if source_path == output_path:
        raise ValueError("snapshot output must differ from source database")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(f"snapshot already exists: {output_path}")
    source_verification = verify_database(source_path)
    if not source_verification.ok:
        raise KiwoomHistoryError("source database did not pass completeness verification")
    fd = os.open(output_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(fd)
    source_conn = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    target_conn = sqlite3.connect(output_path)
    try:
        source_conn.backup(target_conn)
        target_conn.execute("PRAGMA journal_mode = DELETE")
        target_conn.commit()
    except Exception:
        target_conn.close()
        source_conn.close()
        output_path.unlink(missing_ok=True)
        raise
    finally:
        try:
            target_conn.close()
        finally:
            source_conn.close()
    os.chmod(output_path, 0o600)
    return verify_database(output_path)


def build_manifest(result: VerificationResult) -> dict[str, Any]:
    return {
        "format": "kiwoom-trade-history-sqlite",
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": utc_now_iso(),
        "verification_ok": result.ok,
        **result.details,
    }


__all__ = [
    "ALLOWED_BASE_URLS",
    "ApiPage",
    "KiwoomHistoryClient",
    "KiwoomHistoryError",
    "TradeHistoryStore",
    "VerificationResult",
    "account_fingerprint",
    "build_manifest",
    "business_days",
    "create_snapshot",
    "date_windows",
    "execution_key",
    "normalize_side",
    "normalize_symbol",
    "one_year_ago",
    "parse_yyyymmdd",
    "sha256_file",
    "sync_history",
    "verify_database",
]
