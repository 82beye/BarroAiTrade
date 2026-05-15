# F존 ATR 청산 적용 실험 (2026-05-16)

## 목적

[BAR-46/47 deep analysis](../../03-analysis/bar-46-47-f-sf-zone-deep-analysis.md) §6 후속 — SF존의 ATR 동적 청산(`_sfzone_atr_exit_plan`)을 F존 백테스트에도 적용했을 때 효율 변화 측정.

## 구현

`backend/core/backtester/intraday_simulator.py`:

- `IntradaySimulator.__init__` 에 `f_zone_atr_exit: bool = False` 추가
- `_exit_plan_for_strategy(strategy_id, entry_price, candles_window, *, f_zone_atr=False)` 인자 추가
- `f_zone_atr=True` AND `strategy_id == "f_zone"` 이면 `_sfzone_atr_exit_plan` 사용 (sf_zone과 동일 ATR plan)
- 기본 False — 회귀 보존 (기존 90+ 테스트 통과)

신규 테스트 2건 (`test_intraday_simulator.py:TestAtrDynamicSL`):
- `test_exit_plan_fzone_atr_when_enabled` — `f_zone_atr=True` 시 sf_zone 결과와 동일
- `test_simulator_f_zone_atr_exit_flag` — `IntradaySimulator(f_zone_atr_exit=True)` 정상 완료

## 실험 결과 — 캐시 600봉 백테스트 (수수료 0.015%·세금 0.18% 적용)

### 운영 종목 8개 (5/12~13 운영 대상)

| 종목 | Fixed (trades / pnl / win%) | ATR (trades / pnl / win%) | 차이 |
|------|---:|---:|---:|
| 319400 현대무벡스 | 4건 / +202,972 / 75% | 4건 / +873,234 / 89% | **+670,262** |
| 066570 LG전자 | 2건 / -437,523 / 0% | 2건 / **-2,794,443** / 0% | **-2,356,920** |
| 090710 휴림로봇 | 11건 / +94,490 / 65% | 10건 / +152,707 / 74% | +58,218 |
| 010170 대한광통신 | 11건 / +52,269 / 71% | 8건 / +152,397 / 89% | +100,127 |
| 003280 흥아해운 | 1건 / -3,252 / 0% | 1건 / -21,957 / 0% | -18,706 |
| 012200 계양전기 | 1건 / +2,331 / 100% | 1건 / +5,064 / 100% | +2,733 |
| 356680 엑스게이트 | 1건 / +44,882 / 100% | 1건 / +40,385 / 100% | -4,497 |
| 012860 모베이스전자 | 1건 / -10,618 / 0% | 1건 / -100,632 / 0% | -90,014 |
| **합계** | **32건 / -54,450** | **28건 / -1,693,246** | **-1,638,796** ⚠️ |

### 강세 종목 10개 (5/16 캐시 스캔 — 최근 30봉 +10%↑ AND 양봉 ≥60%)

| 종목 | Fixed (trades / pnl / win%) | ATR (trades / pnl / win%) | 차이 |
|------|---:|---:|---:|
| 159010 | 6건 / +54,283 / 75% | 6건 / +59,219 / 83% | +4,937 |
| 187870 | 3건 / -13,028 / 60% | 2건 / +277,771 / 100% | +290,799 |
| 009155 | 7건 / +3,622,823 / 93% | 5건 / **+6,238,104** / 100% | **+2,615,280** |
| 336260 | 6건 / -78,277 / 56% | 4건 / +2,056,450 / 90% | **+2,134,727** |
| 009150 | 4건 / +6,912,379 / 90% | 3건 / **+12,874,740** / 100% | **+5,962,362** |
| 163730 | 2건 / +37,295 / 75% | 2건 / -44,944 / 75% | -82,239 |
| 402340 | 1건 / -123,934 / 0% | 1건 / -805,403 / 0% | **-681,469** |
| 144960 | 2건 / +980 / 67% | 2건 / +25,010 / 75% | +24,029 |
| 252400 | 2건 / -132,684 / 0% | 1건 / +232,307 / 100% | +364,991 |
| 262260 | 5건 / -5,021 / 62% | 5건 / -9,731 / 88% | -4,709 |
| **합계** | **38건 / +10,274,815** | **31건 / +20,903,523** | **+10,628,708** ✅ |

## 핵심 발견

### 1. 시장 국면 의존성이 극명

- **운영 종목 (박스권/변동성 혼합)**: ATR 청산이 **-1.64M 손해**
- **강세 종목 10개**: ATR 청산이 **+10.63M 추가 이익** — Fixed 대비 **2.03배**

ATR 청산은 강세 추세 종목에서만 유리하고, 박스권/변동성 큰 종목에선 SL 폭이 커져 손실 폭도 비례 증가.

### 2. ATR SL의 양날 검

- `_sfzone_atr_exit_plan` SL = `−atr_clamped × 2.0` (정상 -3% ~ -16%)
- 강세 종목 — TP 3.5×ATR 까지 잡아 큰 이익 실현
- 박스권 종목 — SL 6~16%까지 깊어져 손실 비례 확대. 특히 LG전자 -2.79M (Fixed 대비 -2.36M 추가 손실)

### 3. 거래 수 감소

- 운영: 32건 → 28건 (-12.5%)
- 강세: 38건 → 31건 (-18.4%)

ATR이 SL 발동을 줄여 포지션을 오래 보유 → 한 포지션의 시간 점유 ↑ → 재진입 기회 ↓. 강세장에선 "버티면 TP에 도달"하지만 박스권에선 "SL 안 맞고 길게 손실 누적".

### 4. 승률은 일관되게 ATR이 ↑

- 강세 종목: 거의 모든 종목에서 승률 ↑ (예: 336260 56%→90%, 252400 0%→100%)
- 운영 종목도 부분 ↑ (319400 75%→89%, 010170 71%→89%)
- 변동성 비례 TP가 더 자주 도달하는 효과

## 시사점

1. **F존 ATR 청산은 "강세 종목 전용 옵션"** — 모든 종목에 일괄 적용 X
2. **`market_regime` 자동 분류와 결합 가능** — BULL 분류 시 `f_zone_atr_exit=True`, SIDEWAYS·BEARISH는 False
3. **종목별 변동성 임계 기반 토글도 가능** — 종목 ATR%가 일정 임계 초과 시 ATR plan 적용
4. **SF존도 같은 패턴일 가능성** — SF존은 항상 ATR plan을 쓰는데, 박스권 SF존 신호는 거의 발화 안 함(점수 7.0+ 조건). 실효 영향 작음. F존은 신호 빈번해 영향 큼.

## 후속 (선택)

- `IntradaySimulator(f_zone_atr_exit=True)` 를 `simulate_portfolio.py` 에 `--f-zone-atr` 인자로 노출
- `market_regime` 자동 분류와 통합 — BULL 분류 시 `f_zone_atr_exit` 자동 True
- F존 전용 ATR plan (sf_zone 보다 보수적 TP/SL — 예: TP 1.0/2.0/3.0×ATR) 별도 함수
- 동일 실험을 다양한 강세장 시기(2024년 상반기, 2026년 등) 캐시 데이터로 반복 — 통계 신뢰도 ↑

## 코드 변경

| 파일 | 변경 |
|------|------|
| `backend/core/backtester/intraday_simulator.py` | `_exit_plan_for_strategy` `f_zone_atr` 인자 + `IntradaySimulator.__init__` `f_zone_atr_exit` 옵션 (4 줄) |
| `backend/tests/backtester/test_intraday_simulator.py` | 신규 테스트 2건 (15 줄) |
| `docs/03-analysis/bar-46-47-f-sf-zone-deep-analysis.md` | 신규 (deep analysis 문서) |
| `docs/04-report/features/F-zone-atr-exit-experiment.md` | 본 문서 |

`scripts/_compare_fzone_atr.py` — 일회성 실험 스크립트, 미커밋.

## 검증

```bash
venv/bin/python -m pytest backend/tests/backtester/ -q
# 92 passed, 4 skipped
```
