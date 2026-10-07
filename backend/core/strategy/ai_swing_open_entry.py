"""ai_swing 개장 진입 모드 — 추천 유니버스 × 팩터 게이트로 09:05 진입.

## 왜 필요한가

ai_swing 은 `swing_38` 을 상속해 **임펄스 → 38.2% 되돌림 → 반등** 패턴을 요구한다.
이 패턴은 추천 유니버스에서 43거래일 동안 **3건**만 발화했다(하루 0.07건) — 즉 전략이
사실상 거래하지 못한다. 반면 추천 종목의 실측 엣지는 **진입 타이밍**에 있다.

### 진입 타이밍 실측 (추천 1,969건 · 44거래일 · 청산 D+10 종가 · net = 비용 0.891%p 차감)

| 진입 | net평균 | PF |
|---|---|---|
| **추천일 시가** | **+5.31%** | **2.79** |
| 추천일 종가 | +0.66% | 1.16 |
| 익일 시가 | +1.26% | 1.29 |

시가와 종가 차이가 **+4.65%p** — 엣지의 거의 전부가 추천일 당일 장중 상승이다.
장중에 들어가면 사라진다. 현행 ai_swing 은 `BARRO_OPEN_HOLD_HHMM=0930` 때문에
**09:30 이후에만** 진입하므로 구조적으로 이 엣지를 수확할 수 없다.

### 시간 감쇠 (필터 통과분 · 5분봉 · 청산 D+3)

| 진입 | net | PF | 전후반 안정 |
|---|---|---|---|
| 09:00 시가 | +3.44% | 3.44 | O |
| **09:05 (본 모듈)** | **+3.28%** | **3.32** | **O** |
| 09:10 | +2.96% | 2.89 | O |
| 09:30 (현행) | +2.39% | 2.38 | **X** |
| 10:00 | +2.07% | 2.07 | **X** |

09:05 는 시가 대비 **−0.17%p** 손실로 거의 동등하고 전후반 안정성도 유지한다.
09:30 부터는 전반이 음수(−0.18)로 뒤집혀 불안정해진다. 데몬 `BUY_START` 가 09:05 이므로
**09:05 가 실행 가능한 최선**이다.

### 유니버스 (진입 09:05 · 청산 D+3)

| 유니버스 | n | 일평균 | net | PF |
|---|---|---|---|---|
| **스캔(watchlist)** | 63 | 1.43 | **+4.82%** | **4.29** |
| 합집합 | 208 | 4.73 | +3.28% | 3.32 |
| 예측(predictions) | 159 | 3.61 | +2.49% | 2.68 |
| **교집합 (현행 ai_swing)** | 14 | 0.32 | **+1.26%** | **1.52** |

**교집합이 가장 나쁘다** — ai_swing 의 정의적 특징인 교집합 조건이 엣지를 깎는다.
기본값은 측정 최선인 `scan` 으로 둔다.

### 보유기간 (진입 시가 · 필터 통과 n=90)

| 청산 | net | PF |
|---|---|---|
| D+1 종가 | +2.71% | **4.79** |
| D+3 종가 | +2.19% | 2.30 |
| D+5 종가 | **+3.79%** | 2.72 |
| D+10 종가 | +3.70% | 2.20 |
| D+20 종가 | +2.79% | 1.54 |

D+1 이 PF 최고지만 ai_swing 프로파일의 `min_hold_days=3` 이 당일 청산을 막는다.
D+3~D+15 구간은 모두 net +2.19~+3.79% · PF 2.0~2.7 로 양호하므로 **청산은 기존
ai_swing 프로파일을 그대로 쓴다**(별도 변경 없음).

## 한계 (반드시 함께 읽을 것)
- 필터 통과 표본 **63~208건** · 44거래일 · 2026-08~10 **한 국면**.
- 필터(고가 −5% × 거래대금 30억)는 약 40개 규칙 탐색의 최선값이라 **선택 편향**이 있다.
  전후반 부호 일치가 이를 완화하지만 제거하지 못한다.
- 5분봉 캐시가 없는 종목은 09:05 가격을 산출하지 못해 측정에서 빠졌다(낙관 편향 가능).
- 일봉·모의 시세 기준. 슬리피지·호가 공백 미반영.
- **패턴 판정을 건너뛴다** — 이 경로의 진입 권위는 팩터 게이트다. 즉 ai_swing 의
  원래 설계(눌림목 스윙)와 다른 성격의 진입이며, 같은 청산 프로파일을 공유한다.

기본값은 **OFF** 다. 활성화는 사용자 판단이다(§2 S3·S4).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from typing import Any, Iterable, Sequence

ENV_ENABLED = "BARRO_AI_SWING_OPEN_ENTRY_ENABLED"
ENV_UNIVERSE = "BARRO_AI_SWING_OPEN_ENTRY_UNIVERSE"
ENV_UNTIL = "BARRO_AI_SWING_OPEN_ENTRY_UNTIL"
ENV_NEAR20 = "BARRO_AI_SWING_OPEN_ENTRY_NEAR20_PCT"
ENV_TURNOVER = "BARRO_AI_SWING_OPEN_ENTRY_MIN_TURNOVER"
ENV_MIN_PRICE = "BARRO_AI_SWING_OPEN_ENTRY_MIN_PRICE"

UNIVERSES = ("scan", "pred", "union", "inter")
DEFAULT_UNIVERSE = "scan"          # 측정 최선 (net +4.82% · PF 4.29)
DEFAULT_UNTIL = "0910"             # 09:10 이후는 엣지가 감쇠한다
DEFAULT_NEAR20_PCT = -5.0
DEFAULT_MIN_TURNOVER = 3.0e9
DEFAULT_MIN_PRICE = 5000.0
MIN_CANDLES = 20


def _truthy(name: str, default: str = "0") -> bool:
    return (os.environ.get(name, default) or "").strip().lower() in {"1", "true", "yes", "on"}


def _num(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class OpenEntryConfig:
    """개장 진입 설정. enabled=False(기본)면 이 경로는 전혀 동작하지 않는다."""

    enabled: bool = False
    universe: str = DEFAULT_UNIVERSE
    until_hhmm: str = DEFAULT_UNTIL
    near20_pct: float = DEFAULT_NEAR20_PCT
    min_turnover: float = DEFAULT_MIN_TURNOVER
    min_price: float = DEFAULT_MIN_PRICE

    @classmethod
    def from_env(cls) -> "OpenEntryConfig":
        uni = (os.environ.get(ENV_UNIVERSE) or DEFAULT_UNIVERSE).strip().lower()
        if uni not in UNIVERSES:
            uni = DEFAULT_UNIVERSE
        until = (os.environ.get(ENV_UNTIL) or DEFAULT_UNTIL).strip()
        if not (len(until) == 4 and until.isdigit()):
            until = DEFAULT_UNTIL
        return cls(
            enabled=_truthy(ENV_ENABLED, "0"),
            universe=uni,
            until_hhmm=until,
            near20_pct=_num(ENV_NEAR20, DEFAULT_NEAR20_PCT),
            min_turnover=_num(ENV_TURNOVER, DEFAULT_MIN_TURNOVER),
            min_price=_num(ENV_MIN_PRICE, DEFAULT_MIN_PRICE),
        )

    def until_time(self) -> time:
        return time(int(self.until_hhmm[:2]), int(self.until_hhmm[2:]))


def in_open_entry_window(now: Any, cfg: OpenEntryConfig, buy_start: time) -> bool:
    """now 가 [buy_start, until) 구간인가. 비활성이면 항상 False."""
    if not cfg.enabled:
        return False
    try:
        t = now.time() if hasattr(now, "time") else now
        return bool(buy_start <= t < cfg.until_time())
    except (AttributeError, TypeError, ValueError):
        return False      # 잘못된 입력은 창 밖으로 간주(기존 보류 거동 유지)


def select_universe(cfg: OpenEntryConfig, scan: Iterable[str], pred: Iterable[str]) -> list[str]:
    """설정에 따라 추천 유니버스를 고른다. 중복 제거 + 정렬로 결정적 순서를 보장한다."""
    s, p = set(scan or ()), set(pred or ())
    if cfg.universe == "pred":
        sel = p
    elif cfg.universe == "union":
        sel = s | p
    elif cfg.universe == "inter":
        sel = s & p
    else:                      # "scan" (기본)
        sel = s
    return sorted(sel)


def read_reco_codes(day: str, base_dir: str | None = None) -> tuple[list[str], list[str], str]:
    """(scan_codes, pred_codes, reason). `BARRO_AI_TRADE_DIR` 하위 추천 파일을 읽는다.

    규약: `watchlist_{YYYY-MM-DD}.json` · `predictions_{YYYY-MM-DD}.json`
    (backend/core/scanner/ai_trade_universe.py 와 동일). 부재·파싱 실패는 전량 흡수해
    빈 목록 + 사유를 돌린다 — 라이브 무영향(§2 S3).
    """
    import json
    from pathlib import Path

    base = (base_dir or os.environ.get("BARRO_AI_TRADE_DIR") or "").strip()
    if not base:
        return [], [], "ai_trade_dir_unset"
    d = Path(base)
    if not d.is_dir():
        return [], [], "ai_trade_dir_missing"

    def _codes(path: Path) -> list[str] | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        rows = payload.get("stocks") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return None
        out = []
        for r in rows:
            c = str((r or {}).get("code") or "").strip()
            if c:
                out.append(c)
        return out

    scan = _codes(d / f"watchlist_{day}.json")
    pred = _codes(d / f"predictions_{day}.json")
    if scan is None and pred is None:
        return [], [], "files_missing"
    if scan is None:
        return [], pred or [], "watchlist_missing"
    if pred is None:
        return scan, [], "predictions_missing"
    return scan, pred, ""


def compute_factors(candles: Sequence[Any]) -> tuple[float, float, float] | None:
    """(20일고가 근접%, 20일 평균 거래대금, 최근 종가). 자료 부족·이상치는 None."""
    if candles is None or len(candles) < MIN_CANDLES:
        return None
    w = list(candles)[-MIN_CANDLES:]
    try:
        highs = [float(c.high) for c in w]
        closes = [float(c.close) for c in w]
        vols = [float(c.volume) for c in w]
    except (AttributeError, TypeError, ValueError):
        return None
    hh = max(highs)
    last = closes[-1]
    if hh <= 0 or last <= 0:
        return None
    turnover = sum(c * v for c, v in zip(closes, vols)) / len(w)
    return (last / hh - 1.0) * 100.0, turnover, last


def evaluate_open_entry(candles: Sequence[Any], cfg: OpenEntryConfig) -> tuple[bool, str]:
    """(진입 허용?, 사유). 이 경로는 **fail-closed** 다.

    팩터 게이트가 진입 권위이므로 자료가 없으면 판정할 수 없다 — 패턴 판정도
    건너뛰는 경로라 통과시키면 근거 없는 진입이 된다. 일반 게이트(fail-open)와
    반대 방향임을 의도적으로 택했다.
    """
    if not cfg.enabled:
        return False, "open_entry:비활성"
    f = compute_factors(candles)
    if f is None:
        return False, "open_entry:자료부족 — 판정 불가로 차단(fail-closed)"
    near20, turnover, price = f
    fails = []
    if near20 < cfg.near20_pct:
        fails.append(f"20일고가 {near20:+.1f}% < {cfg.near20_pct:+.1f}%")
    if turnover < cfg.min_turnover:
        fails.append(f"거래대금 {turnover/1e8:.0f}억 < {cfg.min_turnover/1e8:.0f}억")
    if price < cfg.min_price:
        fails.append(f"주가 {price:,.0f} < {cfg.min_price:,.0f}")
    if fails:
        return False, "open_entry:" + " + ".join(fails)
    return True, ""
