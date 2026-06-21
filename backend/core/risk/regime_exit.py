"""국면/ATR 인지 적응형 청산 — config-gated, **default-OFF**.

라이브 청산(`HoldingEvaluator`)의 고정% 정책(`STRATEGY_EXIT_PROFILES`)을 시장 국면에
따라 조정하는 후처리 훅. `enabled=False` 또는 `regime is None` 이면 입력 policy 를
**그대로 반환(동일 객체)** → 라이브 byte-identical. `enabled=True` 라도 모든 배수
default 1.0 이라 항등 → 이중 안전판.

근거: `market_regime.classify_regime` 은 이미 라이브(`intraday_buy_daemon.py:871`)에서
산출되어 `refined_signals.json` 에 저장되나 **청산엔 미반영**(전략 "선택" 가중치로만 사용).
6월 변동성장(SIDEWAYS)에서 고정 -4% SL 이 너무 너그러워 트랩 손실을 키움 → SIDEWAYS
SL 타이트화·보유 단축, BULL TP 확장 후보(측정 후 활성화).

순환 import 회피: `ExitPolicy` 는 런타임 import 하지 않고(`dataclasses.replace` + duck typing)
TYPE_CHECKING 에서만 참조. holding_evaluator → regime_exit → market_regime 단방향.

활성화는 측정 후 (d) HITL — 본 모듈은 기능만 제공하며 default 로는 라이브 무변경.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from backend.core.backtester.market_regime import MarketRegime

if TYPE_CHECKING:  # 런타임 미import (순환 회피)
    from backend.core.risk.holding_evaluator import ExitPolicy

_ONE = Decimal("1.0")


def adjust_policy_for_regime(
    policy: "ExitPolicy",
    regime: Optional[MarketRegime],
    atr_pct: float = 0.0,
    *,
    enabled: bool = False,
    sideways_sl_mult: float = 1.0,
    sideways_tp_mult: float = 1.0,
    sideways_max_hold_days: Optional[int] = None,
    bull_tp_mult: float = 1.0,
    bull_sl_mult: float = 1.0,
    bearish_sl_mult: float = 1.0,
) -> "ExitPolicy":
    """국면별 ExitPolicy 조정. default-OFF parity 보장.

    - `enabled=False` OR `regime is None` → policy 그대로 반환(동일 객체).
    - SIDEWAYS: SL×sideways_sl_mult(mult<1 → -4%→-3% 타이트), TP×sideways_tp_mult,
      max_hold_days override(보유 단축, 6월 변동성장 회전 축소).
    - BULL: TP×bull_tp_mult(확장), SL×bull_sl_mult.
    - BEARISH: SL×bearish_sl_mult.
    모든 배수 default 1.0 + max_hold override None → 항등(동일 객체 반환).

    atr_pct 는 향후 변동성 비례 조정용 입력(현재는 국면 기반만; 미사용 시 동작 무영향).
    """
    if not enabled or regime is None:
        return policy

    sl_mult = _ONE
    tp_mult = _ONE
    new_max_hold = policy.max_hold_days

    if regime == MarketRegime.SIDEWAYS:
        sl_mult = Decimal(str(sideways_sl_mult))
        tp_mult = Decimal(str(sideways_tp_mult))
        if sideways_max_hold_days is not None:
            new_max_hold = sideways_max_hold_days
    elif regime == MarketRegime.BULL:
        sl_mult = Decimal(str(bull_sl_mult))
        tp_mult = Decimal(str(bull_tp_mult))
    elif regime == MarketRegime.BEARISH:
        sl_mult = Decimal(str(bearish_sl_mult))

    # 항등 빠른 경로 — 모든 배수 1.0 + max_hold 불변 → 동일 객체(byte-identical).
    if sl_mult == _ONE and tp_mult == _ONE and new_max_hold == policy.max_hold_days:
        return policy

    return replace(
        policy,
        stop_loss_pct=policy.stop_loss_pct * sl_mult,
        take_profit_pct=policy.take_profit_pct * tp_mult,
        tightened_sl_pct=policy.tightened_sl_pct * sl_mult,
        max_hold_days=new_max_hold,
    )


@dataclass(frozen=True)
class RegimeExitConfig:
    """국면 적응 청산 설정 — PolicyConfig/호출자가 조립해 PositionContext 로 전달.

    default(enabled=False) → `apply` 가 입력 policy 그대로 반환(byte-identical).
    """

    enabled: bool = False
    sideways_sl_mult: float = 1.0
    sideways_tp_mult: float = 1.0
    sideways_max_hold_days: Optional[int] = None
    bull_tp_mult: float = 1.0
    bull_sl_mult: float = 1.0
    bearish_sl_mult: float = 1.0

    def apply(
        self, policy: "ExitPolicy", regime: Optional[MarketRegime], atr_pct: float = 0.0,
    ) -> "ExitPolicy":
        return adjust_policy_for_regime(
            policy, regime, atr_pct,
            enabled=self.enabled,
            sideways_sl_mult=self.sideways_sl_mult,
            sideways_tp_mult=self.sideways_tp_mult,
            sideways_max_hold_days=self.sideways_max_hold_days,
            bull_tp_mult=self.bull_tp_mult,
            bull_sl_mult=self.bull_sl_mult,
            bearish_sl_mult=self.bearish_sl_mult,
        )

    @classmethod
    def from_policy_config(cls, cfg) -> "RegimeExitConfig":
        """PolicyConfig(또는 동등 객체)에서 조립. 필드 부재 시 default(무조정)."""
        return cls(
            enabled=bool(getattr(cfg, "regime_exit_enabled", False)),
            sideways_sl_mult=float(getattr(cfg, "regime_sideways_sl_mult", 1.0)),
            sideways_tp_mult=float(getattr(cfg, "regime_sideways_tp_mult", 1.0)),
            sideways_max_hold_days=getattr(cfg, "regime_sideways_max_hold_days", None),
            bull_tp_mult=float(getattr(cfg, "regime_bull_tp_mult", 1.0)),
            bull_sl_mult=float(getattr(cfg, "regime_bull_sl_mult", 1.0)),
            bearish_sl_mult=float(getattr(cfg, "regime_bearish_sl_mult", 1.0)),
        )


__all__ = ["adjust_policy_for_regime", "RegimeExitConfig"]
