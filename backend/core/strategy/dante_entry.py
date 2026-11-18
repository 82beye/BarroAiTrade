"""역매공파 '파란점선' 돌파 진입 판정 (dante) — ★default-OFF★.

주식단테 역매공파 이론의 「파(破)」 단계. 1년 고점에서 평균 변동폭의 2배를 뺀 선을
개인 수급만으로는 넘기 어려운 가격대로 보고, 종가가 이를 넘어서면 세력 개입으로 읽는다.

    Lp = Highest(High, 224) − ATR(224) × 2.0
    breakout = 전일 종가 ≤ Lp_prev × 1.02  AND  당일 종가 > Lp × 1.02

★이름 주의★ `backend/core/strategy/blue_line.py` 의 BlueLineStrategy 는 **다른 전략**이다
(5일 EMA 골든크로스). 수식도 목적도 무관하므로 섞지 말 것.

근거 (2026-11-16 전수 측정 · 룩어헤드 차단: 신호는 당일 종가까지, 진입은 익일 시가)
-----------------------------------------------------------------------------
OHLCV 일봉 캐시 1,315종목 · 돌파 신호 6,290건 · 왕복비용 0.891%p 차감:

    보유 40일 TP+35/SL-7  기대값 +1.30%  PF 1.43
    보유 20일 TP+35/SL-7  기대값 +0.91%  PF 1.38
    보유 10일 TP+35/SL-7  기대값 +0.47%  PF 1.33

같은 날 전종목 기준선(511,014건, 기대값 -0.42%) 대비 **엣지 +1.23%p**.
날짜를 보정해도 남으므로 상승장 효과가 아니다. 전후반 분할도 둘 다 플러스
(전반 +1.04% / 후반 +0.58%).

★익절을 조이면 엣지가 사라진다★ — TP +35 → +10 으로 줄이면 +0.91% → **-0.58%**.
원본 문서의 '당일매매 조정값'(TP +3~5 / SL -2 / 당일청산)은 전 조합 마이너스다
(최선 -1.32%). 수박지표는 단독 기대값 +0.03% 로 엣지가 없고 파란점선과 동시
발생이 11건뿐이라 결합 필터로도 쓸 표본이 없다 — 이 모듈은 파란점선만 다룬다.

★운용 위험★
    승률 28% · 최대 연속손실 22건 · MDD -54.7%
    청산 분포: SL 67% / TP 16% / 기간청산 17%
수익은 소수의 +35% 건이 만든다. 중간에 끄면 손실만 확정된다.

활성화
------
    BARRO_DANTE_ENABLED=1          # 진입 허용 (기본 0)
    BARRO_DANTE_SHADOW=1           # 측정 전용 — 신호 로그만, 주문 없음
    BARRO_DANTE_MIN_TURNOVER=1e9   # 20일 평균 거래대금 하한(원)
    BARRO_DANTE_LOOKBACK=224       # Lp lookback
    BARRO_DANTE_MULTIPLIER=2.0     # ATR 승수
    BARRO_DANTE_BAND=0.02          # above/below 판정 밴드(±2%)

미설정 시 `DanteConfig.enabled=False` → 호출부가 평가를 건너뛰므로 라이브 거동은
바이트 동일하다(§2 S3).

구현 주의
--------
라이브 데몬은 pandas 를 쓰지 않는다(리스트 기반). `legacy_scalping/scanner/
indicators.py` 는 DataFrame 기반이라 핫패스에서 재사용하면 종목마다 DataFrame 을
만들어야 한다 — 수식만 동일하게 옮기고 의존성은 늘리지 않는다. 수식 일치는
`backend/tests/strategy/test_dante_entry.py` 의 교차검증 테스트로 고정한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional, Sequence

STRATEGY_ID = "dante"

ENV_ENABLED = "BARRO_DANTE_ENABLED"
ENV_SHADOW = "BARRO_DANTE_SHADOW"
ENV_MIN_TURNOVER = "BARRO_DANTE_MIN_TURNOVER"
ENV_LOOKBACK = "BARRO_DANTE_LOOKBACK"
ENV_MULT = "BARRO_DANTE_MULTIPLIER"
ENV_BAND = "BARRO_DANTE_BAND"


def _truthy(env: dict, key: str) -> bool:
    return str(env.get(key, "")).strip().lower() in {"1", "true", "yes", "on"}


def _float(env: dict, key: str, default: float) -> float:
    raw = str(env.get(key, "")).strip()
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    return v if v == v and v not in (float("inf"), float("-inf")) else default


def _int(env: dict, key: str, default: int) -> int:
    try:
        return max(1, int(_float(env, key, float(default))))
    except (ValueError, OverflowError):
        return default


@dataclass(frozen=True)
class DanteConfig:
    enabled: bool = False
    shadow: bool = False
    min_turnover: float = 1_000_000_000.0
    lookback: int = 224
    multiplier: float = 2.0
    band: float = 0.02


def config_from_env(env: Optional[dict] = None) -> DanteConfig:
    e = os.environ if env is None else env
    return DanteConfig(
        enabled=_truthy(e, ENV_ENABLED),
        shadow=_truthy(e, ENV_SHADOW),
        min_turnover=max(0.0, _float(e, ENV_MIN_TURNOVER, 1_000_000_000.0)),
        lookback=_int(e, ENV_LOOKBACK, 224),
        multiplier=max(0.0, _float(e, ENV_MULT, 2.0)),
        band=max(0.0, _float(e, ENV_BAND, 0.02)),
    )


@dataclass(frozen=True)
class DanteSignal:
    """판정 결과. signal=True 면 '당일 돌파'."""
    signal: bool
    reason: str
    blue_line: float = 0.0
    close: float = 0.0
    status: str = ""          # below | near | above
    turnover: float = 0.0


def _get(candle: Any, *names: str) -> Optional[float]:
    """dict / 객체 양쪽을 받는다(캐시 dict, OHLCV 모델) — 존 게이트와 같은 규약."""
    if candle is None:
        return None
    for n in names:
        v = candle.get(n) if isinstance(candle, dict) else getattr(candle, n, None)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return None


def _atr(candles: Sequence[Any], period: int, end: int) -> Optional[float]:
    """True Range 단순평균. end 는 포함 인덱스.

    `indicators.calc_atr` 의 `rolling(period, min_periods=1).mean()` 과 일치시킨다 —
    창이 덜 찼으면 있는 봉만으로 평균하고, 첫 봉은 prev_close 가 NaN 이라 pandas 가
    tr1(high-low) 만 남기므로 여기서도 그렇게 계산한다. 이 동치는
    test_cross_validate_against_pandas_indicators 가 고정한다.
    """
    start = max(0, end - period + 1)
    if end < 0 or start > end:
        return None
    total = 0.0
    count = 0
    for j in range(start, end + 1):
        h = _get(candles[j], "high")
        low = _get(candles[j], "low")
        if h is None or low is None:
            return None
        tr = h - low
        if j >= 1:
            pc = _get(candles[j - 1], "close")
            if pc is None:
                return None
            tr = max(tr, abs(h - pc), abs(low - pc))
        total += tr
        count += 1
    return total / count if count else None


def blue_line_at(candles: Sequence[Any], cfg: DanteConfig, end: int) -> Optional[float]:
    """end 인덱스 시점의 파란점선. 데이터 부족·이상치는 None."""
    n = cfg.lookback
    if end < 1 or end >= len(candles):
        return None
    lo = max(0, end - n + 1)
    highs = [_get(candles[j], "high") for j in range(lo, end + 1)]
    if not highs or any(h is None for h in highs):
        return None
    atr = _atr(candles, n, end)
    if atr is None:
        return None
    return max(highs) - atr * cfg.multiplier  # type: ignore[arg-type]


def status_at(close: float, blue: float, band: float) -> str:
    """indicators.calc_blue_line_status 와 동일 규약.

    경계(ratio == 1±band)는 **near** 다. 1e-9 여유를 둔 이유는 10,200/10,000 처럼
    나눗셈 결과가 1.02 보다 1 ulp 커져 'above' 로 튀는 사례가 있어서다 — 존 게이트
    모멘텀 경계에서 이미 같은 버그를 한 번 겪었다.
    """
    if blue <= 0:
        return ""
    ratio = close / blue
    if ratio < 1.0 - band - 1e-9:
        return "below"
    if ratio < 1.0 + band + 1e-9:
        return "near"
    return "above"


def evaluate_breakout(
    candles: Sequence[Any], cfg: Optional[DanteConfig] = None,
) -> DanteSignal:
    """파란점선 당일 돌파 판정.

    candles 는 오래된→최신 순 일봉이며 **마지막 봉이 판정일**이다. 데몬은 당일
    미완성 봉을 포함해 넘기므로 존 게이트와 같은 입력을 받는다.

    데이터 부족·종가 이상치는 **신호 없음**으로 처리한다(fail-closed) — 진입을
    만드는 쪽이라 불확실하면 사지 않는 것이 안전하다. (차단 게이트의 fail-open 과
    방향이 반대인 점에 주의.)
    """
    cfg = cfg or config_from_env()
    if not cfg.enabled:
        return DanteSignal(False, "비활성(default-OFF)")

    need = cfg.lookback + 1
    if len(candles) < need:
        return DanteSignal(False, f"데이터 부족({len(candles)}봉 < {need})")

    i = len(candles) - 1
    close = _get(candles[i], "close")
    prev_close = _get(candles[i - 1], "close")
    if close is None or prev_close is None or close <= 0 or prev_close <= 0:
        return DanteSignal(False, "종가 이상치")

    blue = blue_line_at(candles, cfg, i)
    blue_prev = blue_line_at(candles, cfg, i - 1)
    if blue is None or blue_prev is None or blue <= 0 or blue_prev <= 0:
        return DanteSignal(False, "파란점선 산출 불가")

    st = status_at(close, blue, cfg.band)
    st_prev = status_at(prev_close, blue_prev, cfg.band)

    # 거래대금 하한 — 실행 가능성(시뮬도 10억 이상만 집계했다)
    turnover = 0.0
    vols = [_get(candles[j], "volume") for j in range(max(0, i - 19), i + 1)]
    if all(v is not None for v in vols) and vols:
        turnover = (sum(vols) / len(vols)) * close  # type: ignore[arg-type]
    if cfg.min_turnover > 0 and turnover < cfg.min_turnover:
        return DanteSignal(False, f"거래대금 {turnover/1e8:.0f}억 < 하한 {cfg.min_turnover/1e8:.0f}억",
                           blue, close, st, turnover)

    if st == "above" and st_prev in ("below", "near"):
        return DanteSignal(
            True,
            f"dante:파(破) 돌파 종가 {close:,.0f} > Lp {blue:,.0f}×{1+cfg.band:.2f}"
            f" (전일 {st_prev})",
            blue, close, st, turnover,
        )
    return DanteSignal(False, f"돌파 아님(현재 {st} / 전일 {st_prev})", blue, close, st, turnover)


__all__ = [
    "STRATEGY_ID", "DanteConfig", "DanteSignal", "config_from_env",
    "blue_line_at", "status_at", "evaluate_breakout",
]
