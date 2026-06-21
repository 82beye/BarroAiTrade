"""
38스윙 전략 (Swing-38 Strategy) — 임펄스 후 Fib 0.382 되돌림 매수.

진입 조건 (3 단계 순차):
  1. 임펄스 탐지: 최근 lookback 봉 내 gain ≥ 5% + 거래량 평균 2x 이상 양봉
  2. 0.382 되돌림: 임펄스 고점-저점 기준 retrace ≈ 0.382 (±7.5%)
  3. 반등 확인: 직전 양봉 (close > open)

BAR-49: 신규 포팅. F존 (-2~-5% 눌림) 보다 깊은 되돌림 (~-30%) 노리는 스윙 매매.

Reference:
- Plan: docs/01-plan/features/bar-49-swing-38.plan.md
- Design: docs/02-design/features/bar-49-swing-38.design.md
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone
from decimal import Decimal
from typing import Any, List, Optional

import pandas as pd

from backend.core.strategy.base import Strategy
from backend.core.strategy.round_figure import resolve_sl_pct
from backend.core.strategy.trap_guard import TrapGuardConfig, evaluate_trap_guard
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


@dataclass
class Swing38Params:
    """38스윙 파라미터."""

    impulse_lookback: int = 30
    impulse_min_gain_pct: float = 0.05
    impulse_volume_ratio: float = 2.0
    fib_target: float = 0.382
    fib_tolerance: float = 0.075
    bounce_lookback: int = 5
    min_candles: int = 60

    # BAR-OPS-09 Phase 6 — 변동성 필터: ATR% < min_atr_pct 시 진입 거부.
    # Phase D2 (2026-05-28): default 0.0 → 0.03 활성 (S7 시뮬 결과).
    # S7 진입 필터 시뮬 11 시나리오 중 ATR≥3% 단독 추가가 자본가중 +1.857% (baseline +1.808%,
    # +2.71% 우위) 로 가장 cost-effective. 진입 수 6,397 → 6,095 (-4.7%, 저변동주만 차단).
    # 패턴: 5/15 LG씨엔에스 -514k (flu% 7.5%), 5/14 삼성전자 -80k (flu% 4.2%) 등
    min_atr_pct: float = 0.03
    atr_n: int = 14

    # BAR-OPS-09 Phase 8 — 진입 점수 임계 (impulse*0.4 + fib*0.4 + bounce*0.2 < min_score 차단).
    # BAR-175 score 0-10 스케일 정규화 후 — default 3.0 (기존 0.3 × 10).
    # IntradaySimulator 시뮬 진입점에서 5.0 override (기존 0.5 × 10).
    # 패턴: 5/22 LG전자 -148k / 삼성전기 -124k 같은 약한 swing 시그널(w=0.3 BEARISH) 진입 차단.
    # 운영 차단은 별도 — scripts/intraday_buy_daemon.py 의 regime_weights() 임계 추가 필요 (별도 BAR).
    min_score: float = 3.0

    # BAR-OPS-09 Phase 8c — 진입 시간 게이트: 마지막 candle.time() >= entry_time_cutoff 시 차단.
    # default None (비활성) — 기존 회귀 보존. IntradaySimulator 시뮬 진입점에서 dtime(14, 0) override.
    # 패턴: 5/22 swing_38 LG전자 13:48 -148k, 삼성전기 14:40 -124k (장 후반 진입 → 청산 여유 부족 손실).
    # 운영 분봉 candle 기준 작동. 일봉 시뮬은 .time()=00:00 으로 항상 통과 (영향 미미).
    entry_time_cutoff: Optional[dtime] = None

    # BAR-OPS-09 Phase C (2026-05-27) — 일봉 스캔 강제 + 보유 기간 게이트:
    # - require_daily_candles: True 시 candles timestamp 간격이 24h(일봉) 가 아니면 진입 거부.
    #   swing_38 은 multi-day 스윙 전략 → 분봉/5분봉 노이즈 제거.
    # - min_hold_days: 진입 후 N일 미만 시 청산 평가 차단 (단기 노이즈 SL/TP 발동 방지).
    # - max_hold_days: N일 도달 시 강제 TIME_EXIT (장기 보유 위험 차단).
    # 기존 운영 swing_38 패턴 (당일 청산) 와 다른 새 정책. exit_plan() 에서 ExitPlan 으로 전달.
    require_daily_candles: bool = True
    min_hold_days: int = 3
    # Phase D2 (2026-05-28): 8 → 20 (S6 결합 그리드 결과).
    # S6 SL × max_hold 2D 그리드에서 SL=-15% × D+20 = 자본가중 +1.808% (baseline SL=-10%×D+8
    # +0.597% 대비 +203%). 단일 변수 그리드 max_hold=20도 +1.096% (베이스 +84%) 우위.
    max_hold_days: int = 20

    # BAR-OPS-09 Phase D (2026-05-27) — 분할 진입 (1차/2차 scale-in) + 큰 폭 TP/SL:
    # 사용자 요구: "일별 매수 1번, 다음날 추적 후 기준봉 지지하면 2차 매수".
    # exit_plan() 에서 TP1=+20% / TP2=+50% / SL=-10% / breakeven=+10% 적용 (Phase C 5/10/3 → D 20/50/10).
    # add_on_signal(position, ctx, base_candle_low) 가 다음 신호 발행:
    #  - 1차 진입 후 second_entry_min_days <= 경과일 < second_entry_max_days
    #  - 현재가 >= 기준봉 low * (1 - second_entry_support_tolerance)
    #  → entry_round=2 metadata 와 함께 EntrySignal 반환.
    # caller(orchestrator/scanner) 가 동일 종목 당일 2차 중복 진입 차단 (entry_round=2 이미 실행).
    second_entry_enabled: bool = True
    second_entry_min_days: int = 1     # 1차 진입 D, D+1 부터 평가 (당일 추가 진입 차단)
    second_entry_max_days: int = 5     # D+5 까지만 (그 후 미실행 폐기 — 추세 변경 위험)
    second_entry_size_ratio: float = 0.5  # 1차 진입 수량 × 0.5 (신호 metadata, 운영이 적용)
    second_entry_support_tolerance: float = 0.005  # 기준봉 low * (1 - 0.005) 까지 지지 인정

    # 6월 트랩(가짜 상승/개미 꼬시기) 방어 가드 — config-gated, **default-OFF**.
    # 모든 임계 0 → 기존 진입 경로 byte-identical. 설계: backend/core/strategy/trap_guard.py.
    trap_over_ext_k_atr: float = 0.0
    trap_over_ext_baseline: str = "ma"
    trap_over_ext_ma_period: int = 20
    trap_upper_wick_max: float = 0.0
    trap_gap_atr_mult: float = 0.0
    trap_gap_abs_max_pct: float = 0.0

    def _trap_guard_config(self) -> TrapGuardConfig:
        return TrapGuardConfig(
            over_ext_k_atr=self.trap_over_ext_k_atr,
            over_ext_baseline=self.trap_over_ext_baseline,
            over_ext_ma_period=self.trap_over_ext_ma_period,
            upper_wick_max=self.trap_upper_wick_max,
            gap_atr_mult=self.trap_gap_atr_mult,
            gap_abs_max_pct=self.trap_gap_abs_max_pct,
            atr_n=self.atr_n,
        )


class Swing38Strategy(Strategy):
    """38스윙 — 임펄스 + Fib 0.382 되돌림 + 반등."""

    STRATEGY_ID = "swing_38_v1"

    def __init__(self, params: Optional[Swing38Params] = None) -> None:
        self.params = params or Swing38Params()

    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        p = self.params
        if len(ctx.candles) < p.min_candles:
            return None

        # BAR-OPS-09 Phase C: 일봉 스캔 강제 (분봉/5분봉 노이즈 제거)
        if p.require_daily_candles and len(ctx.candles) >= 2:
            ts1 = ctx.candles[-2].timestamp
            ts2 = ctx.candles[-1].timestamp
            interval_hours = (ts2 - ts1).total_seconds() / 3600
            if interval_hours < 12:  # 12h 미만 = 분봉/5분봉/시간봉 → 거부
                return None

        # BAR-OPS-09 Phase 6: 변동성 필터 — ATR% < min_atr_pct 시 진입 거부 (저변동·고가주 가짜 시그널 방지)
        if p.min_atr_pct > 0:
            atr_pct = self._atr_pct(ctx.candles, n=p.atr_n)
            if atr_pct < p.min_atr_pct:
                return None

        # 6월 트랩(가짜 상승/개미 꼬시기) 방어 가드 (default-OFF) — 모든 임계 0 이면 no-op.
        _trap_cfg = p._trap_guard_config()
        if _trap_cfg.any_enabled():
            _blocked, _reason = evaluate_trap_guard(ctx.candles, _trap_cfg)
            if _blocked:
                return None

        # BAR-OPS-09 Phase 8c: 진입 시간 게이트 — 장 후반 진입 차단 (청산 여유 부족 손실 방지).
        # 일봉 시뮬은 .time()=00:00 으로 항상 통과 (영향 미미).
        if p.entry_time_cutoff is not None:
            last_ts = ctx.candles[-1].timestamp
            if last_ts.time() >= p.entry_time_cutoff:
                return None

        df = self._to_dataframe(ctx.candles)

        # 1. 임펄스 탐지
        impulse = self._detect_impulse(df)
        if impulse is None:
            return None

        # 2. Fib 0.382 되돌림 검증
        fib_score = self._fib_score(df, impulse)
        if fib_score == 0:
            return None

        # 3. 반등 확인
        bounce_score = self._bounce_score(df)
        if bounce_score == 0:
            return None

        impulse_score = min(1.0, impulse["gain_pct"] / 0.10)  # 5%~10% 정규화
        # BAR-175 score 0-10 스케일 정규화 + BAR-OPS-09 Phase 8 min_score 파라미터화
        raw = impulse_score * 0.4 + fib_score * 0.4 + bounce_score * 0.2
        score = raw * 10.0
        if score < p.min_score:
            return None

        return EntrySignal(
            symbol=ctx.symbol,
            name=ctx.name or ctx.symbol,
            price=float(df["close"].iloc[-1]),
            signal_type="swing_38",
            score=round(float(score), 2),
            reason=(
                f"38스윙: 임펄스 {impulse['gain_pct']*100:.1f}% + "
                f"Fib0.382({fib_score:.2f}) + 반등({bounce_score:.2f})"
            ),
            market_type=ctx.market_type,
            strategy_id=self.STRATEGY_ID,
            timestamp=datetime.now(timezone.utc),
            metadata={
                "swing_38_subtype": "swing_38",
                "impulse_gain_pct": round(impulse["gain_pct"], 4),
                "fib_score": round(float(fib_score), 3),
                "bounce_score": round(float(bounce_score), 3),
            },
        )

    # === 3 단계 helper ===

    def _detect_impulse(self, df: pd.DataFrame) -> Optional[dict]:
        """최근 lookback 봉 내 +gain≥5% + volume≥2x avg 양봉 탐색."""
        p = self.params
        avg_volume = df["volume"].mean()
        if avg_volume == 0:
            return None
        recent = df.tail(p.impulse_lookback)
        for i in range(len(recent) - 1, -1, -1):
            row = recent.iloc[i]
            if row["close"] <= row["open"]:
                continue
            gain = (row["close"] - row["open"]) / row["open"]
            if gain < p.impulse_min_gain_pct:
                continue
            if row["volume"] < p.impulse_volume_ratio * avg_volume:
                continue
            return {
                "high": float(row["high"]),
                "low": float(row["low"]),
                "open": float(row["open"]),
                "close": float(row["close"]),
                "gain_pct": float(gain),
            }
        return None

    def _fib_score(self, df: pd.DataFrame, impulse: dict) -> float:
        """임펄스 고점-저점 기준 0.382 ± tolerance zone → [0, 1]."""
        p = self.params
        high, low = impulse["high"], impulse["low"]
        if high == low:
            return 0.0
        close = float(df["close"].iloc[-1])
        retrace = (high - close) / (high - low)
        distance = abs(retrace - p.fib_target)
        if distance > p.fib_tolerance:
            return 0.0
        return float(1.0 - distance / p.fib_tolerance)

    def _bounce_score(self, df: pd.DataFrame) -> float:
        """직전 봉 양봉 + 마감 강도 → [0, 1]."""
        p = self.params
        recent = df.tail(p.bounce_lookback)
        last = recent.iloc[-1]
        if last["close"] <= last["open"]:
            return 0.0
        body = (last["close"] - last["open"]) / last["open"]
        return float(min(1.0, body / 0.02))  # +2% 양봉 → 1.0

    @staticmethod
    def _to_dataframe(candles: List[OHLCV]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "open": [c.open for c in candles],
                "high": [c.high for c in candles],
                "low": [c.low for c in candles],
                "close": [c.close for c in candles],
                "volume": [c.volume for c in candles],
            }
        )

    # === Strategy v2 override ===

    def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan:
        """BAR-OPS-09 Phase D2 (2026-05-28): 38스윙 그리드 서치 결합 최적 반영.

        S6 SL×max_hold 2D + S7 진입 필터 그리드 결과:
        - TP1=+20% (50%) ← Phase D 유지 (TP1 그리드 결과 자본가중 최대)
        - TP2=+50% (50%) ← Phase D 유지 (TP2 그리드 거의 무영향)
        - SL=-15% ← Phase D -10% 에서 강화 (SL×D+20 결합 최적, 큰 손실 흡수 후 회복)
        - breakeven_trigger=+10% ← Phase D 유지 (보수적 BE 잠금)
        - min_hold_days=3, max_hold_days=20 ← Phase D 8 에서 확대 (회복 시간 확보)
        - time_exit 제거 (Phase D 유지)
        시뮬 자본가중 +1.808% (baseline Phase D +0.597% 대비 +203%).
        Swing38Params.min_atr_pct=0.03 활성으로 추가 +2.71% (ATR≥3% 진입 필터).
        """
        p = self.params
        avg = Decimal(str(position.avg_price))
        return ExitPlan(
            take_profits=[
                TakeProfitTier(
                    price=avg * Decimal("1.20"),
                    qty_pct=Decimal("0.5"),
                    condition="38스윙 TP1 +20%",
                ),
                TakeProfitTier(
                    price=avg * Decimal("1.50"),
                    qty_pct=Decimal("0.5"),
                    condition="38스윙 TP2 +50%",
                ),
            ],
            stop_loss=StopLoss(fixed_pct=resolve_sl_pct(
                self.STRATEGY_ID, avg, Decimal("-0.15"),
                symbol=getattr(position, "symbol", ""))),
            breakeven_trigger=Decimal("0.10"),
            min_hold_days=p.min_hold_days,
            max_hold_days=p.max_hold_days,
        )

    def add_on_signal(
        self,
        position: Position,
        ctx: AnalysisContext,
        base_candle_low: Optional[Decimal] = None,
    ) -> Optional[EntrySignal]:
        """BAR-OPS-09 Phase D (2026-05-27) — 38스윙 2차 분할 진입 시그널.

        사용자 요구: "일별 매수는 1번, 다음날 추적 후 기준봉 지지하면 추가 2차 매수".

        조건 (AND):
          1. params.second_entry_enabled
          2. params.require_daily_candles=True 면 ctx.candles 일봉 간격 검증
          3. second_entry_min_days <= 경과일 <= second_entry_max_days
             - 일별 1회 제약: caller 가 entry_round=2 이미 실행한 종목을 dispatch 제외
          4. 현재가 >= 기준봉 low * (1 - second_entry_support_tolerance)
             - base_candle_low: 1차 진입일 일봉의 low. caller(orchestrator) 가
               position.metadata['base_candle_low'] 또는 별도 OHLCV 조회로 주입.
               미주입 시 보수 추정 — avg_price * (1 - 0.01) (1% 손실까지 지지로 인정).

        반환:
          EntrySignal(signal_type="swing_38_add", metadata={'entry_round': 2,
                       'parent_entry_time': iso, 'base_candle_low': float,
                       'elapsed_days': int, 'size_ratio': float})
          → caller 가 수량 = round1_qty × size_ratio 로 매수 호가 송출.
        """
        p = self.params
        if not p.second_entry_enabled:
            return None
        if position.entry_time is None:
            return None
        if len(ctx.candles) < 1:
            return None

        # 일봉 간격 게이트 (Phase C 와 동일 — 분봉 노이즈 차단)
        if p.require_daily_candles and len(ctx.candles) >= 2:
            interval_hours = (
                ctx.candles[-1].timestamp - ctx.candles[-2].timestamp
            ).total_seconds() / 3600
            if interval_hours < 12:
                return None

        # 경과일 (D = 1차 진입일, D+1 부터 평가)
        et = position.entry_time
        if et.tzinfo is None:
            et = et.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - et).days
        if days < p.second_entry_min_days:
            return None  # 당일(D) 추가 진입 차단
        if days > p.second_entry_max_days:
            return None  # 시한 만료 — 추세 변경 위험

        last = ctx.candles[-1]
        cur_price = Decimal(str(last.close))

        # 기준봉 low 결정 — 미주입 시 보수 추정 (avg × 0.99)
        if base_candle_low is None:
            base_low = Decimal(str(position.avg_price)) * Decimal("0.99")
            base_source = "estimated_avg*0.99"
        else:
            base_low = base_candle_low
            base_source = "provided"

        support_threshold = base_low * (
            Decimal("1") - Decimal(str(p.second_entry_support_tolerance))
        )
        if cur_price < support_threshold:
            return None  # 기준봉 지지 깨짐

        return EntrySignal(
            symbol=ctx.symbol,
            name=ctx.name or ctx.symbol,
            price=float(last.close),
            signal_type="swing_38",
            score=10.0,
            reason=(
                f"38스윙 2차: 1차 +{days}일 경과, 기준봉 지지 "
                f"(현재가 {float(cur_price):.0f} ≥ low {float(base_low):.0f} "
                f"× {1 - p.second_entry_support_tolerance:.3f})"
            ),
            market_type=ctx.market_type,
            strategy_id=self.STRATEGY_ID,
            timestamp=datetime.now(timezone.utc),
            metadata={
                "swing_38_subtype": "swing_38_add",
                "entry_round": 2,
                "parent_entry_time": position.entry_time.isoformat(),
                "base_candle_low": float(base_low),
                "base_low_source": base_source,
                "elapsed_days": days,
                "size_ratio": p.second_entry_size_ratio,
            },
        )

    def position_size(self, signal: EntrySignal, account: Account) -> Decimal:
        """BAR-OPS-09 Phase 9: 균등 진입 (5/22 비중 편차 제거).
        score 차등(BAR-175 0-10 스케일) 무력화 — 모든 진입 동일 비율 0.08.
        """
        from backend.core.strategy.position_sizing import even_position_size
        return even_position_size(signal, account)

    def health_check(self) -> dict[str, Any]:
        p = self.params
        return {
            "strategy_id": self.STRATEGY_ID,
            "ready": p.impulse_min_gain_pct >= 0.05 and p.min_candles >= 60,
            "impulse_min_gain_pct": p.impulse_min_gain_pct,
            "fib_target": p.fib_target,
            "fib_tolerance": p.fib_tolerance,
        }

    @staticmethod
    def _atr_pct(candles: List[OHLCV], n: int = 14) -> float:
        """ATR% wrapper — see backend.core.strategy.indicators.atr_pct (Phase 7 refactor)."""
        from backend.core.strategy.indicators import atr_pct
        return atr_pct(candles, n=n)


__all__ = ["Swing38Strategy", "Swing38Params"]
