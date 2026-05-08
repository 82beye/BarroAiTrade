# BAR-OPS-29 — 시뮬 vs 실현 PnL 비교 분석 (`/diff`)

## 목적
시뮬 (예측) PnL vs 실현 PnL 매칭 → 전략 정확도 + bias 측정.

## 분류 정책

| 시뮬 | 실현 | 분류 |
|------|------|------|
| > 0 | ≥ 시뮬 × 0.80 | 양호 |
| > 0 | < 시뮬 × 0.80 | 과대 시뮬 (예측 너무 낙관) |
| < 0 | ≥ 시뮬 × 1.20 | 양호 (실 손실 ≤ 예측 +20%) |
| < 0 | < 시뮬 × 1.20 | 과소 시뮬 (실 손실 더 큼) |
| = 0 | any | 신호 없음 |

## 산출
- `backend/core/journal/pnl_diff.py`:
  - `SymbolDiff` (frozen) — symbol·sim_pnl·real_pnl·diff·diff_pct·bias 등
  - `compare(sim_entries, real_entries)` → 종목별 매칭, abs(diff) 내림차순 정렬
  - `summarize(diffs)` → total_sim/real/diff + bias_counts
  - `_bias(sim, real)` — 4가지 분류 로직
- `scripts/run_telegram_bot.py`:
  - `/diff` 명령 — 12 종목 비교 + 차이 큰 5종목
- `backend/tests/journal/test_pnl_diff.py` — 8 cases

## 실 검증

```
$ /diff
🔍 시뮬 vs 실현 (12 종목)
시뮬 합계: +7,505,290
실현 합계: -284,487
차이:     -7,789,777
_과대 시뮬: 2 / 양호: 1 / 신호 없음: 9_

*차이 큰 5종목*
005380 현대차       sim=+7,400,500 real=+0 (-100%) 과대 시뮬
001440 대한전선      sim=+222,520   real=+0 (-100%) 과대 시뮬
319400 현대무벡스    sim=-117,730   real=+0 (+100%) 양호
042940 상지건설      sim=+0         real=-87,278 (-) 신호 없음
009410 태영건설      sim=+0         real=-41,563 (-) 신호 없음
```

→ 시뮬 종목과 실 거래 종목이 거의 다름. 실 운영 시 시뮬 추천만 매수하면 정확도 비교 가능.

## 운영 가치
- 매주 `/diff` 호출 → 시뮬 정확도 추적
- 과대 시뮬 종목 다수 → 점수 threshold 상향 필요 (min_score 0.5 → 0.7)
- 과소 시뮬 다수 → 손절 한도 보수적 조정 (SL -2% → -1.5%)
- 양호 비율 ≥ 80% → 자금 한도 점진 확대 가능

## 텔레그램 명령 누적 (15개)

| 카테고리 | 명령 |
|----------|------|
| 메타 | /help /ping |
| 조회 | /balance /history /sim /eval /audit /pnl **/diff** |
| 매수 | /sim_execute → /confirm |
| 매도 | /sell_execute → /confirm_sell |
| 공통 | /cancel |

## Tests
- 8 신규 / 회귀 **787 → 795 (+8)**, 0 fail

## 다음
- BAR-OPS-30 — 자동 정책 조정 (diff 결과 기반 min_score / SL 자동 튜닝)
- BAR-OPS-31 — WebSocket 실시간 시세
- BAR-OPS-32 — Frontend 대시보드
