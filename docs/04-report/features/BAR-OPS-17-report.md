# BAR-OPS-17 — LiveOrderGate (실전 진입 안전 게이트)

## 목적
`api.kiwoom.com` (실전) 호출 시 사고 방지.
**BAR-64 Kill Switch / BAR-68 audit log 정식 도입 전 임시 보호막.**

## 4중 안전 검증

| 단계 | 정책 | 차단 예외 |
|------|------|-----------|
| 1 | `LIVE_TRADING_ENABLED` 환경변수 강제 (값=truthy 필요) | `TradingDisabled` |
| 2 | 일일 손실 한도 (`-3%` 기본) — 매수만 차단, 매도(손절) 허용 | `DailyLossLimitExceeded` |
| 3 | 일일 거래수 한도 (`50건` 기본) | `DailyOrderLimitExceeded` |
| 4 | 모든 시도 audit.csv append (BLOCKED / DRY_RUN / ORDERED / FAILED) | — |

## 산출
- `backend/core/risk/live_order_gate.py`:
  - `LiveOrderGate` — `KiwoomNativeOrderExecutor` wrapper
  - `place_buy(symbol, qty, price=None, daily_pnl_pct=0)`
  - `place_sell(symbol, qty, price=None, daily_pnl_pct=0)`
  - `GatePolicy` (frozen dataclass) — 한도 / env_flag_name 조정 가능
  - 에러 클래스 3종 (모두 `RuntimeError` 상속)
- `backend/tests/risk/test_live_order_gate.py` — 8 cases

## 실 검증

```python
# 1) ENV flag 미설정 → 강제 차단
os.environ.pop('LIVE_TRADING_ENABLED', None)
exec = KiwoomNativeOrderExecutor(oauth=o)             # dry_run=False
gate = LiveOrderGate(executor=exec, audit_path='audit.csv')
await gate.place_buy('005930', 1)
# → TradingDisabled: LIVE_TRADING_ENABLED=truthy 필요 (현재: '')

# 2) dry_run=True → 통과
exec_dry = KiwoomNativeOrderExecutor(oauth=o, dry_run=True)
gate = LiveOrderGate(executor=exec_dry, audit_path='audit.csv')
r = await gate.place_buy('005930', 10)
# → OrderResult(order_no='DRY_RUN', dry_run=True)
```

audit.csv:
```
ts,action,side,symbol,qty,price,order_no,return_code,blocked,reason
2026-05-08T14:14:38+00:00,BLOCKED,buy,005930,1,MKT,,,1,LIVE_TRADING_ENABLED=truthy 필요...
2026-05-08T14:14:38+00:00,DRY_RUN,buy,005930,10,MKT,DRY_RUN,0,0,
```

## 운영 시나리오

### 모의(mockapi) — 자유로운 테스트
```bash
# env flag 불필요 (executor.dry_run=True 또는 mockapi 베이스)
python scripts/simulate_leaders.py --check-balance ...
```

### 실전(api.kiwoom.com) 진입 시 — 4중 검증
```bash
# 1) 명시적 활성화 (오타 방지)
export LIVE_TRADING_ENABLED=true

# 2) 정책 조정 (보수적)
# 코드: GatePolicy(daily_loss_limit_pct=Decimal("-1.5"), daily_max_orders=10)

# 3) 매 주문마다 daily_pnl_pct 전달 → 손실 한도 자동 차단

# 4) audit.csv 정기 검토 (BLOCKED reason 분석)
```

## 보안
- ✅ env-flag 명시적 활성화 (CWE-1234 Improper Restriction of Auth Mechanisms 방어)
- ✅ audit append-only (CSV — 추후 BAR-68 hash chain 연결 가능)
- ✅ 매도(손절) 는 손실 한도 무관 — 시장 사고 시 출구 보장
- ✅ 모든 BLOCKED / FAILED 도 audit 기록 — 감사 무결성

## Tests
- 8 신규 / 회귀 **711 → 719 (+8)**, 0 fail

## End-to-end 운영 흐름 (OPS-08~17)
```
주도주 선정 (OPS-11/12)
  ↓
시뮬 (OPS-08)
  ↓
영속 (OPS-13)
  ↓
잔고 (OPS-15) → 자금 한도 정책 (OPS-16) → 추천 qty
  ↓
LiveOrderGate (OPS-17) ← 4중 안전망
  ↓
KiwoomNativeOrderExecutor (OPS-14)
  ↓
DRY_RUN 또는 실 주문
```

## 다음
- BAR-OPS-18 — simulate_leaders.py 에 LiveOrderGate 통합 (`--execute` 옵션)
- BAR-OPS-19 — WebSocket 실시간 fill 추적
- BAR-64/68 정식 도입 시 LiveOrderGate 흡수
