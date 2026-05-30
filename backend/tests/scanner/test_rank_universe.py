"""RankUniverseProvider 단위 테스트 — 당일 순위 4종 합집합 유니버스.

mock httpx 응답 기반 (키움 rkinfo 응답 구조). 실 API 미호출.
"""
from __future__ import annotations

import pytest

from backend.core.scanner.rank_universe import (
    DEFAULT_SOURCES,
    RankSource,
    RankUniverseProvider,
)


# ─── 테스트 더블 ─────────────────────────────────────────────────────────────
class _FakeToken:
    class _Secret:
        @staticmethod
        def get_secret_value() -> str:
            return "tok"
    access_token = _Secret()


class _FakeOAuth:
    base_url = "https://mockapi.kiwoom.com"

    def __init__(self):
        self.invalidated = 0

    async def get_token(self):
        return _FakeToken()

    def invalidate_token(self):
        self.invalidated += 1


class _MockResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        pass


class _MockClient:
    """api-id 헤더로 분기해 미리 준비된 list 응답 반환."""

    def __init__(self, by_tr: dict[str, dict]):
        self._by_tr = by_tr
        self.calls: list[dict] = []

    async def post(self, url, headers, json):
        tr = headers["api-id"]
        self.calls.append({"url": url, "tr": tr, "body": json})
        return _MockResponse(self._by_tr.get(tr, {"return_code": 0}))

    async def aclose(self):
        pass


def _ok(list_key: str, codes: list[str]) -> dict:
    return {"return_code": 0, list_key: [{"stk_cd": c, "stk_nm": c} for c in codes]}


# ─── 테스트 ──────────────────────────────────────────────────────────────────
def test_default_sources_are_today_rank_4():
    trs = {s.tr_id for s in DEFAULT_SOURCES}
    assert trs == {"ka10032", "ka10030", "ka10027", "ka10023"}


@pytest.mark.asyncio
async def test_union_dedup_and_order():
    """소스 우선순위(거래대금>거래량>등락률>급증) + 중복 제거(첫 등장 유지)."""
    by_tr = {
        "ka10032": _ok("trde_prica_upper", ["005930", "000660"]),
        "ka10030": _ok("tdy_trde_qty_upper", ["000660", "035720"]),   # 000660 중복
        "ka10027": _ok("pred_pre_flu_rt_upper", ["035420"]),
        "ka10023": _ok("trde_qty_sdnin", ["005930", "068270"]),       # 005930 중복
    }
    client = _MockClient(by_tr)
    provider = RankUniverseProvider(_FakeOAuth(), http_client=client, rate_limit_seconds=0)
    universe = await provider.fetch_universe()
    assert universe == ["005930", "000660", "035720", "035420", "068270"]
    # 4개 소스 모두 호출
    assert [c["tr"] for c in client.calls] == ["ka10032", "ka10030", "ka10027", "ka10023"]


@pytest.mark.asyncio
async def test_symbol_normalization():
    """통합거래소 마커 _AL/_NX 제거."""
    by_tr = {"ka10032": _ok("trde_prica_upper", ["005930_AL", "000660_NX"])}
    provider = RankUniverseProvider(
        _FakeOAuth(), http_client=_MockClient(by_tr), rate_limit_seconds=0,
        sources=(DEFAULT_SOURCES[0],),
    )
    universe = await provider.fetch_universe()
    assert universe == ["005930", "000660"]


@pytest.mark.asyncio
async def test_per_source_limit():
    by_tr = {"ka10032": _ok("trde_prica_upper", [f"{i:06d}" for i in range(10)])}
    provider = RankUniverseProvider(
        _FakeOAuth(), http_client=_MockClient(by_tr), rate_limit_seconds=0,
        sources=(DEFAULT_SOURCES[0],), per_source_limit=3,
    )
    universe = await provider.fetch_universe()
    assert universe == ["000000", "000001", "000002"]


@pytest.mark.asyncio
async def test_max_symbols_cut():
    by_tr = {
        "ka10032": _ok("trde_prica_upper", ["A", "B"]),
        "ka10030": _ok("tdy_trde_qty_upper", ["C", "D"]),
        "ka10027": _ok("pred_pre_flu_rt_upper", ["E"]),
        "ka10023": _ok("trde_qty_sdnin", ["F"]),
    }
    provider = RankUniverseProvider(_FakeOAuth(), http_client=_MockClient(by_tr), rate_limit_seconds=0)
    universe = await provider.fetch_universe(max_symbols=3)
    assert universe == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_partial_source_failure_tolerated():
    """한 소스가 에러 응답(rc!=0)이어도 나머지로 유니버스 구성."""
    by_tr = {
        "ka10032": {"return_code": 1, "return_msg": "fail"},   # 실패
        "ka10030": _ok("tdy_trde_qty_upper", ["000660"]),
        "ka10027": _ok("pred_pre_flu_rt_upper", ["035420"]),
        "ka10023": _ok("trde_qty_sdnin", ["068270"]),
    }
    provider = RankUniverseProvider(_FakeOAuth(), http_client=_MockClient(by_tr), rate_limit_seconds=0)
    universe = await provider.fetch_universe()
    assert universe == ["000660", "035420", "068270"]


@pytest.mark.asyncio
async def test_auth_retry_on_rc3():
    """rc=3 인증실패 → 토큰 무효화 후 1회 재시도."""
    calls = {"n": 0}

    class _RetryClient:
        def __init__(self):
            self.calls = []

        async def post(self, url, headers, json):
            self.calls.append(headers["api-id"])
            calls["n"] += 1
            if calls["n"] == 1:
                return _MockResponse({"return_code": 3, "return_msg": "auth"})
            return _MockResponse(_ok("trde_prica_upper", ["005930"]))

        async def aclose(self):
            pass

    oauth = _FakeOAuth()
    provider = RankUniverseProvider(
        oauth, http_client=_RetryClient(), rate_limit_seconds=0,
        sources=(DEFAULT_SOURCES[0],),
    )
    universe = await provider.fetch_universe()
    assert universe == ["005930"]
    assert oauth.invalidated == 1
