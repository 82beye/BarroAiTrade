"""MA 지지 반등 추가매수 판정 (2026-11-06, 사용자 지시) — ★default-OFF★.

판정: 당일 저가가 MA20 을 이탈했으나 종가가 MA20 위로 회복하고, MA60 위에 있을 때
      "단기 저점 확인"으로 보고 추가매수 후보로 올린다.

★실측 경고 — 이 패턴 단독으로는 엣지가 없다★
OHLCV 캐시 1,200 종목 전수(신호 41,377건), 익일 시가 진입·장중 TP/SL 우선 체결·
왕복비용 0.90% 차감 기준:

    보유 1/2/3/5일 평균 수익률  -0.25% / -0.32% / -0.30% / -0.25% (승률 42%)
    TP/SL 전 조합(2~8% × -3~-7%) 기대값 전부 음수 — 최선이 TP+8/SL-7 의 -1.15%

20일 모멘텀 구간별로 보면 **단조로 악화**한다:
    <10%  -1.04%  /  10~30%  -1.34%  /  30~50%  -2.35%  /  50%+  -2.66%
즉 급등 후 조정일수록 더 나쁘다. 41,377건 표본이라 노이즈로 보기 어렵다.

그래서 기본값은 OFF 다. `BARRO_MA_SUPPORT_ADDON_ENABLED=1` 로만 켠다.
켤 경우 모멘텀 상한(`_MAX_MOM_PCT`, default 10)으로 최악 구간을 배제한다.

한계(미검증): 일봉 종가 기준이라 장중 지지/이탈의 실제 경로는 반영하지 않는다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Sequence


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class MaSupportConfig:
    enabled: bool = False
    ma_short: int = 20
    ma_long: int = 60
    max_momentum_pct: float = 10.0   # 20일 상승률 상한 (초과 시 제외)

    @classmethod
    def from_env(cls) -> "MaSupportConfig":
        def _i(name: str, d: int) -> int:
            try:
                return int(os.environ.get(name, str(d)))
            except Exception:
                return d

        def _fl(name: str, d: float) -> float:
            try:
                return float(os.environ.get(name, str(d)))
            except Exception:
                return d

        raw = (os.environ.get("BARRO_MA_SUPPORT_ADDON_ENABLED", "0") or "").strip().lower()
        return cls(
            enabled=raw in {"1", "true", "yes", "on"},
            ma_short=_i("BARRO_MA_SUPPORT_SHORT", 20),
            ma_long=_i("BARRO_MA_SUPPORT_LONG", 60),
            max_momentum_pct=_fl("BARRO_MA_SUPPORT_MAX_MOM_PCT", 10.0),
        )


@dataclass(frozen=True)
class MaSupportResult:
    signal: bool
    reason: str
    ma_short: float = 0.0
    ma_long: float = 0.0
    momentum_pct: float = 0.0


def evaluate_ma_support(
    closes: Sequence[float],
    low: float,
    close: float,
    cfg: Optional[MaSupportConfig] = None,
) -> MaSupportResult:
    """MA 지지 반등 여부. 판정일까지의 종가만 쓴다(룩어헤드 차단).

    closes: 판정일 종가를 **포함**한 과거 종가 시퀀스(오름차순).
    실패·데이터 부족은 전량 흡수해 signal=False (fail-closed, §2 S3).
    """
    cfg = cfg or MaSupportConfig.from_env()
    if not cfg.enabled:
        return MaSupportResult(False, "비활성(default-OFF)")
    try:
        c = [_f(x) for x in closes if _f(x) > 0]
        n_long = cfg.ma_long
        if len(c) < n_long or low <= 0 or close <= 0:
            return MaSupportResult(False, f"데이터 부족(종가 {len(c)}개 < {n_long})")
        ma_s = sum(c[-cfg.ma_short:]) / cfg.ma_short
        ma_l = sum(c[-n_long:]) / n_long
        mom = (c[-1] / c[-21] - 1) * 100 if len(c) >= 21 and c[-21] > 0 else 0.0

        if not (low < ma_s <= close):
            return MaSupportResult(False, f"MA{cfg.ma_short} 지지확인 실패", ma_s, ma_l, mom)
        if close <= ma_l:
            return MaSupportResult(False, f"MA{cfg.ma_long} 아래 — 중기 하락", ma_s, ma_l, mom)
        if mom > cfg.max_momentum_pct:
            return MaSupportResult(
                False,
                f"20일 모멘텀 {mom:.1f}% > 상한 {cfg.max_momentum_pct}% "
                f"(실측: 모멘텀 높을수록 기대값 악화)",
                ma_s, ma_l, mom,
            )
        return MaSupportResult(
            True,
            f"MA{cfg.ma_short} 지지 반등 (저가 {low:,.0f} < MA {ma_s:,.0f} <= 종가 {close:,.0f})",
            ma_s, ma_l, mom,
        )
    except Exception as exc:  # noqa: BLE001 — 외부 입력, 라이브 영향 차단
        return MaSupportResult(False, f"평가 실패: {exc}")
