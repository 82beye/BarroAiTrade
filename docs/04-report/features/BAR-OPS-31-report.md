# BAR-OPS-31 — 정책 config 영속 + `/tune apply` 자동 반영

## 변경
- `PolicyConfig` (dataclass): min_score / stop_loss_pct / take_profit_pct / max_per_position / max_total_position / daily_loss_limit / daily_max_orders + history
- `PolicyConfigStore`: JSON 영속 + history append (최근 50건)
- `apply(recommendations)`: PolicyRecommendation list → config update
  - 동일값은 skip (no history)
  - unknown field 무시 (forward compat)
- `/tune apply` 명령 — 추천 즉시 반영
- `/policy` 명령 — 현재 정책 + 최근 3 변경 표시
- `/tune` 추천이 현재 config 값 기준 (이전: 하드코딩 0.5)

## 산출
- `backend/core/journal/policy_config.py`
- `scripts/run_telegram_bot.py`: `/policy`, `/tune apply`
- `backend/tests/journal/test_policy_config.py` — 8 cases

## 실 검증

```
$ /tune apply
✅ 정책 적용됨 (1건)
min_score: 0.5 → 0.6  (warn)
_저장: data/policy.json_

$ /policy
📜 현재 정책
min_score: 0.6
stop_loss: -2.0%
take_profit: 5.0%
max_per_position: 30%
max_total_position: 90%
daily_loss_limit: -3.0%
daily_max_orders: 50

*최근 변경* (1/50)
2026-05-08T15:51:59 min_score: 0.5 → 0.6
```

→ 모바일 텔레그램 한 줄로 정책 변경 + 자동 영속 + 이력 추적.

## 학습 루프 자동화 (완성)

```
시뮬 → 영속 → 실현 → diff → tune 추천
                              ↓
                         /tune apply
                              ↓
                  data/policy.json 자동 update
                              ↓
                  다음 simulate_leaders --min-score 0.6
                  (또는 PolicyConfig.load() 자동 로드)
```

## 데이터 모델

```json
{
  "min_score": 0.6,
  "stop_loss_pct": -2.0,
  "take_profit_pct": 5.0,
  "max_per_position": 0.3,
  "max_total_position": 0.9,
  "daily_loss_limit": -3.0,
  "daily_max_orders": 50,
  "history": [
    {
      "timestamp": "2026-05-08T15:51:59+00:00",
      "source": "tune_telegram",
      "changes": [
        {"field": "min_score", "old": 0.5, "new": 0.6,
         "reason": "과대 시뮬 67%", "severity": "warn"}
      ]
    }
  ]
}
```

## 텔레그램 명령 누적 (17개)

| 카테고리 | 명령 |
|----------|------|
| 메타 | /help /ping |
| 조회 | /balance /history /sim /eval /audit /pnl /diff /tune **/policy** |
| 매수 | /sim_execute → /confirm |
| 매도 | /sell_execute → /confirm_sell |
| 정책 | **/tune apply** |
| 공통 | /cancel |

## Tests
- 8 신규 / 회귀 **809 → 817 (+8)**, 0 fail

## 다음
- BAR-OPS-32 — simulate_leaders 가 PolicyConfig 자동 로드 (CLI 옵션 미지정 시)
- BAR-OPS-33 — WebSocket 실시간 시세
- BAR-OPS-34 — Frontend 대시보드
