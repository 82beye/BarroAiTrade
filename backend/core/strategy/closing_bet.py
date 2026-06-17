"""
종가베팅(종베) 전략 — ClosingBetStrategy.

더트레이딩 방법론의 시그니처 기법. 장 막판(15:00~15:20)에 당일 주도주를 종가 근처에
매수해 익일 아침(9~10시) +4~5% 슈팅에 매도하는 오버나잇 1박 단타.

설계 근거:
- docs/02-design/features/2026-06-17-thetrading-methodology-uplift.design.md §6
- docs/03-analysis/2026-06-17-thetrading-methodology-extract.md §4.1

⚠️ 구현 범위 (Increment 1 — 기본 비활성 스캐폴딩):
- 본 모듈은 **일봉 컨텍스트 + ctx.timestamp 진입창**으로 동작하는 자기완결 스캐폴드다.
  EOD 선정 게이트(신고가 돌파 장대양봉 + 진입 시간창)를 구현한다.
- 분봉 자금유입(오전/오후) 게이트·존(F/골드존) 진입가·거래대금 rank/시총 hard-cut은
  **기본 비활성(default-off) 옵션**으로 두고, 라이브 활성화(별도 HITL 단계)에서 분봉/선정
  컨텍스트를 주입해 켠다. (intraday·선정 메타가 필요해 일봉만으로는 근사 불가.)
- SignalScanner 에는 closing_bet=False 로 등록되어 **라이브 동작에 영향 없음**.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from typing import Any, List, Optional

from backend.core.strategy.base import Strategy
from backend.core.strategy.round_figure import resolve_sl_pct
from backend.models.market import MarketType, OHLCV
from backend.models.position import Position
from backend.models.signal import EntrySignal
from backend.models.strategy import (
    Account,
    AnalysisContext,
    ExitPlan,
    StopLoss,
    TakeProfitTier,
)

_KST = timezone(timedelta(hours=9))


@dataclass
class ClosingBetParams:
    """종가베팅 파라미터.

    시간/존/유입 관련 기본값은 방법론(부록 §4.1) 기준. intraday·선정 의존 게이트는
    require_* 토글로 기본 비활성(일봉 스캐폴드 회귀 보존).
    """

    # ── 진입 시간창 (KST) ──
    entry_window_start: dtime = dtime(15, 0)
    entry_window_end: dtime = dtime(15, 20)
    require_eod_window: bool = True       # ctx.timestamp(KST)가 창 밖이면 진입 거부

    # ── 신고가 돌파 (일봉) ──
    require_new_high: bool = True
    new_high_lookback: int = 60           # 직전 N봉 고점 대비 돌파 판정
    new_high_tolerance: float = 0.0       # 직전 고점 대비 허용 미달폭(0=완전 돌파)

    # ── 기준봉 = 신고가 돌파 장대양봉 (당일 일봉) ──
    base_min_gain_pct: float = 0.05       # 몸통 최소 상승률 5%
    base_upper_wick_max: float = 1.0      # 윗꼬리/몸통 상한 (1.0=윗꼬리 양봉 허용)

    # ── 변동성 필터 (기존 전략 패턴 계승) ──
    min_atr_pct: float = 0.0              # default 0 = 비활성 (회귀 보존)
    atr_n: int = 14
    min_candles: int = 70                 # new_high_lookback + 여유

    # ── 익절 / 청산 ──
    tp_shoot_pct: float = 0.045           # 익일 슈팅 익절 +4.5%
    tp_shoot_pct_largecap: float = 0.02   # 대형주 +2%
    largecap_market_cap: float = 5.0e12   # 대형주 판정 시총 (5조)
    morning_exit_time: dtime = dtime(10, 0)   # 익일 10:00 시간청산

    # ── 보유 한도 ──
    max_hold_days: int = 3                # D1~D3
    stop_loss_pct: float = -0.03          # 보조 고정 SL (0.618 이탈과 병행 2차망)

    # ── intraday/선정 의존 게이트 (기본 비활성 — 활성화는 별도 HITL 단계) ──
    require_zone: bool = False            # 존(골드존 0.5~0.618) 진입가 — intraday 필요
    gold_fib_low: float = 0.5
    gold_fib_high: float = 0.618
    require_money_flow: bool = False      # 분봉 오전/오후 자금유입 — intraday 필요
    flow_block_am_only: bool = True
    max_trade_value_rank: int = 5         # 거래대금 1~5위 (선정 메타 있을 때만)
    min_trade_value: float = 3.0e10       # 300억 (선정 메타 있을 때만)
    require_leader_meta: bool = False     # ctx.theme_context 선정 메타 hard-cut 강제

    # ── 토글 (라이브 활성 단계용 placeholder) ──
    enable_ipo_mode: bool = False         # 신규주 종베 (상장 1~2일)
    enable_down_mode: bool = False        # 음봉 종베 (하락장)


class ClosingBetStrategy(Strategy):
    """종가베팅 — 15:00~15:20 신고가 돌파 장대양봉 주도주 종가 진입(오버나잇)."""

    STRATEGY_ID = "closing_bet_v1"

    def __init__(self, params: Optional[ClosingBetParams] = None) -> None:
        self.params = params or ClosingBetParams()

    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        p = self.params
        candles = ctx.candles
        if len(candles) < p.min_candles:
            return None

        # ① 진입 시간창 게이트 (KST) — 종베의 핵심 차별점.
        if p.require_eod_window and not self._in_entry_window(ctx.timestamp):
            return None

        # ② 변동성 필터 (옵션, 기존 패턴 계승)
        if p.min_atr_pct > 0 and self._atr_pct(candles, n=p.atr_n) < p.min_atr_pct:
            return None

        today = candles[-1]

        # ③ 신고가 돌파 (일봉) — 직전 lookback 봉 고점 초과
        if p.require_new_high:
            prior = candles[-(p.new_high_lookback + 1):-1]
            if not prior:
                return None
            prior_high = max(c.high for c in prior)
            if today.high < prior_high * (1.0 - p.new_high_tolerance):
                return None

        # ④ 기준봉 = 장대양봉 (몸통 ≥5%, 윗꼬리 제한)
        body = (today.close - today.open) / today.open if today.open > 0 else 0.0
        if body < p.base_min_gain_pct:
            return None
        body_abs = today.close - today.open
        if body_abs > 0:
            upper_wick_ratio = (today.high - today.close) / body_abs
            if upper_wick_ratio > p.base_upper_wick_max:
                return None

        cur = float(today.close)

        # ⑤ (옵션, 기본 off) 거래대금 rank/시총 선정 메타 hard-cut — ctx.theme_context 주입 시.
        leader = self._leader_meta(ctx)
        if p.require_leader_meta:
            if leader is None:
                return None
            rank = leader.get("rank_trade_value")
            tval = leader.get("trade_value")
            if rank is None or rank > p.max_trade_value_rank:
                return None
            if tval is not None and tval < p.min_trade_value:
                return None

        # ⑥ (옵션, 기본 off) 존(골드존) 진입가 — 당일 캔들 되돌림 기준(intraday 활성 시 정밀화).
        if p.require_zone:
            rng = today.high - today.low
            if rng <= 0:
                return None
            retrace = (today.high - cur) / rng
            if not (p.gold_fib_low <= retrace <= p.gold_fib_high):
                return None

        # ⑦ (옵션, 기본 off) 분봉 자금유입 — intraday 활성 시 주입.
        flow_grade = self._money_flow_grade(ctx) if p.require_money_flow else "N/A"
        if p.require_money_flow and flow_grade == "BLOCK":
            return None

        # ── 스코어 + 신호 ──
        new_high_margin = 0.0
        if p.require_new_high and prior:
            new_high_margin = max(0.0, (today.high - prior_high) / prior_high)
        score = self._score(body, new_high_margin, flow_grade)

        is_largecap = bool(leader and leader.get("market_cap", 0) >= p.largecap_market_cap)
        stop_fib_price = float(today.high - (today.high - today.low) * 0.618)

        return EntrySignal(
            symbol=ctx.symbol,
            name=ctx.name or ctx.symbol,
            price=cur,
            signal_type="closing_bet",
            score=round(float(score), 2),
            reason=(
                f"종베: 신고가돌파 장대양봉 +{body * 100:.1f}% "
                f"(돌파마진 {new_high_margin * 100:.1f}%, flow={flow_grade})"
            ),
            market_type=ctx.market_type,
            strategy_id=self.STRATEGY_ID,
            timestamp=datetime.now(timezone.utc),
            metadata={
                "overnight": True,
                "base_high": float(today.high),
                "base_low": float(today.low),
                "stop_fib_price": stop_fib_price,
                "is_largecap": is_largecap,
                "flow_grade": flow_grade,
                "max_hold_days": p.max_hold_days,
            },
        )

    # === 헬퍼 ===

    def _in_entry_window(self, ts: datetime) -> bool:
        """ctx.timestamp(KST)가 [entry_window_start, entry_window_end] 안인지."""
        p = self.params
        if ts.tzinfo is not None:
            ts = ts.astimezone(_KST)
        t = ts.time()
        return p.entry_window_start <= t <= p.entry_window_end

    def _leader_meta(self, ctx: AnalysisContext) -> Optional[dict]:
        """선정 메타(거래대금 rank/시총)를 ctx.theme_context 에서 추출 (dict 인 경우만)."""
        tc = getattr(ctx, "theme_context", None)
        return tc if isinstance(tc, dict) else None

    def _money_flow_grade(self, ctx: AnalysisContext) -> str:
        """분봉 오전/오후 자금유입 등급. intraday 데이터 부재 시 'N/A'(통과).

        활성화(별도 HITL)에서 분봉 캔들/유입 메타를 주입해 BOTH/PM_ONLY/BLOCK 판정.
        """
        tc = self._leader_meta(ctx)
        if not tc or "am_inflow" not in tc:
            return "N/A"
        am = bool(tc.get("am_inflow"))
        pm = bool(tc.get("pm_inflow"))
        if self.params.flow_block_am_only and am and not pm:
            return "BLOCK"          # 오전유입후 死 = 금지
        if not pm:
            return "BLOCK"
        return "BOTH" if (am and pm) else "PM_ONLY"

    def _score(self, body: float, new_high_margin: float, flow_grade: str) -> float:
        """0~10 스케일 스코어 — 기준봉 강도 + 돌파마진 + 유입 등급."""
        s = min(5.0, body / 0.05 * 2.5)            # 몸통 5%→2.5, 10%→5.0 cap
        s += min(3.0, new_high_margin / 0.03 * 1.5)  # 돌파마진 3%→1.5 cap 3.0
        s += {"BOTH": 2.0, "PM_ONLY": 1.0}.get(flow_grade, 0.5)
        return min(10.0, s)

    @staticmethod
    def _atr_pct(candles: List[OHLCV], n: int = 14) -> float:
        from backend.core.strategy.indicators import atr_pct
        return atr_pct(candles, n=n)

    # === Strategy v2 override ===

    def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan:
        """종베 청산: 익일 슈팅 익절(+4.5%/대형주 +2%), 0.618 이탈 SL, 익일 10:00 시간청산, D3 한도.

        ⚠️ 오버나잇 의미론: time_exit=morning_exit_time 은 *익일* 아침이다. ExitEngine 이
        time_exit 을 '당일 그 시각'으로 해석하면 진입일 당일 즉시청산 버그가 난다 →
        라이브 활성 단계에서 overnight 플래그로 진입일 당일 time_exit 평가를 skip 해야 한다
        (limit_up_chase _maybe_gap_partial 의 당일 제외 패턴). 본 Increment 에서는 전략이
        비활성이라 latent.
        """
        p = self.params
        avg = Decimal(str(position.avg_price))
        is_large = bool(getattr(position, "metadata", {}) and
                        position.metadata.get("is_largecap", False))
        tp_pct = Decimal(str(p.tp_shoot_pct_largecap if is_large else p.tp_shoot_pct))

        # 0.618 이탈 가격 기반 SL (메타 있으면) — 보조 고정 SL 과 더 보수적인 쪽.
        sl_pct = Decimal(str(p.stop_loss_pct))
        meta = getattr(position, "metadata", {}) or {}
        fib_stop = meta.get("stop_fib_price")
        if fib_stop and float(avg) > 0:
            fib_pct = (Decimal(str(fib_stop)) - avg) / avg
            sl_pct = max(sl_pct, fib_pct)   # 둘 중 덜 깊은(더 가까운) 쪽 = 더 보수적 청산

        return ExitPlan(
            take_profits=[
                TakeProfitTier(
                    price=avg * (Decimal("1") + tp_pct * Decimal("0.6")),
                    qty_pct=Decimal("0.5"),
                    condition="종베 1차 저항 분할익절",
                ),
                TakeProfitTier(
                    price=avg * (Decimal("1") + tp_pct),
                    qty_pct=Decimal("0.5"),
                    condition=f"종베 슈팅 +{float(tp_pct) * 100:.0f}%",
                ),
            ],
            stop_loss=StopLoss(fixed_pct=resolve_sl_pct(
                self.STRATEGY_ID, avg, sl_pct,
                symbol=getattr(position, "symbol", ""))),
            time_exit=p.morning_exit_time if ctx.market_type == MarketType.STOCK else None,
            breakeven_trigger=Decimal("0.02"),
            min_hold_days=None,                 # 익일 아침 즉시 청산 허용
            max_hold_days=p.max_hold_days,      # D3 강제 TIME_EXIT
        )

    def position_size(self, signal: EntrySignal, account: Account) -> Decimal:
        from backend.core.strategy.position_sizing import even_position_size
        return even_position_size(signal, account)

    def health_check(self) -> dict[str, Any]:
        p = self.params
        return {
            "strategy_id": self.STRATEGY_ID,
            "ready": p.min_candles >= p.new_high_lookback and p.max_hold_days >= 1,
            "entry_window": f"{p.entry_window_start}~{p.entry_window_end}",
            "max_hold_days": p.max_hold_days,
        }


__all__ = ["ClosingBetStrategy", "ClosingBetParams"]
