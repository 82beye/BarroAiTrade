# BAR-OPS-36 — Runner (승자 보유 → 최고점 청산)

_작성: 2026-06-09 / 브랜치: `feat/BAR-OPS-36-runner` / 근거: reports/2026-06-08 (459550 1차를 고점 2,385까지 보유 시 +101K 추가)_

## 목적

고정 익절(+5%)이 **상한가·강한 추세의 초과 수익**을 잘라먹는 문제 해결. 익절가를 '즉시 매도'가
아니라 **러너 모드 진입 트리거**로 전환해, **추세가 무너질 때만 최고점 부근에서 청산**한다.
대상: supertrend 자동매매(`SupertrendAutoTrader`). 전부 default OFF(no-op), shadow→enforce.

## 설계 — 상태/로직

### 러너 진입 트리거 (셋 중 하나, `_runner_triggered`)
1. **TP 도달**: 현재가 ≥ entry×(1+`take_profit_pct`%) — 익절가 도달 시 매도 대신 러너 전환
2. **상한가**: 현재가 ≥ 전일종가×(1+`runner_limit_up_pct`%, 기본 29%) — KRX ~+30% 상한 근접 포착
3. **시초 갭상승**: 보유종목 당일 시가 ≥ 전일종가×(1+`runner_gap_up_pct`%) — 갭 모멘텀 동행

### 러너 청산 판정 (`_runner_should_exit`, 우선순위)
1. **상한가 잠김 → 홀딩**: 현재가가 상한가권이면 되돌림 무시하고 보유 ("상한가에서 안 판다")
2. **수익잠금 floor**: 현재가 ≤ entry×(1+`runner_profit_lock_pct`%, 기본 +2%) → 청산 (승자→손실 방지)
3. **최고점 되돌림(추세이탈)**: 현재가 ≤ peak×(1−`runner_giveback_pct`%, 기본 3%) 또는 peak−`runner_giveback_atr_mult`×ATR → 청산
4. (러너 홀딩 중에도) **ST SELL 전환** = 추세 반전 → 청산

### 외곽 안전망 (러너보다 우선)
- BAR-OPS-35 **하드 손절**(`hard_stop_pct`)·**ATR 트레일**(`trail_atr_mult`)이 먼저 잡으면 러너 평가 생략.
- 즉 청산 우선순위: 트레일 > 하드손절 > (러너: 상한가홀딩 > 수익잠금 > 고점되돌림) > 고정익절 > ST SELL.

## 신규 config 플래그 (SupertrendAutoConfig, 전부 default OFF)

| 플래그 | default | 의미 |
|--------|:---:|------|
| `runner_enabled` | False | 마스터 스위치 |
| `runner_limit_up_pct` | 29.0 | 전일종가 대비 % 이상이면 상한가(트리거+홀딩). 0=비활성 |
| `runner_gap_up_pct` | 0.0 | 보유종목 시초 갭상승 % 이상이면 러너. 0=비활성 |
| `runner_giveback_pct` | 3.0 | 최고종가 대비 되돌림 % → 추세이탈 청산. 0이면 ATR 사용 |
| `runner_giveback_atr_mult` | 0.0 | >0 이면 peak−mult×ATR (giveback_pct 대신) |
| `runner_profit_lock_pct` | 2.0 | 러너 진입 후 최소 보장 수익(이 밑이면 청산) |
| `runner_gap_partial_ratio` | 0.0 | 익일 시가갭에서 매도할 비율(0.5=절반). 0=비활성 |
| `runner_gap_partial_min_pct` | 3.0 | 익일 시가갭(전일종가比) ≥ 이 %여야 부분익절 |
| `runner_gap_partial_window_bars` | 6 | 개장 후 이 봉수(×5분) 이내만(갭은 개장 현상). 6=30분 |

> `runner_enabled=False`(기본) 이면 `_runner_triggered`·`_maybe_gap_partial` 항상 비활성 → 기존 동작(고정 익절 즉시매도) 완전 보존.

## 익일 시가 갭 부분익절 (`_maybe_gap_partial`)

**근거 (일봉 분석 2026-06-09, 상한가 익일 531건)**: 79%가 갭상승·평균 **+8.3%**, 그러나 **47%가 장중 페이드**(시가→종가 평균 -3.6%). 동시에 익일 고가 평균 +18.6%·19%는 또 상한가 → **시가 갭에서 일부 확정(신뢰구간 +8.3%) + 잔량 peak-trail(꼬리 +18.6% 포착)** 이 최적 +EV.

**동작**: 보유(오버나잇) supertrend 종목이 익일 **개장 초반(window_bars 이내)** 에 시가갭 ≥ `min_pct` 이고 현재가 > 진입가면, 보유의 `runner_gap_partial_ratio` 만큼 즉시 매도(확정)하고 **잔량은 포지션에 남겨 러너로 런**. `partial_tp_done` 마킹으로 1회만. 조건 충족 사이클은 잔량 청산평가를 스킵(같은 봉 중복매도 방지).

**조건 (전부 충족)**: `runner_enabled` + `gap_partial_ratio>0` · `partial_tp_done==False` · 오늘 봉수 ≤ window_bars · 진입일 < 오늘(오버나잇) · 시가갭 ≥ min_pct · 현재가 > 진입가.

**잔량 표현**: 부분매도 후 `total_recommended_qty`를 잔량으로 갱신 + `upsert`(remove 아님). supertrend 청산은 `total_recommended_qty` 우선이라 이후 전량청산이 잔량만 매도. (tranche 세부는 supertrend 경로 미사용이라 그대로 — 데이터정합성 무영향.)

## 동작 예 (6/8 459550 1차)

- 실제: 12:55 @2,110 진입 → 13:47 @2,330 매도(+10.4%). 당일 고점 2,385(14:05).
- 러너 ON (giveback 3%): TP/추세 유지 동안 홀딩 → 고점 2,385 형성 후 2,385×0.97=2,314 되돌림 시 청산
  → 약 2,314 청산(실제 2,330과 유사~상회, 고점 추종). 상한가까지 갔으면 '상한가 홀딩'으로 더 보유.
- 핵심: **추세 유지 시 고점 동행, 무너지면 peak−3%에서 청산**. 고정 익절보다 초과 수익 포착.

## 한계 / 운영

- **5분봉 종가 기준**: 되돌림·상한가 판정이 5분봉 close 기반 → 봉 내 급변은 다음 봉까지 지연(매매복기의
  실시간/공백복귀 손절 권고와 동일 한계). 외곽 하드손절이 catastrophic 보호.
- **상한가 잠김 판정**: 현재가 ≥ 전일종가×1.29 단순 기준(틱·연속잠김 정밀판정은 후속). 상한가 풀리면
  giveback/floor 로 자동 전환되어 하방 보호됨.
- **전량 러너**: 분할 익절(일부 TP 확정 + 잔량 러너)은 미구현 — 후속 옵션. 현재는 수익잠금 floor 로 하방 보장.

## shadow → enforce 권장 순서

1. `runner_enabled=True` + `runner_giveback_pct=3.0` + `runner_profit_lock_pct=2.0` (기본 러너).
2. `runner_limit_up_pct=29.0` (상한가 홀딩) — 상한가 종목 초과수익.
3. `runner_gap_up_pct=5.0` (보유 갭상승 동행) — shadow 진입/청산 관측 후.
4. **`runner_gap_partial_ratio=0.5` + `runner_gap_partial_min_pct=3~5`** — 상한가 오버나잇 보유 시 익일
   시가 갭에서 절반 확정(+8.3% 신뢰) + 잔량 러너. 분석상 가장 높은 +EV 조합.
> 활성화 후 `scripts/_daily_strategy_audit.py` 로 평균 청산수익률·peak 대비 청산위치·승자 평균보유시간·
> 부분익절 확정수익 측정.

## 검증

- 신규 테스트 16건(트리거 3 + 청산판정 4 + 러너 통합 2 + **갭 부분익절 7**) + 기존 51건 =
  `test_supertrend_auto_trader.py` **67 passed**.
- 전체 backend: 1325 passed (러너 무관 기존 실패 1건 = origin BAR-OPS-35 ka10075 테스트 미갱신, 별도).
- `runner_enabled=False` 기본에서 기존 동작 완전 불변(회귀 0).
