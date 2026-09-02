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
from dataclasses import dataclass, replace
from datetime import time as dtime
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
    if not pct.is_finite() or not Decimal("-1") < pct < 0:
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

    # ── 청산: 2026-07-30 그리드 실측 최적 (사용자 결정으로 적용) ──
    # 초기값은 swing_38 Phase D2 계승(SL -15% / trail +20%·off 5%)이었으나, 일봉 랜덤
    # 유니버스 120종목 × 3 seed 그리드에서 아래 조합이 27/27 PASS · 3-seed 평균 +2.172%
    # (계승값 +0.309% 대비 약 7배)로 나와 사용자 승인 후 기본값을 교체했다.
    #   두 축 모두 단조: trail_off 3%>5%>8% (9/9 쌍), trail_start 10%>15%>20%.
    #   승률도 동시 개선(26~28% → 36~39.5%) — 좁은 손절↔낮은 승률 트레이드오프가
    #   트레일링 최적화와 함께 사라진다.
    # 실측 근거: docs/04-report/features/2026-07-30-ai-swing-p0.report.md §3-2·§3-4
    # ⚠️ 랜덤 유니버스(대조군) 기준이며 단테 교집합은 미검증 — shadow 실측 후 재검토.
    sl_pct: Decimal = Decimal("-0.05")        # env BARRO_AI_SWING_SL_PCT 로 override
    # TP/BE 는 그리드 미검증 → swing_38 계승값 유지
    tp1_pct: Decimal = Decimal("0.20")
    tp1_qty: Decimal = Decimal("0.5")
    tp2_pct: Decimal = Decimal("0.50")
    tp2_qty: Decimal = Decimal("0.5")
    be_pct: Decimal = Decimal("0.10")         # breakeven_trigger (+10% 도달 시 본전 잠금)
    # ── 추적 수익화(트레일링) — 사용자 요구의 핵심: "손절선만 깨지지 않으면 추적 수익화" ──
    # HoldingEvaluator 프로파일(trailing_start_pct 20 / trailing_offset_pct 5)과 **등가**로
    # ExitPlan.trail_stages 에 실어 ExitEngine 도 같은 트레일링을 쓰게 한다.
    #   peak 가 entry 대비 +trail_start_pct 도달 → SL 을 peak × (1 - trail_offset_pct) 로 올림.
    # ※ 이 배선이 없으면 ExitPlan.trail_sl_for_peak() 가 None 을 돌려주고(trail_stages 부재)
    #   ExitEngine 트레일링이 통째로 비활성 → 시뮬은 SL/TP/BE 만으로 돌아 라이브
    #   HoldingEvaluator(트레일링 있음)와 다시 어긋난다. 기존 전략들이 실제로 그 상태다
    #   (backend/core/strategy/ 전체에 trail_stages 설정 0건).
    trail_start_pct: Decimal = Decimal("0.10")
    trail_offset_pct: Decimal = Decimal("0.03")


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
        ExitPlan — TP1/TP2 분할익절 + SL(라운드피겨 보정 경유) + BE + **트레일링** +
        보유기간 게이트. time_exit 은 두지 않는다 (multi-day 스윙 — 당일 강제청산 폐기).
    """
    entry = entry_price if isinstance(entry_price, Decimal) else Decimal(str(entry_price))
    # env 가 있으면 param 을 덮는다 (운영 kill-lever). 없으면 그리드 서치 대상 param.
    sl_base = _sl_fraction_from_env(p.sl_pct)
    # 트레일링 — HoldingEvaluator 프로파일(start 20% / offset 5%)과 등가 1단계.
    #   trail_stages 형식은 [(high_pnl_pct, trail_pct), ...] DESC 이고
    #   trail_sl = peak × (1 + trail_pct) 이므로 offset 은 음수로 넣는다.
    #   trail_start_pct <= 0 이면 트레일링 비활성(None) — 그리드로 끌 수 있게 한다.
    trail_stages = (
        [(p.trail_start_pct, -abs(p.trail_offset_pct))]
        if p.trail_start_pct > 0 and p.trail_offset_pct > 0
        else None
    )
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
        trail_stages=trail_stages,
        min_hold_days=p.min_hold_days,
        max_hold_days=p.max_hold_days,
    )


# ── 라이브 진입 게이트 정렬 (2026-09-02) ────────────────────────────────────
# ★ 발견: 백테스트 시뮬(`IntradaySimulator` ai_swing 분기)은 `min_atr_pct=0.035` +
#   `entry_time_cutoff=14:00` 으로 돌리는데, 라이브는 `AiSwingStrategy()` 를 무인자로
#   만들어 Swing38Params 기본값(0.03 / 컷오프 없음)을 쓴다. 즉 **백테스트가 보고한
#   수익률은 라이브보다 엄격한 진입 조건에서 나온 값**이고, 라이브는 시뮬이 한 번도
#   모델링하지 않은 느슨한 진입까지 집어간다.
#
# 기본값은 **현재 라이브값 그대로**다 — env 미설정이면 동작이 바이트 동일하다(§2 S3).
# `.env.local` 에서 시뮬과 같은 값으로 올려야 백테스트 근거가 라이브에 그대로 적용된다.
_ENV_MIN_ATR = "BARRO_AI_SWING_MIN_ATR_PCT"      # 예: 3.5 (percent)
_ENV_CUTOFF = "BARRO_AI_SWING_ENTRY_CUTOFF"      # 예: 14:00 (HH:MM), 빈값=비활성
_ENV_MIN_SCORE = "BARRO_AI_SWING_MIN_SCORE"      # 예: 5.0


def _float_from_env(name: str, default: float, env: dict | None = None) -> float:
    """percent 문자열 → 소수. 오타·빈값·음수는 default 로 흡수한다(라이브 무영향, §2 S3)."""
    raw = (env if env is not None else os.environ).get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        v = float(str(raw).strip()) / 100.0
    except (TypeError, ValueError):
        return default
    return v if 0.0 < v < 1.0 else default


def _score_from_env(name: str, default: float, env: dict | None = None) -> float:
    """진입 점수 임계(0~10 스케일). 범위를 벗어나면 default."""
    raw = (env if env is not None else os.environ).get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return v if 0.0 <= v <= 10.0 else default


def _cutoff_from_env(name: str, default, env: dict | None = None):
    """`HH:MM` → datetime.time. 빈 문자열은 **명시적 비활성**(None) 으로 해석한다."""
    raw = (env if env is not None else os.environ).get(name)
    if raw is None:
        return default
    txt = str(raw).strip()
    if not txt:
        return None
    try:
        hh, mm = txt.split(":", 1)
        return dtime(int(hh), int(mm))
    except (TypeError, ValueError):
        return default


def live_params(base: Optional[AiSwingParams] = None,
                env: dict | None = None) -> AiSwingParams:
    """env 를 반영한 라이브 진입 파라미터. 미설정이면 base 를 그대로 돌려준다.

    백테스트는 `IntradaySimulator` 가 파라미터를 **명시로** 넣으므로 이 함수를 타지
    않는다 — 그리드 스윕이 env 에 오염되지 않는다.
    """
    p = base or AiSwingParams()
    return replace(
        p,
        min_atr_pct=_float_from_env(_ENV_MIN_ATR, p.min_atr_pct, env),
        min_score=_score_from_env(_ENV_MIN_SCORE, p.min_score, env),
        entry_time_cutoff=_cutoff_from_env(_ENV_CUTOFF, p.entry_time_cutoff, env),
    )


class AiSwingStrategy(Swing38Strategy):
    """AI 스윙 — 단테 교집합 종목에 swing_38 진입 판정 + 파라미터화된 스윙 청산.

    진입 판정(`_detect_impulse`/`_fib_score`/`_bounce_score`)·2차 분할진입·사이징·
    헬스체크는 부모(Swing38Strategy)를 그대로 상속한다 (재구현하지 않는다).
    """

    STRATEGY_ID = "ai_swing_v1"

    def __init__(self, params: Optional[AiSwingParams] = None) -> None:
        # 무인자 생성(=라이브)만 env 를 반영한다. 시뮬은 params 를 명시로 넣어 그대로 쓴다.
        self.params: AiSwingParams = params if params is not None else live_params()

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
