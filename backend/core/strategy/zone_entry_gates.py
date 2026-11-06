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
ENV_MOM_MAX = "BARRO_ZONE_MAX_MOM20_PCT"      # ③ 20일 모멘텀 상한(%). 0 또는 미설정 = OFF

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
    # ③ 과확장 게이트 — 진입 직전 20일 모멘텀 상한(%). 0 = OFF.
    #   실측(존3 38건): 상한 30% 기준 통과 23건 PF 0.72 / 탈락 15건 PF 0.28.
    #   gold_zone 단독은 통과 18건 PF 1.00 / 탈락 11건 PF 0.13 으로 분리가 가장 뚜렷하다.
    max_mom20_pct: float = 0.0

    def any_enabled(self) -> bool:
        return self.trend_enabled or self.reentry_enabled or self.max_mom20_pct > 0


def config_from_env(env: Optional[dict] = None) -> ZoneGateConfig:
    e = os.environ if env is None else env
    return ZoneGateConfig(
        trend_enabled=_truthy(e, ENV_TREND_ENABLED),
        trend_margin_pct=_float(e, ENV_TREND_MARGIN, 0.0),
        reentry_enabled=_truthy(e, ENV_REENTRY_ENABLED),
        reentry_lookback_days=max(0, int(_float(e, ENV_REENTRY_LOOKBACK, 60.0))),
        shadow=_truthy(e, ENV_SHADOW),
        max_mom20_pct=max(0.0, _float(e, ENV_MOM_MAX, 0.0)),
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



def evaluate_momentum_gate(
    candles: Sequence[Any], cfg: ZoneGateConfig,
) -> tuple[bool, str]:
    """과확장 게이트 — 진입 직전 20일 모멘텀이 상한을 넘으면 차단.

    근거(2026-11-06 실측, 존3 라운드트립 38건 · 진입 **전일까지**의 종가만 사용):

        상한 10%  통과 11건 PF 0.73 / 탈락 27건 PF 0.43
        상한 20%  통과 16건 PF 0.71 / 탈락 22건 PF 0.39
        상한 30%  통과 23건 PF 0.72 / 탈락 15건 PF 0.28
        상한 40%  통과 27건 PF 0.64 / 탈락 11건 PF 0.26

    네 임계 모두 통과>탈락이고 방향이 단조라 단일 최적점 과적합 위험이 낮다.
    탈락 15건 중 10건이 손실이며 상위 손실이 고르게 분포한다(한두 건 쏠림 아님).

    교차검증: OHLCV 1,200종목의 MA20 지지반등 패턴 41,377건에서도 20일 모멘텀이
    높을수록 기대값이 단조 악화했다(<10% -1.04% / 30~50% -2.35% / 50%+ -2.66%).
    서로 독립인 두 데이터가 같은 방향을 가리킨다.

    ★한계★
    - 존3 표본 38건. 전후반 분할 시 통과군 PF 1.06 → 0.53 으로 약화한다
      (탈락군은 0.27 → 0.29 로 안정이라 분리 자체는 유지).
    - **f_zone(n=7)은 역전**한다(통과 PF 0.42 / 탈락 1.06). 표본이 작아 판단 보류이며
      f_zone 에 적용하려면 별도 검증이 필요하다. 기여는 gold_zone 이 대부분이다.
    - 네이버 카페 매매일지 45,547건 본문에서 타점 패턴별 익절/손절 동반비는
      1.05~1.21 로 유의한 차이가 없었다 — 일지는 이 게이트의 근거가 **아니다**.
      다만 '이평지지'(1.05)·'돌파'(1.08)가 최하위인 점은 위 측정과 방향이 같다.

    데이터 부족(21봉 미만)은 **차단하지 않는다**(fail-open) — 조회 실패가 매매를
    통째로 멈추는 사고를 막는다. 추세 게이트와 같은 규약이다.
    """
    if cfg.max_mom20_pct <= 0:
        return False, ""
    if len(candles) < 21:
        return False, ""
    now = _close(candles[-1])
    past = _close(candles[-21])
    if now is None or past is None or past <= 0:
        return False, ""
    mom = (now / past - 1.0) * 100.0
    # 경계값은 통과시킨다 — 부동소수점 오차로 상한과 같은 값이 차단되지 않게 1e-9 여유.
    if mom > cfg.max_mom20_pct + 1e-9:
        return True, f"overext:20일 모멘텀 {mom:+.2f}% > 상한 +{cfg.max_mom20_pct:.2f}%"
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
        evaluate_momentum_gate(candles, cfg),
    ):
        if blocked:
            reasons.append(reason)
    if not reasons:
        return False, ""
    return (not cfg.shadow), " + ".join(reasons)


__all__ = [
    "ZONE_STRATEGIES", "ZoneGateConfig", "config_from_env", "is_zone_strategy",
    "evaluate_trend_gate", "evaluate_reentry_gate", "evaluate_momentum_gate",
    "evaluate_zone_gates",
]
