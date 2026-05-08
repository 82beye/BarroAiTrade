# BAR-OPS-20 — 보유 종목 매도 시그널 평가 + 자동 매도

## 목적
OPS-08~19 가 매수 자동화. **매도(청산) 자동화** 가 빠짐. 보유 종목 손익률 → TP/SL 시그널.

## 정책
| signal | 조건 | 행동 |
|--------|------|------|
| TAKE_PROFIT | pnl_rate ≥ TP (기본 +5%) | 익절 추천 |
| STOP_LOSS | pnl_rate ≤ SL (기본 -2%) | 손절 추천 |
| HOLD | SL < pnl_rate < TP | 보유 유지 |

## 산출
- `backend/core/risk/holding_evaluator.py`:
  - `ExitPolicy(take_profit_pct, stop_loss_pct)` (frozen)
  - `evaluate_holding(h, policy) → HoldingDecision`
  - `evaluate_all(holdings, policy) → list[HoldingDecision]`
  - `render_decisions_table(decisions)` — markdown ✅TP/🛑SL/HOLD 표
- `scripts/evaluate_holdings.py`:
  - `--tp` / `--sl` 정책 조정
  - `--auto-sell` — TP/SL 도달 종목 LiveOrderGate 매도
  - `--dry-run` (기본) / `--no-dry-run` 안전 게이트
  - `--audit-log` audit CSV append
- `backend/tests/risk/test_holding_evaluator.py` — 9 cases (경계값 4 + 다중·custom·markdown)

## 실 검증
```bash
$ python scripts/evaluate_holdings.py --tp 5.0 --sl -2.0
보유 종목 없음.
```
모의 계좌 신규 상태 — 보유 0. 코드 자체는 9 unit test 로 모든 시나리오 검증:
- TP threshold (5.0%, 5.0% 정확 / 6.35% 초과)
- SL threshold (-2.0%, -2.0% 정확 / -3.5% 초과)
- HOLD (1.5% / 0.0%)
- custom policy (보수적 +3%/-1%)
- markdown 출력 ✅TP/🛑SL

## 운영 시나리오

### 매일 시뮬 후 매도 평가 (cron 결합)
```bash
# 1) 시뮬 + 매수 추천 + DRY_RUN
python scripts/simulate_leaders.py --top 5 --check-balance --execute \
  --log data/simulation_log.csv --audit-log data/order_audit.csv

# 2) 보유 종목 매도 평가 + DRY_RUN
python scripts/evaluate_holdings.py --tp 5.0 --sl -2.0 --auto-sell \
  --audit-log data/order_audit.csv

# 3) 일일 리포트 markdown
python scripts/generate_daily_report.py data/simulation_log.csv \
  --output "reports/$(date +%F).md"
```

### 시간대별 호출 (보수적)
```bash
# 09:30 시뮬 + 매수 (보수적)
python scripts/simulate_leaders.py --top 3 --min-score 0.7 \
  --max-per-position 0.10 --max-total-position 0.30 --execute

# 매시간 보유 종목 평가
0 10-15 * * 1-5 python scripts/evaluate_holdings.py --tp 3.0 --sl -1.5 --auto-sell

# 15:20 강제 청산 (장 마감 5분 전, 모든 종목 시장가 매도)
20 15 * * 1-5 python scripts/evaluate_holdings.py --tp -100 --sl 100 --auto-sell
# → tp/sl 무조건 도달 → 모든 종목 매도
```

## OPS-08~20 누적 흐름
```
[매수 사이클]
  주도주 (OPS-11/12) → 시뮬 (OPS-08) → 영속 (OPS-13) →
  잔고 (OPS-15) → 정책 (OPS-16) → 추천 → LiveOrderGate (OPS-17) → 매수 (OPS-14)

[매도 사이클] ← This BAR
  잔고 (OPS-15) → ExitPolicy (OPS-20) → TP/SL 시그널 →
  LiveOrderGate (OPS-17) → 매도 (OPS-14)

[리포트]
  CSV (OPS-13) → markdown (OPS-19)
```

## Tests
- 9 신규 / 회귀 **726 → 735 (+9)**, 0 fail

## 다음
- BAR-OPS-21 — WebSocket 실시간 시세 → 시그널 트리거 빈도 ↑
- BAR-OPS-22 — Slack 통보 (TP/SL 발생 즉시 알림)
- BAR-OPS-23 — 일자별 실현손익 (ka10073) 누적
