"""단테 AI 스윙 전략 — AiSwingStrategy (수일 보유 스윙).

배경
----
단테 예측 종목(ai_trade 교집합 유니버스)을 원천으로 **수일 보유 스윙 매매**를 하는
전략이다. 진입 판정은 이미 검증된 `Swing38Strategy`(임펄스 → Fib 0.382 되돌림 →
반등)를 **그대로 상속**해 재사용하고, 청산 임계만 `AiSwingParams` 로 분리해
그리드 서치가 가능하게 했다 (원본 swing_38 은 exit_plan 안에 리터럴 하드코딩).

⚠️ 기본 비활성 (default-OFF, CLAUDE.md §2 S3/S4)
- 어떤 스캐너·데몬·시뮬 진입점에도 등록하지 않았다. 호출자가 `"ai_swing"` 을 명시로
  넘기지 않으면 기존 동작은 바이트 동일하다.
- 전략 활성은 사용자 판단이다 — 코드에서 임의로 켜지 않는다.

★단일 원천(single source of truth)★
- 청산 임계를 만드는 곳은 `build_exit_plan()` **하나뿐**이다. 라이브
  (`AiSwingStrategy.exit_plan`) 와 백테스트 시뮬 분기가 **둘 다** 이 함수만 호출한다.
  기존 swing_38 은 시뮬 경로가 전략 `exit_plan()` 을 호출하지 않아 시뮬↔라이브 청산
  정책이 어긋나 있었다 — 그 재발을 구조적으로 막는 것이 이 함수의 존재 이유다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from backend.core.strategy.round_figure import resolve_sl_pct
from backend.core.strategy.swing_38 import Swing38Params, Swing38Strategy
from backend.models.position import Position
from backend.models.signal import EntrySignal
from backend.models.strategy import (
    AnalysisContext,
    ExitPlan,
    StopLoss,
    TakeProfitTier,
)

STRATEGY_ID = "ai_swing_v1"

# ── SL env — swing_38 과 **분리**한다 ────────────────────────────────────────
# `BARRO_SWING38_SL_PCT` 를 재사용하면 swing_38 운영 튜닝(.env.local 로 -8 타이트닝 등)
# 이 ai_swing 청산까지 동시에 바꿔버린다. 두 전략은 유니버스(단테 교집합)와 시그널
# 품질이 달라 SL 튜닝 축도 분리돼야 하므로 전용 env 를 쓴다.
_SL_ENV = "BARRO_AI_SWING_SL_PCT"


def _sl_fraction_from_env(default: Decimal) -> Decimal:
    """`BARRO_AI_SWING_SL_PCT`(percent 문자열) → fraction. 미설정·무효 시 default.

    예: "-8.0" → Decimal("-0.08"). 운영 즉시 롤백용 kill-lever 이므로 env 가 있으면
    파라미터(`AiSwingParams.sl_pct`) 를 덮는다. 그리드 서치는 env 미설정 상태에서
    `sl_pct` 를 스윕한다.

    외부 입력이라 예외를 전량 흡수한다 (§2 S3) — 오타·빈값·양수가 라이브 청산을
    깨뜨리지 않게 default 로 폴백한다 (StopLoss.fixed_pct 는 lt=0 제약).
    """
    raw = os.environ.get(_SL_ENV)
    if raw is None or not str(raw).strip():
        return default
    try:
        pct = Decimal(str(raw).strip()) / Decimal("100")
    except (ArithmeticError, ValueError, TypeError):
        return default
    if pct >= 0:
        return default
    return pct


@dataclass
class AiSwingParams(Swing38Params):
    """AI 스윙 파라미터 — 진입은 Swing38Params 계승, 청산은 파라미터화.

    min_hold_days=3 / max_hold_days=20 은 Swing38Params 기본값을 그대로 계승한다.
    """

    # ── 진입: 완화 스타트 ──
    # 단테 교집합(유니버스 축소) + swing_38 이중 게이트(일봉 강제·ATR≥3%)가 겹치면
    # 발화가 0 이 될 수 있다. swing_38 시뮬 운영값(min_score 5.0)보다 느슨하게
    # 시작하고 shadow 실측 후 조인다.
    min_score: float = 3.0

    # ── 청산: swing_38 프로파일 계승 + 그리드 서치용 파라미터화 ──
    # 값 근거는 swing_38 Phase D2 (S6 SL×max_hold 2D + S7 진입필터 그리드) 결과 계승.
    sl_pct: Decimal = Decimal("-0.15")        # env BARRO_AI_SWING_SL_PCT 로 override
    tp1_pct: Decimal = Decimal("0.20")
    tp1_qty: Decimal = Decimal("0.5")
    tp2_pct: Decimal = Decimal("0.50")
    tp2_qty: Decimal = Decimal("0.5")
    be_pct: Decimal = Decimal("0.10")         # breakeven_trigger (+10% 도달 시 본전 잠금)


def build_exit_plan(
    entry_price: Decimal,
    p: AiSwingParams,
    *,
    symbol: str = "",
) -> ExitPlan:
    """★단일 원천★ ai_swing 청산 계획 — 라이브·백테스트 시뮬이 **둘 다** 이것만 호출한다.

    기존 swing_38 은 시뮬 경로가 전략 `exit_plan()` 을 호출하지 않아 시뮬↔라이브 청산
    정책이 어긋나 있었다. 그 재발을 구조적으로 막는 것이 이 함수의 존재 이유다.

    Args:
        entry_price: 진입가(평단). Decimal.
        p: AiSwingParams — TP/SL/BE·보유기간 임계.
        symbol: 라운드피겨 로그용 종목코드 (선택).

    Returns:
        ExitPlan — TP1/TP2 분할익절 + SL(라운드피겨 보정 경유) + BE + 보유기간 게이트.
        time_exit 은 두지 않는다 (multi-day 스윙 — 당일 강제청산 폐기).
    """
    entry = entry_price if isinstance(entry_price, Decimal) else Decimal(str(entry_price))
    # env 가 있으면 param 을 덮는다 (운영 kill-lever). 없으면 그리드 서치 대상 param.
    sl_base = _sl_fraction_from_env(p.sl_pct)
    return ExitPlan(
        take_profits=[
            TakeProfitTier(
                price=entry * (Decimal("1") + p.tp1_pct),
                qty_pct=p.tp1_qty,
                condition=f"AI스윙 TP1 +{p.tp1_pct * 100:.0f}%",
            ),
            TakeProfitTier(
                price=entry * (Decimal("1") + p.tp2_pct),
                qty_pct=p.tp2_qty,
                condition=f"AI스윙 TP2 +{p.tp2_pct * 100:.0f}%",
            ),
        ],
        stop_loss=StopLoss(fixed_pct=resolve_sl_pct(
            STRATEGY_ID, entry, sl_base, symbol=symbol)),
        breakeven_trigger=p.be_pct,
        min_hold_days=p.min_hold_days,
        max_hold_days=p.max_hold_days,
    )


class AiSwingStrategy(Swing38Strategy):
    """AI 스윙 — 단테 교집합 종목에 swing_38 진입 판정 + 파라미터화된 스윙 청산.

    진입 판정(`_detect_impulse`/`_fib_score`/`_bounce_score`)·2차 분할진입·사이징·
    헬스체크는 부모(Swing38Strategy)를 그대로 상속한다 (재구현하지 않는다).
    """

    STRATEGY_ID = "ai_swing_v1"

    def __init__(self, params: Optional[AiSwingParams] = None) -> None:
        self.params: AiSwingParams = params or AiSwingParams()

    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        """부모 진입 판정 결과의 `signal_type` 만 "ai_swing" 으로 교체.

        부모가 signal_type 을 `"swing_38"` 리터럴로 하드코딩(swing_38.py:190)하므로
        override 가 필요하다. `strategy_id` 는 `self.STRATEGY_ID` 참조라 자동 전파된다
        (= "ai_swing_v1").
        """
        signal = super()._analyze_v2(ctx)
        if signal is None:
            return None
        return signal.model_copy(update={"signal_type": "ai_swing"})

    def add_on_signal(self, position: Position, ctx: AnalysisContext,
                      base_candle_low: Optional[Decimal] = None) -> Optional[EntrySignal]:
        """2차 분할진입 — 판정은 부모 그대로, `signal_type` 만 "ai_swing" 으로 교체.

        부모가 여기서도 `"swing_38"` 리터럴을 쓴다(swing_38.py:380). 라벨을 맞추지
        않으면 strategy_id="ai_swing_v1" 인데 signal_type="swing_38" 인 신호가 나온다.
        """
        signal = super().add_on_signal(position, ctx, base_candle_low)
        if signal is None:
            return None
        return signal.model_copy(update={"signal_type": "ai_swing"})

    def exit_plan(
        self,
        position: Position,
        ctx: Optional[AnalysisContext] = None,
    ) -> ExitPlan:
        """★build_exit_plan() 단일 원천 위임★ — 여기서 임계를 다시 쓰지 않는다.

        ctx 는 부모(Swing38Strategy.exit_plan)와 동일하게 받되 미사용이므로 기본값
        None 을 허용한다 (백테스트 시뮬이 position 만 들고 호출하는 경로 대비).
        """
        return build_exit_plan(
            Decimal(str(position.avg_price)),
            self.params,
            symbol=getattr(position, "symbol", ""),
        )


__all__ = [
    "STRATEGY_ID",
    "AiSwingParams",
    "AiSwingStrategy",
    "build_exit_plan",
]
