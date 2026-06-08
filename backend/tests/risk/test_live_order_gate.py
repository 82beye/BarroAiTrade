"""BAR-OPS-17 — LiveOrderGate 테스트."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth, KiwoomNativeToken
from backend.core.gateway.kiwoom_native_orders import (
    KiwoomNativeOrderExecutor,
    OrderResult,
    OrderSide,
)
from backend.core.risk.live_order_gate import (
    DailyLossLimitExceeded,
    DailyOrderLimitExceeded,
    GatePolicy,
    InvalidOrderQty,
    LiveOrderGate,
    TradingDisabled,
)


def _oauth_mock() -> AsyncMock:
    o = AsyncMock(spec=KiwoomNativeOAuth)
    o.base_url = "https://mockapi.kiwoom.com"
    o.get_token = AsyncMock(
        return_value=KiwoomNativeToken(
            access_token=SecretStr("tok"), token_type="Bearer",
            expires_at=datetime(2099, 1, 1),
        )
    )
    return o


def _make_gate(tmp_path, *, dry_run: bool, policy=None):
    exec = KiwoomNativeOrderExecutor(oauth=_oauth_mock(), dry_run=dry_run)
    return LiveOrderGate(
        executor=exec,
        audit_path=tmp_path / "audit.csv",
        policy=policy,
    )


# -- env flag enforcement ---------------------------------------------------


@pytest.mark.asyncio
async def test_env_flag_required_when_not_dry_run(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    gate = _make_gate(tmp_path, dry_run=False)
    with pytest.raises(TradingDisabled, match="LIVE_TRADING_ENABLED"):
        await gate.place_buy(symbol="005930", qty=1)
    # audit BLOCKED 기록 확인
    audit = (tmp_path / "audit.csv").read_text(encoding="utf-8")
    assert "BLOCKED" in audit
    assert "LIVE_TRADING_ENABLED" in audit


@pytest.mark.asyncio
async def test_env_flag_not_required_when_dry_run(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    gate = _make_gate(tmp_path, dry_run=True)
    r = await gate.place_buy(symbol="005930", qty=1)
    assert r.dry_run is True


@pytest.mark.asyncio
async def test_env_flag_truthy_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    # KiwoomNativeOrderExecutor 의 dry_run=False 인데 mock 호출 X — 직접 _executor patch
    gate = _make_gate(tmp_path, dry_run=False)
    gate._executor.place_buy = AsyncMock(return_value=OrderResult(
        side=OrderSide.BUY, symbol="005930", qty=1, price=None,
        order_no="0001", return_code=0, return_msg="ok",
    ))
    r = await gate.place_buy(symbol="005930", qty=1)
    assert r.order_no == "0001"


# -- daily loss limit -------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_loss_limit_blocks_buy(tmp_path):
    gate = _make_gate(tmp_path, dry_run=True, policy=GatePolicy(daily_loss_limit_pct=Decimal("-3.0")))
    with pytest.raises(DailyLossLimitExceeded):
        await gate.place_buy(symbol="005930", qty=1, daily_pnl_pct=Decimal("-3.5"))


@pytest.mark.asyncio
async def test_daily_loss_limit_allows_sell_for_stop_loss(tmp_path):
    gate = _make_gate(tmp_path, dry_run=True, policy=GatePolicy(daily_loss_limit_pct=Decimal("-3.0")))
    r = await gate.place_sell(symbol="005930", qty=1, daily_pnl_pct=Decimal("-5.0"))
    assert r.dry_run is True


# -- daily order count limit ------------------------------------------------


@pytest.mark.asyncio
async def test_daily_max_orders(tmp_path):
    policy = GatePolicy(daily_max_orders=2, require_env_flag=False)
    gate = _make_gate(tmp_path, dry_run=True, policy=policy)
    await gate.place_buy(symbol="005930", qty=1)
    await gate.place_buy(symbol="005930", qty=1)
    with pytest.raises(DailyOrderLimitExceeded):
        await gate.place_buy(symbol="005930", qty=1)


# -- audit log -------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_logs_dry_run_orders(tmp_path):
    policy = GatePolicy(require_env_flag=False)
    gate = _make_gate(tmp_path, dry_run=True, policy=policy)
    await gate.place_buy(symbol="005930", qty=10)
    await gate.place_sell(symbol="005930", qty=5, price=Decimal("280000"))

    content = (tmp_path / "audit.csv").read_text(encoding="utf-8")
    assert "DRY_RUN,buy,005930,10" in content
    assert "DRY_RUN,sell,005930,5,280000" in content


@pytest.mark.asyncio
async def test_audit_logs_blocked_with_reason(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    gate = _make_gate(tmp_path, dry_run=False)
    with pytest.raises(TradingDisabled):
        await gate.place_buy(symbol="005930", qty=1)
    content = (tmp_path / "audit.csv").read_text(encoding="utf-8")
    # blocked=1, reason 포함
    assert ",BLOCKED,buy,005930,1," in content
    assert ",1,LIVE_TRADING_ENABLED" in content


# -- qty<=0 사전 차단 (2026-05-13 DCA ValueError 회귀 방지) ------------------


@pytest.mark.asyncio
async def test_qty_zero_buy_blocked_before_executor(tmp_path):
    """qty=0 매수 시도 → InvalidOrderQty + audit BLOCKED, executor 미호출."""
    policy = GatePolicy(require_env_flag=False)
    exec = KiwoomNativeOrderExecutor(oauth=_oauth_mock(), dry_run=True)
    exec.place_buy = AsyncMock()  # 호출되면 안 됨
    gate = LiveOrderGate(executor=exec, audit_path=tmp_path / "audit.csv", policy=policy)

    with pytest.raises(InvalidOrderQty, match="qty must be > 0"):
        await gate.place_buy(symbol="012860", qty=0)

    exec.place_buy.assert_not_called()
    content = (tmp_path / "audit.csv").read_text(encoding="utf-8")
    assert ",BLOCKED,buy,012860,0," in content
    assert "qty must be > 0" in content


@pytest.mark.asyncio
async def test_qty_negative_sell_blocked(tmp_path):
    """qty=-1 매도 시도 → 동일하게 InvalidOrderQty."""
    policy = GatePolicy(require_env_flag=False)
    gate = _make_gate(tmp_path, dry_run=True, policy=policy)
    with pytest.raises(InvalidOrderQty):
        await gate.place_sell(symbol="005930", qty=-1)
    content = (tmp_path / "audit.csv").read_text(encoding="utf-8")
    assert ",BLOCKED,sell,005930,-1," in content


@pytest.mark.asyncio
async def test_qty_positive_passes(tmp_path):
    """정상 qty 는 게이트 통과 후 executor 호출 — 회귀 방지."""
    policy = GatePolicy(require_env_flag=False)
    gate = _make_gate(tmp_path, dry_run=True, policy=policy)
    result = await gate.place_buy(symbol="005930", qty=1)
    assert result.dry_run is True


# ════════════════════════════════════════════════════════════════════════════
# BAR-OPS-35 (2026-06-08 매매복기) — 회로차단기 latch · 주문 retry · audit 체결컬럼
# 전부 default OFF: 위 회귀 테스트가 기본동작 불변을 보장. 아래는 ON 동작.
# ════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_p0_2_daily_loss_latch_sticky(tmp_path):
    """[P0#2] latch ON — 한도 도달 후 평가손익이 회복돼도 당일 매수 잠금 유지.

    459550: 12:30/12:35 -3.x% 차단 후 12:55 회복으로 통과시켜 2차(-509K) 재진입 허용 → latch 로 차단.
    """
    policy = GatePolicy(require_env_flag=False, daily_loss_latch=True,
                        daily_loss_limit_pct=Decimal("-3.0"))
    gate = _make_gate(tmp_path, dry_run=True, policy=policy)
    # 1) -3.5% → 차단 + latch 설정
    with pytest.raises(DailyLossLimitExceeded):
        await gate.place_buy(symbol="459550", qty=1, daily_pnl_pct=Decimal("-3.5"))
    # 2) -1.0% 로 회복 — latch 없으면 통과지만, latch 라 여전히 차단
    with pytest.raises(DailyLossLimitExceeded, match="latch"):
        await gate.place_buy(symbol="459550", qty=1, daily_pnl_pct=Decimal("-1.0"))


@pytest.mark.asyncio
async def test_p0_2_no_latch_allows_recovery(tmp_path):
    """latch OFF(기본) — 회복 시 매수 재개(기존 stateless 동작 보존)."""
    policy = GatePolicy(require_env_flag=False, daily_loss_limit_pct=Decimal("-3.0"))
    gate = _make_gate(tmp_path, dry_run=True, policy=policy)
    with pytest.raises(DailyLossLimitExceeded):
        await gate.place_buy(symbol="459550", qty=1, daily_pnl_pct=Decimal("-3.5"))
    # 회복 → 통과 (latch 아님)
    r = await gate.place_buy(symbol="459550", qty=1, daily_pnl_pct=Decimal("-1.0"))
    assert r.dry_run is True


@pytest.mark.asyncio
async def test_p0_5_sell_retry_succeeds_after_transient(tmp_path):
    """[P0#5] 매도 transient 오류 2회 후 성공 — order_retry_count=2 → 3회 시도."""
    policy = GatePolicy(require_env_flag=False, order_retry_count=2,
                        order_retry_backoff_sec=0.0, retry_sell_only=True)
    gate = _make_gate(tmp_path, dry_run=True, policy=policy)
    ok = OrderResult(side=OrderSide.SELL, symbol="459550", qty=1, price=None,
                     order_no="SELL_OK", return_code=0, return_msg="ok")
    gate._executor.place_sell = AsyncMock(side_effect=[RuntimeError("http"), RuntimeError("http"), ok])
    r = await gate.place_sell(symbol="459550", qty=1)
    assert r.order_no == "SELL_OK"
    assert gate._executor.place_sell.call_count == 3


@pytest.mark.asyncio
async def test_p0_5_buy_not_retried_when_sell_only(tmp_path):
    """retry_sell_only=True → 매수는 재시도 안 함(1회 시도 후 실패 전파)."""
    policy = GatePolicy(require_env_flag=False, order_retry_count=3, retry_sell_only=True)
    gate = _make_gate(tmp_path, dry_run=True, policy=policy)
    gate._executor.place_buy = AsyncMock(side_effect=RuntimeError("http"))
    with pytest.raises(RuntimeError):
        await gate.place_buy(symbol="459550", qty=1)
    assert gate._executor.place_buy.call_count == 1   # 재시도 없음
    # FAILED audit 기록
    content = (tmp_path / "audit.csv").read_text(encoding="utf-8")
    assert ",FAILED,buy,459550," in content


@pytest.mark.asyncio
async def test_p0_4_audit_has_fill_columns(tmp_path):
    """[P0#4] audit 헤더에 filled_qty/avg_fill_price 추가 + 전달 시 기록."""
    policy = GatePolicy(require_env_flag=False)
    gate = _make_gate(tmp_path, dry_run=True, policy=policy)
    await gate.place_buy(symbol="001740", qty=296, filled_qty=178,
                         avg_fill_price=Decimal("13500"))
    content = (tmp_path / "audit.csv").read_text(encoding="utf-8")
    header = content.splitlines()[0]
    assert "filled_qty" in header and "avg_fill_price" in header
    # 요청 296 ≠ 체결 178 분리 기록 (sync-loss 가시화)
    row = [l for l in content.splitlines() if ",001740," in l][0]
    assert "296" in row and "178" in row and "13500" in row


def test_p0_4_audit_migration_adds_fill_columns(tmp_path):
    """[P0#4] 기존 11컬럼(strategy_id) audit → 13컬럼으로 자동 migration."""
    import csv as _csv
    from backend.core.risk.live_order_gate import _migrate_audit_csv_header
    p = tmp_path / "audit.csv"
    old_header = ["ts", "action", "side", "symbol", "qty", "price",
                  "order_no", "return_code", "blocked", "reason", "strategy_id"]
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f)
        w.writerow(old_header)
        w.writerow(["2026-06-08T00:00:00+00:00", "ORDERED", "buy", "005930", "1",
                    "MKT", "0001", "0", "0", "", "supertrend"])
    assert _migrate_audit_csv_header(p) is True
    rows = list(_csv.reader(open(p, encoding="utf-8")))
    assert "filled_qty" in rows[0] and "avg_fill_price" in rows[0]
    assert len(rows[1]) == len(rows[0])          # 기존 row 패딩됨
    assert _migrate_audit_csv_header(p) is False  # 재실행 시 no-op
