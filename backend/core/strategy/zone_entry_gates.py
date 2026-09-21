"""존 3전략(f_zone·sf_zone·gold_zone) 진입 게이트 — default-OFF.

배경 (2026-09-22 실측)
---------------------
브로커 체결 기록 `fill_audit.csv` × `order_audit.csv` 로 존 3전략의 라운드트립
75건(일봉 특성 산출 가능분 59건)을 귀속해 측정한 결과, 통합 PF 0.47 로 손익분기
아래였다. 청산 정책(SL/트레일링) 스윕은 **개선에 실패**했고(SL -4.0 → -3.0 → -2.0
로 조일수록 PF 0.63 → 0.56 → 0.35), 병목이 청산이 아니라 **진입**임이 확인됐다.

진입 특성 중 기전이 분명하고 표본이 유지되는 두 축만 게이트로 만든다.

  ① 추세 게이트 — MA5 ≥ MA20 (정배열)
       통과 n=29 PF 0.80 / 탈락 n=30 PF 0.25
  ② 재진입 게이트 — 같은 종목의 첫 진입만 허용
       1번째 n=37 PF 0.56 / 2번째 이상 n=22 PF 0.33

  ①+② 동시 적용: n=20 승률 55.0% **PF 1.04** 평균 +0.08%
                 (탈락군 n=39 PF 0.28 — 분리는 뚜렷)

한계 (반드시 함께 읽을 것)
-------------------------
- **표본 20건**이다. 시간 분할 시 전반 PF 1.22 / 후반 0.78 로 흔들린다.
- **f_zone 은 두 게이트를 통과해도 PF 0.46** 으로 여전히 적자다. 손익분기를
  넘기는 기여는 gold_zone(통과 PF 1.31, n=13) 이 대부분이고 sf_zone 은 n=2 라
  판단 불가다. f_zone 은 이격(vs_ma20)을 어떻게 요구해도 악화만 했다(≥15% → PF 0.07).
- 측정 구간 전체가 **mock 시세**다(일 ±25% 변동이 흔함). 실거래 이전 가능성은 미검증.

따라서 이 모듈은 **관찰·검증용이며 기본값은 완전 OFF** 다. 활성화는 사용자 판단.

활성화
------
  BARRO_ZONE_TREND_GATE_ENABLED=1     # ① 정배열 요구
  BARRO_ZONE_TREND_GATE_MARGIN=0.0    # MA5 가 MA20 보다 최소 N% 위여야 통과
  BARRO_ZONE_REENTRY_GUARD_ENABLED=1  # ② 같은 종목 재진입 차단
  BARRO_ZONE_REENTRY_LOOKBACK_DAYS=60 # ② 조회 기간(일). 0 = 무제한(권장하지 않음)
  BARRO_ZONE_GATE_SHADOW=1            # 측정 전용 — 차단하지 않고 로그만(권장 1주)

모든 플래그 미설정 시 `ZoneGateConfig.any_enabled()` 가 False → 호출부가 평가 자체를
건너뛰므로 라이브 동작은 바이트 동일하다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

# 이 게이트가 적용되는 전략 (STRATEGY_ID 의 _v1 접미사 제거 기준)
ZONE_STRATEGIES = frozenset({"f_zone", "sf_zone", "gold_zone"})

ENV_TREND_ENABLED = "BARRO_ZONE_TREND_GATE_ENABLED"
ENV_TREND_MARGIN = "BARRO_ZONE_TREND_GATE_MARGIN"
ENV_REENTRY_ENABLED = "BARRO_ZONE_REENTRY_GUARD_ENABLED"
ENV_REENTRY_LOOKBACK = "BARRO_ZONE_REENTRY_LOOKBACK_DAYS"
ENV_SHADOW = "BARRO_ZONE_GATE_SHADOW"

_TRUTHY = {"1", "true", "yes", "on", "y"}


def _truthy(env: dict, key: str) -> bool:
    return str(env.get(key, "") or "").strip().lower() in _TRUTHY


def _float(env: dict, key: str, default: float) -> float:
    raw = str(env.get(key, "") or "").strip()
    # 인라인 주석이 값에 섞여 들어오는 .env 파싱 사고 방어 (2026-09-22 실사례)
    raw = raw.split("#", 1)[0].strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class ZoneGateConfig:
    """존 진입 게이트 설정. 전부 default-OFF."""

    trend_enabled: bool = False
    trend_margin_pct: float = 0.0
    reentry_enabled: bool = False
    # 재진입 차단 조회 기간(일). 무제한이면 존 전략이 한 번이라도 건드린 종목이
    # 영구 차단돼 유니버스가 단조 감소한다(2026-09-22 개장 전 발견: 67종목 영구차단).
    # 실측 재진입 간격은 중앙 12일·p90 41일·**최대 49일** — 60일이면 유해 재진입
    # 22건을 전부 포착하면서 윈도우가 유계다(당일 차단 67 → 23종목).
    reentry_lookback_days: int = 60
    shadow: bool = False

    def any_enabled(self) -> bool:
        return self.trend_enabled or self.reentry_enabled


def config_from_env(env: Optional[dict] = None) -> ZoneGateConfig:
    e = os.environ if env is None else env
    return ZoneGateConfig(
        trend_enabled=_truthy(e, ENV_TREND_ENABLED),
        trend_margin_pct=_float(e, ENV_TREND_MARGIN, 0.0),
        reentry_enabled=_truthy(e, ENV_REENTRY_ENABLED),
        reentry_lookback_days=max(0, int(_float(e, ENV_REENTRY_LOOKBACK, 60.0))),
        shadow=_truthy(e, ENV_SHADOW),
    )


def is_zone_strategy(strategy_id: str) -> bool:
    key = (strategy_id or "").replace("_v1", "").replace("_v2", "")
    return key in ZONE_STRATEGIES


def _close(candle: Any) -> Optional[float]:
    """dict / 객체 양쪽을 받는다(캐시 dict, OHLCV 모델)."""
    if candle is None:
        return None
    v = candle.get("close") if isinstance(candle, dict) else getattr(candle, "close", None)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sma(candles: Sequence[Any], n: int) -> Optional[float]:
    if len(candles) < n:
        return None
    vals = [_close(c) for c in candles[-n:]]
    if any(v is None for v in vals):
        return None
    return sum(vals) / n  # type: ignore[arg-type]


def evaluate_trend_gate(
    candles: Sequence[Any], cfg: ZoneGateConfig,
) -> tuple[bool, str]:
    """정배열 게이트. returns (blocked, reason).

    candles 는 오래된→최신 순 일봉. 마지막 봉이 *당일 미완성 봉* 이어도 그대로
    쓴다 — 라이브 판정과 같은 입력을 받아야 시뮬↔라이브가 어긋나지 않는다.
    데이터 부족(20봉 미만)은 **차단하지 않는다**(fail-open) — 게이트가 조회 실패로
    매매를 통째로 멈추는 사고를 막는다.
    """
    if not cfg.trend_enabled:
        return False, ""
    ma5, ma20 = _sma(candles, 5), _sma(candles, 20)
    if ma5 is None or ma20 is None or ma20 == 0:
        return False, "trend:insufficient_data(fail-open)"
    spread = (ma5 - ma20) / ma20 * 100.0
    if spread < cfg.trend_margin_pct:
        return True, f"trend:MA5/MA20 이격 {spread:+.2f}% < {cfg.trend_margin_pct:+.2f}%"
    return False, ""


def evaluate_reentry_gate(
    symbol: str, prior_symbols: Iterable[str], cfg: ZoneGateConfig,
) -> tuple[bool, str]:
    """재진입 게이트. prior_symbols = 과거 존 전략으로 매수한 적 있는 종목 집합."""
    if not cfg.reentry_enabled:
        return False, ""
    if symbol and symbol in set(prior_symbols):
        return True, f"reentry:{symbol} 존 전략 매수 이력 존재 — 첫 진입만 허용"
    return False, ""


def evaluate_zone_gates(
    strategy_id: str,
    symbol: str,
    candles: Sequence[Any],
    prior_symbols: Iterable[str],
    cfg: ZoneGateConfig,
) -> tuple[bool, str]:
    """존 전략 진입 종합 판정. returns (blocked, reason).

    - 존 전략이 아니면 무조건 통과 (다른 전략에 영향 0)
    - cfg 가 전부 OFF 면 무조건 통과
    - shadow=True 면 사유는 돌려주되 blocked=False (측정 전용)
    """
    if not cfg.any_enabled() or not is_zone_strategy(strategy_id):
        return False, ""
    reasons = []
    for blocked, reason in (
        evaluate_trend_gate(candles, cfg),
        evaluate_reentry_gate(symbol, prior_symbols, cfg),
    ):
        if blocked:
            reasons.append(reason)
    if not reasons:
        return False, ""
    return (not cfg.shadow), " + ".join(reasons)


__all__ = [
    "ZONE_STRATEGIES", "ZoneGateConfig", "config_from_env", "is_zone_strategy",
    "evaluate_trend_gate", "evaluate_reentry_gate", "evaluate_zone_gates",
]
