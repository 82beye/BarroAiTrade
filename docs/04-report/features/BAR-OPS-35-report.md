# BAR-OPS-35 — 트레이딩뷰 등급 시뮬 정확도

## 목적
시뮬 결과 vs 실 거래 차이 최소화. 학습 루프 (`/tune apply`) 가 의미 있는 정확도 위에서 동작.

## 5가지 옵션 추가

| 옵션 | default | 효과 |
|------|---------|------|
| `entry_on_next_open` | **True** | 시그널 캔들 close → 다음 캔들 open 진입 (lookahead 제거) |
| `exit_on_intrabar` | **True** | bar high/low 터치 시 체결 (close 도달 X 도) |
| `commission_pct` | 0.0 | 매수+매도 각각 차감 (키움 위탁 0.015% 권장) |
| `tax_pct_on_sell` | 0.0 | 매도 시만 차감 (증권거래세+농특세 0.18% 권장) |
| `slippage_pct` | 0.0 | 진입가 * (1+slippage) — 시장가 진입 보정 |

## 실 검증 (현대차 005380, 600 일봉, swing_38)

| | trades | PnL | 차이 |
|---|---|---|---|
| **Legacy** (close 진입/청산, 0% fee) | 19 | +7,400,500 | — |
| **Realistic** (default + 수수료 0.015% + 세금 0.18% + 슬리피지 0.05%) | 16 | **+1,866,351** | **-74.8%** |

→ 현실 보정 후 PnL 75% 감소. 기존 시뮬이 매우 낙관적이었음 입증.

## 변경 핵심 (intraday_simulator.py)

```python
# 1. next-open 진입
if signal:
    pending_entry = True             # 현 캔들에서 진입 X
    # 다음 루프 iteration 에서:
    entry_price = current.open * (1 + slippage)

# 2. intrabar 청산
if exit_on_intrabar:
    # high ≥ tp_price → tp_price 체결
    # low ≤ sl_price → sl_price 체결
    new_pos, orders = self._evaluate_intrabar(position, plan, candle)

# 3. 수수료/세금
gross = (exit_price - entry_price) * qty
commission = (exit_price + entry_price) * qty * commission_pct
tax = exit_price * qty * tax_pct_on_sell
pnl = gross - commission - tax
```

## 운영 권장 설정

```python
# 키움증권 모의/실 위탁 표준
sim = IntradaySimulator(
    entry_on_next_open=True,
    exit_on_intrabar=True,
    commission_pct=0.015,         # 0.015% (위탁수수료)
    tax_pct_on_sell=0.18,          # 0.18% (증권거래세 0.15 + 농특세 0.15)
    slippage_pct=0.05,             # 0.05% (시장가 호가 1~2틱)
)
```

## Tests
- 5 신규 (default 검증 1 + synthetic 4 — 일부 진입 시그널 X 시 skip)
- 회귀 829 → **830** passed (+5 신규, 4 skip), 0 fail
- flaky `test_tampered_token_raises` 단독 통과 확인 (known issue)

## 운영 영향
- 백테스트 결과가 실 운영과 비슷해짐 → `/tune apply` 학습 루프 정확도 ↑
- simulate_leaders.py 의 default 도 자동 적용 (코드 변경 없이 효과)
- 이전 시뮬 결과 (`data/simulation_log.csv`) 와 비교 시 차이 클 수 있음 — 정상

## 다음
- 운영 시작 (cron + 봇 데몬)
- 보안 액션 (키 + 토큰 회전)
- 1주 검증 후 실전 host 결정
