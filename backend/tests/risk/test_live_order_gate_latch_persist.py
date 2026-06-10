"""BAR-OPS-38 — LiveOrderGate latch 파일 영속 테스트.

6/10 발견: 데몬은 사이클마다 게이트 인스턴스를 재생성 → 인스턴스 메모리 latch
(BAR-OPS-35)가 사이클 간 증발. latch_state_path 영속으로 인스턴스 간 공유 검증.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth, KiwoomNativeToken
from backend.core.gateway.kiwoom_native_orders import KiwoomNativeOrderExecutor
from backend.core.risk.live_order_gate import (
    DailyLossLimitExceeded,
    GatePolicy,
    LiveOrderGate,
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


def _gate(tmp_path, state_path):
    exec_ = KiwoomNativeOrderExecutor(oauth=_oauth_mock(), dry_run=True)
    return LiveOrderGate(
        executor=exec_,
        audit_path=tmp_path / "audit.csv",
        policy=GatePolicy(
            daily_loss_limit_pct=Decimal("-3.0"),
            daily_loss_latch=True,
            latch_state_path=str(state_path),
            loss_metric_label="당일실현+보유평가/추정예탁자산",
        ),
    )


async def test_latch_shared_across_gate_instances(tmp_path):
    state = tmp_path / "daily_gate_state.json"

    # 게이트 A — 한도 도달로 latch 트립(+파일 영속)
    gate_a = _gate(tmp_path, state)
    with pytest.raises(DailyLossLimitExceeded) as e:
        await gate_a.place_buy(symbol="005930", qty=1,
                               daily_pnl_pct=Decimal("-3.5"))
    assert "당일실현+보유평가" in str(e.value)   # [BAR-OPS-38] 산식 라벨 명시

    # 게이트 B — 새 인스턴스(데몬 다음 사이클). 손익이 회복(0%)돼도 latch 가 차단.
    gate_b = _gate(tmp_path, state)
    with pytest.raises(DailyLossLimitExceeded) as e2:
        await gate_b.place_buy(symbol="005930", qty=1,
                               daily_pnl_pct=Decimal("0.0"))
    assert "latch" in str(e2.value)

    # 매도는 latch 와 무관하게 가능해야 한다 (손절 경로 보장)
    r = await gate_b.place_sell(symbol="005930", qty=1,
                                daily_pnl_pct=Decimal("0.0"))
    assert r.dry_run


async def test_no_latch_without_state_path_is_per_instance(tmp_path):
    """latch_state_path 미지정(기존 동작) — 인스턴스 간 비공유(회귀 가드)."""
    exec_ = KiwoomNativeOrderExecutor(oauth=_oauth_mock(), dry_run=True)

    def mk():
        return LiveOrderGate(
            executor=exec_, audit_path=tmp_path / "audit.csv",
            policy=GatePolicy(daily_loss_limit_pct=Decimal("-3.0"),
                              daily_loss_latch=True),
        )

    g1 = mk()
    with pytest.raises(DailyLossLimitExceeded):
        await g1.place_buy(symbol="005930", qty=1, daily_pnl_pct=Decimal("-3.5"))
    # 새 인스턴스 — 메모리 latch 미공유라 회복 시 통과(기존 BAR-OPS-35 동작 보존)
    g2 = mk()
    r = await g2.place_buy(symbol="005930", qty=1, daily_pnl_pct=Decimal("0.0"))
    assert r.dry_run
