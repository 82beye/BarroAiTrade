# BAR-OPS-30 — 정책 자동 튜닝 추천 (`/tune`)

## 목적
OPS-29 diff bias_counts → min_score / SL / max_per_position 조정 자동 추천.
**자동 적용 X** — 사용자가 검토 후 simulate_leaders 옵션으로 수동 반영.

## 추천 로직 (신호 종목 기준)

| 정책 | 트리거 | 액션 | severity |
|------|--------|------|----------|
| min_score | 과대 시뮬 ≥ 50% | +0.1 (cap 0.9) | warn |
| min_score | 양호 ≥ 80% + current ≥ 0.30 | -0.1 | info |
| stop_loss | 과소 시뮬 ≥ 30% | +0.5 (보수화: -2.0 → -1.5) | critical |
| max_per_position | 양호 ≥ 80% + 신호 ≥ 5 | +0.05 (cap 0.50) | info |

신호 종목 = 양호 + 과대 시뮬 + 과소 시뮬 (신호 없음 제외).

## 산출
- `backend/core/journal/policy_tuner.py`:
  - `PolicyRecommendation` (frozen)
  - `recommend_min_score / recommend_stop_loss / recommend_max_per_position`
  - `tune_all(bias_counts)` — 3 정책 일괄
- `scripts/run_telegram_bot.py` — `/tune` 핸들러
- `backend/tests/journal/test_policy_tuner.py` — 14 cases

## 실 검증

```
$ /tune
📋 정책 튜닝 추천 (1건)
⚠️ min_score: 0.5 → 0.6
   과대 시뮬 비율 67% — 시뮬 점수 임계 상향

_적용: simulate_leaders --min-score / --max-per-position 등_
```

mockapi 데이터 (시뮬 vs 실현 12 종목, 신호 3) 기준 — **과대 시뮬 2/3 = 67%** → min_score 상향 추천 정확 발동.

## 운영 흐름

```
매일 시뮬 (OPS-08)
    ↓
영속 (OPS-13)
    ↓ 1주 누적
주간: /diff (OPS-29) → bias_counts 추출
    ↓
주간: /tune (OPS-30) → 3 정책 추천
    ↓
운영자 검토
    ↓
다음 주: simulate_leaders --min-score 0.6 ... 반영
```

## 텔레그램 명령 누적 (16개)

| 카테고리 | 명령 |
|----------|------|
| 메타 | /help /ping |
| 조회 | /balance /history /sim /eval /audit /pnl /diff **/tune** |
| 매수 | /sim_execute → /confirm |
| 매도 | /sell_execute → /confirm_sell |
| 공통 | /cancel |

## Tests
- 14 신규 / 회귀 **795 → 809 (+14)**, 0 fail

## 다음
- BAR-OPS-31 — WebSocket 실시간 시세
- BAR-OPS-32 — 자동 정책 적용 (config 파일 update)
- BAR-OPS-33 — Frontend 대시보드
