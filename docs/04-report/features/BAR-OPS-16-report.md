# BAR-OPS-16 — 시뮬 + 잔고 통합 (자금 한도 추천 매수 qty)

## 목적
시뮬 결과만으로는 실 매수 판단 불가. **자금 한도 정책 + 잔고 조회 + 추천 qty** 까지 결합 → 운영 가능 의사결정.

## 산출
- `backend/core/risk/balance_gate.py`:
  - `evaluate_risk_gate(deposit, balance, candidates, max_per_position_ratio, max_total_position_ratio)`
  - `PositionRecommendation` (frozen) — symbol·name·cur_price·max_value·recommended_qty·blocked·reason
  - `RiskGateResult` (frozen) — cash·current_eval·available·max_per_position·max_total_position·recommendations[]
  - 정책: 종목당 30% / 총 90% (조정 가능)
- `scripts/simulate_leaders.py` — `--check-balance` + `--max-per-position` + `--max-total-position`
- `backend/tests/risk/test_balance_gate.py` — 7 cases

## 실 검증 (mockapi.kiwoom.com, 2026-05-08, top 5)

```
== 시뮬 결과 ==
총 거래: 110, swing_38 +7,458,231

== 잔고 기반 자금 한도 + 추천 매수 qty ==
  예수금         :      48,930,069
  현재 평가금액   :               0
  진입 가능액     :      44,037,062
  종목당 한도    :      14,679,021
  총 보유 한도   :      44,037,062

  symbol  name        price  rec_qty       value  비고
  319400  현대무벡스   37,700    389  +14,665,300  ✅
  001440  대한전선     72,300    203  +14,676,900  ✅
  005380  현대차      618,000    23  +14,214,000  ✅
  005930  삼성전자    276,500     1     +276,500  ✅ (한도 거의 소진)
  010170  대한광통신  22,350     9     +201,150  ✅ (한도 소진)
```

→ 3 종목으로 한도 거의 채우고 4·5번째는 남은 자금만 사용. 정책 정확 동작.

## 정책 알고리즘
```
slot_per_position = cash * max_per_position_ratio          # 종목당 상한
max_total = cash * max_total_position_ratio                # 총 상한
available = max_total - balance.total_eval                  # 남은 가용
                                                           # (음수면 0)

for each candidate (점수 내림차순):
    slot = min(slot_per_position, available - consumed)
    qty = floor(slot / cur_price)
    if qty > 0:
        consumed += qty * cur_price
        recommend(qty)
    else:
        block(reason)
```

## 운영
```bash
# 당일 16시 cron — 시뮬 + 잔고 + 추천 → CSV 영속화
python scripts/simulate_leaders.py --top 5 --min-score 0.5 \
  --check-balance \
  --max-per-position 0.30 \
  --max-total-position 0.90 \
  --log data/simulation_log.csv

# 보수적 운영 (per 10%, total 50%)
python scripts/simulate_leaders.py --top 5 --check-balance \
  --max-per-position 0.10 --max-total-position 0.50
```

## Tests
- 7 신규 / 회귀 **704 → 711 (+7)**, 0 fail

## OPS 트랙 마무리
이번 자율 진행 사이클 완성도:

| 단계 | BAR | 산출 |
|------|-----|------|
| 입력 | OPS-10 | OAuth + 일봉/분봉 (api/dostk/chart) |
| 선정 | OPS-11/12 | LeaderPicker 3-factor (TV·FR·VOL) + min_score |
| 분석 | OPS-08 | IntradaySimulator 5 전략 |
| 영속 | OPS-13 | CSV append + history 분석 |
| 잔고 | OPS-15 | 계좌평가·예수금 조회 (acnt) |
| 정책 | **OPS-16** | **자금 한도 + 추천 qty** ← 운영 마침표 |
| 주문 | OPS-14 | DRY_RUN + 매수/매도 어댑터 (ordr) |

**End-to-end 자동화 흐름 가능** — 시뮬→영속→정책→추천→주문(DRY_RUN).

## ⚠️ 실전 진입 전 미통합 항목
- BAR-64 Kill Switch (Circuit Breaker)
- BAR-68 MFA + audit log 무결성
- 미체결 주문 조회 (kt00004 body 조정 필요)
- 실시간 WebSocket fill 추적
