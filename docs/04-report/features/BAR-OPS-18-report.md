# BAR-OPS-18 — simulate_leaders.py `--execute` 통합 (End-to-End 자동화 마침표)

## 목적
OPS-08~17 까지 누적된 모든 단계를 **한 명령**으로 실행. 시뮬→영속→정책→추천→주문 자동 흐름.

## CLI 변경
```bash
python scripts/simulate_leaders.py \
  --top 5 --min-score 0.5 \
  --check-balance --max-per-position 0.30 --max-total-position 0.90 \
  --execute \
  --audit-log data/order_audit.csv \
  --daily-loss-limit -3.0 --daily-max-orders 50 \
  --log data/simulation_log.csv
```

신규 옵션:
- `--execute` — 추천 qty LiveOrderGate 로 실 주문
- `--dry-run` (기본 True) / `--no-dry-run` (실 주문, LIVE_TRADING_ENABLED 필요)
- `--audit-log` (기본 `data/order_audit.csv`)
- `--daily-loss-limit` / `--daily-max-orders` — GatePolicy 조정

## End-to-end 검증 (mockapi.kiwoom.com, 2026-05-08)

```
== 시뮬 (5 전략, 3 종목) ==
swing_38: +7,505,290 PnL

== 잔고 + 한도 + 추천 ==
예수금 48.9M, 한도 per 30% / total 90%
319400 현대무벡스 389주 / 001440 대한전선 203주 / 005380 현대차 23주

== 주문 실행 (LiveOrderGate, dry_run=True) ==
[DRY_RUN] 319400 현대무벡스    qty=389 order_no=DRY_RUN
[DRY_RUN] 001440 대한전선      qty=203 order_no=DRY_RUN
[DRY_RUN] 005380 현대차       qty= 23 order_no=DRY_RUN

→ 실행 3 건 / audit: data/order_audit.csv
```

audit.csv:
```csv
ts,action,side,symbol,qty,price,order_no,return_code,blocked,reason
2026-05-08T14:17:26+00:00,DRY_RUN,buy,319400,389,MKT,DRY_RUN,0,0,
2026-05-08T14:17:26+00:00,DRY_RUN,buy,001440,203,MKT,DRY_RUN,0,0,
2026-05-08T14:17:26+00:00,DRY_RUN,buy,005380,23,MKT,DRY_RUN,0,0,
```

## 운영 시나리오

### 매일 cron (모의 환경 자동화)
```bash
0 16 * * 1-5 cd /path/to/repo && \
  set -a; . ./.env.local; set +a; \
  python scripts/simulate_leaders.py --top 5 --min-score 0.5 \
    --check-balance --execute \
    --log data/simulation_log.csv \
    --audit-log data/order_audit.csv
```

### 실전(api.kiwoom.com) 단계적 진입
```bash
# 1) 모의에서 1~2주 검증 후
export LIVE_TRADING_ENABLED=true
export KIWOOM_BASE_URL=https://api.kiwoom.com

# 2) 첫 1주 — 보수적 한도, 작은 자금
python scripts/simulate_leaders.py --top 3 --min-score 0.7 \
  --max-per-position 0.10 --max-total-position 0.30 \
  --daily-loss-limit -1.5 --daily-max-orders 10 \
  --check-balance --execute --no-dry-run

# 3) audit.csv 매일 점검 후 한도 점진 확장
```

## ⚠️ 실전 진입 전 필수 통합
LiveOrderGate 가 임시 보호막이지만 정식 운영에는 다음 모두 필요:
- BAR-64 Kill Switch / Circuit Breaker (시세 단절 / 슬리피지 폭증 자동 차단)
- BAR-68 MFA + audit hash chain (감사 무결성)
- 미체결 조회 (kt00004 body 조정)
- WebSocket 실시간 fill 추적

## Tests
- 신규 0 / 회귀 **719 passed**, 0 fail (CLI 변경, executor 자체는 OPS-14/17 테스트로 검증)

## 누적 OPS 트랙 통계
| | 시작 | 현재 |
|---|---|---|
| BAR | 0 | **18 (OPS-01~18)** |
| Tests | 240 | **719** (+479) |
| PR | 134 | **152** (예정) |
| 신규 코드 | 0 | ~6,500 줄 (gateway/risk/journal/scripts) |
| 사용 키움 API TR-ID | 0 | **8** (oauth, ka10081/80/32/27/30, kt00018/00001, kt10000/01) |
