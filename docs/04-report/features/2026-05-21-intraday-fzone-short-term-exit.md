---
tags: [report, strategy, intraday, fzone, exit-signal, 2026-05-21]
date: 2026-05-21
related:
  - "[[2026-05-21-0520-live-trade-verification]]"
  - "[[bar-46-47-f-sf-zone-deep-analysis]]"
---

# 1분봉 F존 + 단기 고점 캔들 인식 매도 모듈 — 이미지 패턴 코드화

## Context

사용자 제시 차트 (142280 5/20 1분봉):
- **매수 기준선** (09:15) / **매수 시그널** (09:30) — 눌림목 매수 패턴
- **매도 기준선** (09:18) / **매도 시그널** (09:43~45) — 단기 고점 매물대 패턴

기존 F존 코드는 일봉 기반 5단계 매수 + % 기반 청산만 지원. 이미지의 1분봉 차트 패턴 매매 코드화 진행.

## Step 1: 단기 고점 캔들 인식 매도 모듈

`backend/core/strategy/short_term_high_exit.py` 신규

### 3 패턴 (1 충족 시 SELL)

| 패턴 | 조건 | 의미 |
|---|---|---|
| **DOJI** | body/range < 15% (open ≈ close) | 결정 미루는 매물대 |
| **UPPER_WICK** | wick ≥ body × 1.0 AND wick ≥ 가격 × 0.5% | 셀러 출현 (위꼬리 음봉/도지) |
| **RED_FOLLOW** | 직전 봉 peak + 현재 봉 음봉 | 고점 직후 매도세 |

### 선행 조건
- 현재 봉 high 가 최근 30봉 peak 의 0.3% 안 (peak_tolerance_pct)
- 익절 구간 도달 (rate ≥ partial_tp_pct 3.0%) — `evaluate_holding` 에서 평가

### 142280 5/20 검증

| 시각 | OHLC | 패턴 | 결과 |
|---|---|---|---|
| 09:15 | 5,660/5,770/5,610/5,620 | HOLD | 고점 미근접 |
| **09:18** | 5,850/5,900/5,770/5,810 | **upper_wick** | **SELL ★ (매도 기준선)** |
| 09:21 | 6,120/6,290/6,100/6,175 | upper_wick | SELL (추가 고점) |
| **09:45** | 6,310/6,310/6,310/6,310 | **doji** | **SELL ★ (매도 시그널)** |

## Step 2: SellSignal.SHORT_TERM_HIGH 통합

`backend/core/risk/holding_evaluator.py`

```python
class SellSignal:
    ...
    SHORT_TERM_HIGH = "short_term_high"   # 신규

class PositionContext:
    ...
    minute_candles: Optional[list] = None  # 1분봉 시퀀스

def evaluate_holding(h, policy, ctx):
    # 우선순위 0 (trailing 이전)
    if ctx.minute_candles and rate >= policy.partial_tp_pct:
        ste = detect_short_term_high_exit(ctx.minute_candles)
        if ste.signal:
            return HoldingDecision(signal=SHORT_TERM_HIGH, ...)
```

### daemon 통합 (`intraday_buy_daemon.py`)

- `_evaluate_and_sell` 에서 익절 구간(+3%) 도달 종목만 fetch_minute → 1분봉 전달
- API 부담 절감 (모든 보유 fetch 안 함)

### 통합 검증

142280 5/20 09:15 entry @5,620 → 09:18 cur @5,810 (rate +3.38%)
→ **signal=short_term_high, pattern=upper_wick** ✅

## Step 3: 1분봉 F존 (`FZoneParams.for_intraday()`)

`backend/core/strategy/f_zone.py`

### v1 → v2 튜닝

| 파라미터 | 일봉 default | v1 intraday | **v2 intraday** |
|---|---|---|---|
| impulse_min_gain_pct | 3.0% | 1.5% | **1.0%** |
| impulse_lookback | 5봉 | 30봉 | **15봉** |
| pullback_min/max | -5%/-0.5% | -3%/-0.3% | -3%/-0.3% |
| pullback_max_candles | 10 | 60봉 | **30봉** |
| pullback_volume_ratio | 0.7 | 0.7 | **0.85** |
| ma_periods | [5,20,60] | [20,60,240] | **[20,60,120]** |
| ma_support_tolerance | 1.0% | 0.5% | 0.5% |
| bounce_min_gain_pct | 0.5% | 0.3% | 0.3% |
| bounce_volume_ratio | 1.2 | 1.2 | **1.1** |
| min_candles | 60 | 240 | **120** |

### v2 검증

| 종목 | 일자/시각 | 신호 | score | 패턴 |
|---|---|---|---:|---|
| **274090 덕산테코피아** | 5/19 09:10 | **f_zone** ★ | **6.68** | 기준봉 +4.3% 18.4x / 눌림 -1.7% / 반등 +2.1% |
| **080220 제주반도체** | 5/19 09:11 | **f_zone** ★ | **6.83** | 기준봉 +2.6% 2.2x / 눌림 -0.6% / 반등 +1.2% |
| **080220** | 5/19 09:12 | **f_zone** ★ | **7.45** | 반등 강화 (+2.1% 2.5x) |
| 142280 | 5/20 09:25~35 | no signal | — | 시초가 폭등 후 매물대 — 일반 5단계 미적용 |

→ **5/19 실제 익절 종목(274090 +309k / 080220 +215k) 진입 시점 1분봉 F존 신호 정확 인식**.

## 변경 파일 (commits)

| commit | 내용 |
|---|---|
| `eac68e0` | Step 1+2 — short_term_high_exit 모듈 + evaluate_holding 통합 + daemon 1분봉 fetch |
| `58e32fa` | Step 3 v1 — FZoneParams.for_intraday() factory |
| `7a36f72` | Step 3 v2 튜닝 — 임계 완화로 신호 빈도 확보 |

## 사용 방법

### 1분봉 F존 진입
```python
from backend.core.strategy.f_zone import FZoneStrategy, FZoneParams
intra = FZoneStrategy(FZoneParams.for_intraday())
sig = intra.analyze(ctx)  # ctx.candles = 1분봉 시퀀스
```

### 단기 고점 매도 평가
```python
from backend.core.risk.holding_evaluator import PositionContext, evaluate_holding
ctx = PositionContext(
    ...,
    minute_candles=minute_bars_today,  # 보유 종목 5/20 1분봉
)
decision = evaluate_holding(h, policy, ctx)
if decision.signal == SellSignal.SHORT_TERM_HIGH:
    # 매도 실행
```

## 운영 통합 상태

| 항목 | 운영 daemon 적용 | 비고 |
|---|---|---|
| Step 1+2 단기 고점 매도 | ✅ 적용 (commit eac68e0) | 익절 구간 도달 시 1분봉 fetch + 평가 |
| Step 3 1분봉 F존 매수 | ⚪ 인프라만 도입 | daemon 통합은 별도 PR (API 부담 검토 후) |

## 제한·한계

1. **142280 시초가 폭등 패턴 미인식** — 5단계 일반 패턴 안 맞음. 별도 모듈 필요 (시초가 +20% 종목 진입 차단·역추격 모듈)
2. **1분봉 fetch API 부담** — 모든 picker 종목 매 사이클 fetch 는 부담. 익절 구간 도달 종목만 fetch (현재 daemon 구현)
3. **v2 튜닝값 광범위 검증 필요** — 274090·080220 외 다른 종목·시점 누적 검증

## 후속 후보

- **운영 daemon 1분봉 F존 매수 통합** — picker top 종목 fetch_minute → FZoneIntraday 평가 → 일봉 신호와 병행
- **시초가 폭등 종목 진입 차단** (P10) — 09:00 시초가 +N% 종목 제외
- **5분봉 F존** — 1분봉 노이즈 회피 + 일봉 너무 느린 중간 timeframe

## 참조

- 5/20 검증 보고: [[2026-05-21-0520-live-trade-verification]]
- F존/SF존 로직: [[bar-46-47-f-sf-zone-deep-analysis]]
- 사용자 차트 이미지: 142280 5/20 09:15·09:18·09:30·09:43 패턴
