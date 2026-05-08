# BAR-OPS-32 — CLI PolicyConfig 자동 로드

## 변경
- `scripts/simulate_leaders.py` — `_run()` 시작 시 PolicyConfig 자동 로드:
  - CLI 옵션이 default 값일 때만 config 사용 (사용자 명시 시 override)
  - min_score / max_per_position / max_total_position / daily_loss_limit / daily_max_orders
- `scripts/evaluate_holdings.py` — 동일 패턴:
  - tp / sl

## 정책 우선순위
```
1. CLI 명시 옵션 (--min-score 0.7) ← 최우선
2. data/policy.json (OPS-31 적용값)
3. CLI default (cfg 미존재 시)
```

## 학습 루프 완전 자동화 (4단계)

```
주1회 분석 (Telegram):
  /diff           ← bias_counts 생성
  /tune apply     ← policy.json update + history

매일 cron:
  python scripts/simulate_leaders.py --top 5 --check-balance --execute
       ↑
       옵션 미지정 → policy.json 의 min_score 0.6 자동 적용
       ↑
       다음 주 다시 /diff → /tune apply → 점진 학습
```

## 실 검증

```python
# 이전 OPS-31 에서 /tune apply 로 min_score 0.5 → 0.6 적용
$ cat data/policy.json | jq .min_score
0.6

# simulate_leaders.py 옵션 미지정으로 실행
$ python scripts/simulate_leaders.py --top 3
== 당일 주도주 선정 (mode=daily, top=3, min_flu=1.0%, min_score=0.6) [policy.json 로드됨] ==
                                                            ^^^ 자동 로드
```

## 운영 효과
- 운영자가 매일 CLI 옵션 변경 X
- 텔레그램 한 줄 (`/tune apply`) → 다음 시뮬부터 자동 반영
- 정책 변경 이력 자동 추적 (history)

## Tests
- 신규 0 (CLI 통합은 직접 실행 검증)
- 회귀 **817 passed**, 0 fail (변경 없음)

## 학습 루프 cron 예시
```bash
# 매일 09:30 — 매수 사이클 (policy.json 자동)
30 9 * * 1-5 python scripts/simulate_leaders.py --top 5 \
  --check-balance --execute --telegram --log data/simulation_log.csv

# 매주 월요일 08:00 — 정책 자동 튜닝
0 8 * * 1 python -c "
import asyncio
from scripts.run_telegram_bot import _cmd_tune
asyncio.run(_cmd_tune(None, {'chat':{'id':$TELEGRAM_CHAT_ID},'text':'/tune apply'}))
"
```

→ **사람 개입 없이 자기학습 운영 시스템 가능.**

## 다음
- BAR-OPS-33 — WebSocket 실시간 시세
- BAR-OPS-34 — Frontend 대시보드
- BAR-OPS-35 — A/B 테스트 (정책 v1 vs v2 동시 시뮬)
