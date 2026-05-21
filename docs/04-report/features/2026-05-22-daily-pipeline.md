# 2026-05-22 — Daily 운영 audit 자동화 (Phase 1)

## 목적

5/18~5/21 운영 후, 매일 저녁 운영 결과를 audit 하고 손실 종목을 진단해 전략
파라미터를 다음 영업일에 반영하는 **일별 사이클**의 1단계. kt00009 정확 체결가
기반 종목별 net 계산 → 전략별 분류 → 손실 종목 단계별 진단을 자동화한다.

## 도구 3종 (`scripts/`)

| 스크립트 | 역할 | 입력 | 출력 |
|---|---|---|---|
| `_daily_evening_pipeline.py` | zip 해제 + kt00009 체결 → 종목별 net + 전략 분류 | zip / `--executions-file` | 콘솔 표 + `analysis/strategy_ledger.csv` |
| `_strategy_perf_track.py` | 누적 전략 성과 집계 | `strategy_ledger.csv` | `analysis/strategy_perf.csv` + 누적 표 |
| `_loss_drill_down.py` | 손실 종목 1개 단계별 진단 | `--symbol --date` | 5개 섹션 콘솔 진단 |

### net 계산

`net = (매도평단 − 매수평단) × 수량 − 수수료 − 세금`
- 수수료 = `(매수평단 + 매도평단) × 수량 × 0.015%` (키움 위탁, 매수·매도 각각)
- 세금 = `매도평단 × 수량 × 0.18%` (증권거래세 + 농특세, 매도 시만)

### 전략 귀속 — 다단계 fallback

1. `data/active_positions.json` 의 `strategy` 필드 (`ActivePositionStore`)
2. `logs/*.log` 에서 종목코드 + 전략명 동시 출현 줄 매칭
3. `ohlcv_cache/` 분봉으로 `IntradaySimulator` 재시뮬 → 체결 시각 최근접 전략
4. 모두 실패 시 `unknown`

## 사용법

```bash
# 라이브 kt00009 (M4 — KIWOOM_APP_KEY/SECRET/ACCOUNT_NO 환경변수 필요)
python scripts/_daily_evening_pipeline.py --zip ~/Downloads/BarroAiTrade_x.zip --date 2026-05-21

# 사전 덤프 파일로 (라이브 호출 없이)
python scripts/_daily_evening_pipeline.py --date 2026-05-21 \
    --executions-file kt00009_2026-05-21.json --import-dir analysis/imports/2026-05-21

python scripts/_strategy_perf_track.py [--graph]
python scripts/_loss_drill_down.py --symbol 027360 --date 2026-05-21
```

## 일별 사이클

```
운영 머신 zip → _daily_evening_pipeline (해제 + kt00009 + 전략 분류)
  → strategy_ledger.csv → _strategy_perf_track (누적 성과)
  → _loss_drill_down (손실 종목 진단) → 전략 fix → 시뮬 검증 → commit
```

매일 저녁 반복하며 손실 패턴을 진단하고 1~2개 전략 파라미터를 다음 영업일에
반영한다 (Phase 2~7 — f_zone·swing_38·gold_zone·sf_zone·청산 타점 정교화).

## 런타임 산출물 (gitignore)

`analysis/imports/<date>/`, `analysis/strategy_ledger.csv`,
`analysis/strategy_perf.csv`, `analysis/strategy_perf.png` 는 운영 데이터이므로
추적하지 않는다 (`analysis/*.py` 는 추적 유지).

## 검증

- `pytest backend/tests/test_daily_pipeline.py` — net 계산·전략 귀속 fallback·
  ledger idempotency·perf 집계·파이프라인 스모크 (14건)
- 회귀 `pytest backend/tests/strategy/ backend/tests/risk/` — 영향 없음 (기존 코드 미변경)
- 라이브 kt00009 호출은 키움 자격증명·네트워크 필요 — M4 에서 `--mode real` 최초 확인
