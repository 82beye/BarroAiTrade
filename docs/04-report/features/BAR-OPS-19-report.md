# BAR-OPS-19 — 일일 markdown 리포트 자동 생성

## 목적
운영자 일일 모니터링 — CSV 보다 가독성 높은 markdown 표 + 5 섹션 종합 리포트.

## 산출
- `backend/core/journal/markdown_report.py` — 6 렌더 함수:
  - `render_leader_table(leaders)` — 주도주 표 (rank·symbol·flu%·rank들·score)
  - `render_simulation_summary(trades, pnl, per_strategy)` — 시뮬 요약
  - `render_gate_recommendations(gate)` — 잔고 + 추천 qty
  - `render_history_by_strategy(entries)` — 전략별 누적 (PnL 내림차순)
  - `render_history_by_run(entries)` — 실행별 시계열
  - `render_daily_report(...)` — 5 섹션 통합 리포트
- `scripts/generate_daily_report.py` — CSV history → markdown CLI
- `backend/tests/journal/test_markdown_report.py` — 7 cases

## 실 검증

```bash
$ python scripts/generate_daily_report.py data/simulation_log.csv \
    --output reports/2026-05-08.md \
    --title "2026-05-08 일일 시뮬 리포트"

📝 markdown 리포트 → reports/2026-05-08.md (556 bytes)
```

생성 결과 (발췌):
```markdown
# 2026-05-08 일일 시뮬 리포트
_생성: 2026-05-08T14:23:40+00:00_
_총 15 entries / data/simulation_log.csv_

## 전략별 누적
| strategy | runs | trades | win% | total_pnl |
|----------|-----:|-------:|-----:|----------:|
| swing_38 | 3 | 66 | 72.7% | +7,505,290 |
| f_zone | 3 | 0 | 0.0% | +0 |
...
```

## 운영 시나리오

### 매일 cron (시뮬 + 리포트 자동)
```bash
0 16 * * 1-5 cd /path/to/repo && \
  set -a; . ./.env.local; set +a; \
  python scripts/simulate_leaders.py --top 5 --min-score 0.5 \
    --check-balance --execute \
    --log data/simulation_log.csv \
    --audit-log data/order_audit.csv && \
  python scripts/generate_daily_report.py data/simulation_log.csv \
    --output "reports/$(date +%F).md" \
    --title "$(date +%F) 일일 시뮬 리포트"
```

### slack/email 통보 통합 (외부 도구)
```bash
# markdown 생성 → slack-cli 또는 mailx 로 전송
python scripts/generate_daily_report.py ... --output today.md
slack-cli post --channel '#trading-daily' --file today.md
```

## Tests
- 7 신규 / 회귀 **719 → 726 (+7)**, 0 fail

## 다음
- BAR-OPS-20 — slack/email 통보 자동화
- BAR-OPS-21 — frontend dashboard 페이지 (markdown → React)
- BAR-OPS-22 — WebSocket 실시간 시세 (`/api/dostk/ws/ticker`)
