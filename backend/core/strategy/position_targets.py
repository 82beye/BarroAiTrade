"""포지션 목표가 산출 — 진입 시점에 추가매수·손절·익절 가격을 확정해 추적 가능하게 한다.

## 왜 필요한가
기존에는 진입만 기록되고(`active_positions.json` 의 `entry_price`·`sl_pct`) **목표가가
어디인지 사람이 볼 수 없었다**. 대시보드 보유종목에도 평가손익만 보여 "지금 어디까지
가면 익절인지 / 어디서 손절인지"를 알 수 없었다.

## 산출 원천 — 라이브 실효값만 쓴다
청산 임계는 `backend/core/risk/holding_evaluator.STRATEGY_EXIT_PROFILES[<전략>]` 이
**실제로 전담**한다(`evaluate_holding()` → `resolve_policy()`). 전략 파일의
`exit_plan()` 은 라이브에서 호출되지 않으므로 **쓰지 않는다** — 그 값을 읽으면
화면과 실제 청산이 어긋난다(memory: exitengine-unwired-live-sl).

추가매수(DCA) 트리거는 `backend/core/journal/active_positions.Tranche.trigger_drop_pct`
규약을 따른다(T1 0% 즉시 · T2 −2% · T3 −4%).

## 반환 필드
- `entry`            진입가(평균단가)
- `dca_prices`       추가매수 발동가 목록 [(트랜치, 하락%, 가격), ...]
- `stop_loss`        손절가 (= entry × (1 + sl_pct/100))
- `partial_tp`       1차 익절가 + 비중
- `take_profit`      최종 익절가
- `trailing_start`    트레일링 발동가 / `trailing_offset_pct` 되밀림 허용폭
- `breakeven_trigger` 본전 보호 발동가
- `min_hold_until` / `max_hold_until`  보유기간 경계(거래일 아닌 역일 기준 근사)

모든 가격은 **원 단위 정수로 내림**한다(호가 단위 보정은 하지 않는다 — 표시·추적용).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

KST = timezone(timedelta(hours=9))

# DCA 트랜치 규약 — active_positions.Tranche 와 동일 (T1 즉시 · T2 −2% · T3 −4%)
DEFAULT_DCA_DROPS: tuple[float, ...] = (-2.0, -4.0)


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@dataclass
class PositionTargets:
    """진입 시점에 확정되는 목표가 묶음. JSON 직렬화 가능."""

    symbol: str = ""
    strategy: str = ""
    entry: float = 0.0
    stop_loss: float = 0.0
    stop_loss_pct: float = 0.0
    partial_tp: float = 0.0
    partial_tp_pct: float = 0.0
    partial_tp_ratio: float = 0.0
    take_profit: float = 0.0
    take_profit_pct: float = 0.0
    trailing_start: float = 0.0
    trailing_start_pct: float = 0.0
    trailing_offset_pct: float = 0.0
    breakeven_trigger: float = 0.0
    breakeven_trigger_pct: float = 0.0
    dca_prices: list[dict] = field(default_factory=list)
    min_hold_days: int = 0
    max_hold_days: int = 0
    min_hold_until: str = ""
    max_hold_until: str = ""
    as_of: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def build_targets(
    symbol: str,
    strategy: str,
    entry_price: Any,
    *,
    profiles: Optional[dict] = None,
    entry_time: Optional[Any] = None,
    dca_drops: tuple[float, ...] = DEFAULT_DCA_DROPS,
) -> Optional[PositionTargets]:
    """진입가 + 전략 프로파일 → 목표가. 산출 불가 시 None(호출부가 폴백).

    profiles 를 주지 않으면 라이브 `STRATEGY_EXIT_PROFILES` 를 읽는다.
    전략 키는 `_v1`/`_v2` 접미사를 제거해 매칭한다(resolve_policy 와 동일 규약).
    """
    entry = _f(entry_price)
    if entry <= 0:
        return None

    if profiles is None:
        try:
            from backend.core.risk.holding_evaluator import STRATEGY_EXIT_PROFILES
            profiles = STRATEGY_EXIT_PROFILES
        except Exception:
            return None

    key = str(strategy or "").replace("_v1", "").replace("_v2", "")
    # 키 부재(= 등록되지 않은 전략)만 None 이다. 키는 있으나 비어 있으면
    #   임계가 없다는 뜻이므로 0 으로 채운 목표가를 돌려준다 — 화면에서
    #   "목표가 미설정"으로 보이는 것이 조용히 사라지는 것보다 정직하다(§8).
    prof = profiles.get(key)
    if prof is None:
        return None

    def pct(name: str, default: float = 0.0) -> float:
        v = prof.get(name)
        if isinstance(v, Decimal):
            return float(v)
        return _f(v, default)

    sl = pct("stop_loss_pct")
    ptp = pct("partial_tp_pct")
    ptr = pct("partial_tp_ratio")
    tp = pct("take_profit_pct")
    ts = pct("trailing_start_pct")
    to = pct("trailing_offset_pct")
    be = pct("breakeven_trigger_pct")
    minh = int(_f(prof.get("min_hold_days"), 0))
    maxh = int(_f(prof.get("max_hold_days"), 0))

    def at(p: float) -> float:
        return float(int(entry * (1 + p / 100.0)))

    # 보유기간 경계 — 역일 기준 근사(거래일 환산은 호출부·화면에서 해석)
    base: Optional[date] = None
    if entry_time is not None:
        try:
            t = entry_time if isinstance(entry_time, datetime) else datetime.fromisoformat(str(entry_time))
            t = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t
            base = t.astimezone(KST).date()
        except (TypeError, ValueError):
            base = None
    if base is None:
        base = datetime.now(KST).date()

    return PositionTargets(
        symbol=str(symbol or ""),
        strategy=key,
        entry=float(int(entry)),
        stop_loss=at(sl) if sl else 0.0,
        stop_loss_pct=sl,
        partial_tp=at(ptp) if ptp else 0.0,
        partial_tp_pct=ptp,
        partial_tp_ratio=ptr,
        take_profit=at(tp) if tp else 0.0,
        take_profit_pct=tp,
        trailing_start=at(ts) if ts > 0 else 0.0,
        trailing_start_pct=ts,
        trailing_offset_pct=to,
        breakeven_trigger=at(be) if be else 0.0,
        breakeven_trigger_pct=be,
        dca_prices=[
            {"tranche": i + 2, "drop_pct": d, "price": at(d)}
            for i, d in enumerate(dca_drops)
        ],
        min_hold_days=minh,
        max_hold_days=maxh,
        min_hold_until=(base + timedelta(days=minh)).isoformat() if minh else "",
        max_hold_until=(base + timedelta(days=maxh)).isoformat() if maxh else "",
        as_of=datetime.now(KST).isoformat(timespec="seconds"),
    )


def progress(targets: PositionTargets, cur_price: Any) -> dict:
    """현재가 대비 각 목표까지의 거리(%). 화면 표시용 — 판정에는 쓰지 않는다."""
    cur = _f(cur_price)
    if cur <= 0 or targets.entry <= 0:
        return {}
    out = {"cur": cur, "pnl_pct": round(cur / targets.entry * 100 - 100, 2)}
    for name in ("stop_loss", "partial_tp", "take_profit", "trailing_start", "breakeven_trigger"):
        tgt = getattr(targets, name, 0.0)
        if tgt and tgt > 0:
            out[f"to_{name}_pct"] = round(tgt / cur * 100 - 100, 2)
    return out
