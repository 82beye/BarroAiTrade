# 학습 보고서 — f_zone `impulse_max_gain_pct` 가설 실험 (롤백)

**가설:** "f_zone 의 LG전자 손실(-627,521원)은 +7% 초과 과열 임펄스 다음 봉 매수
패턴에서 발생. 임펄스에 상한(7%)을 두면 단기 정점 매수가 차단된다."

**결과:** **다종목 일반화 실패**. LG 1종목에서는 효과(−190k 절감)였으나 전체
8 종목 합산은 **−243k 손해** (특히 sf_zone 의 winning 시그널 함께 사망).

**조치:** **롤백** — 베이스라인 상태로 복귀. 본 보고서는 학습 기록 보존.

---

## 1. 변경 내역 (롤백 전)

```python
# backend/core/strategy/f_zone.py
class FZoneParams:
    impulse_min_gain_pct: float = 0.03
    impulse_max_gain_pct: float = 0.07   # ← 추가했던 상한
    ...

def _detect_impulse(self, df, analysis):
    ...
    if (p.impulse_min_gain_pct <= gain_pct <= p.impulse_max_gain_pct  # ← 상한 검사
        and vol_ratio >= p.impulse_volume_ratio):
        ...
```

## 2. 백테스트 결과 비교 (8 종목 × 600봉)

| 전략 | Before | After | Δ |
|------|-------:|------:|--:|
| **f_zone** | 8/8 active, 34 trades, win 56.5%, **−99,409** | 6/8 active, 22 trades, win 54.9%, **−185,905** | **−86,496** ❌ |
| **sf_zone** | 4/8 active, 9 trades, win 89.9%, **+160,847** | 1/8 active, 2 trades, win 80.0%, **+4,309** | **−156,538** ❌ |
| gold_zone / swing_38 / scalping_consensus | 동일 | 동일 | 0 |
| **합계 영향** | | | **−243,034** |

### LG전자 단일 종목 (가설 추출 대상)

| | Before | After |
|---|-------:|------:|
| trades | 3 | 2 |
| total | −627,521 | **−437,011** |
| Δ | | **+190,510** ✅ |

→ 가설은 **LG에서만 유효**, 다른 7종목으로 일반화에서 역효과.

## 3. 핵심 학습

### Lesson 1 — N=3 패턴 일반화의 통계적 함정

LG전자 3 trades 패턴을 8 종목 합 약 50 trades 모집단에 일반화. 표본
크기·종목 다양성을 고려하지 않은 가설. **차단된 12 f_zone trades 중
약 7건이 winning trade** (계산: 종전 19W/15L → 후 12W/10L → 차단 7W/5L).
"+7% 초과 임펄스 = 평균적으로 좋은 시그널" 이라는 데이터 반전.

### Lesson 2 — sf_zone 의 정의에 정면 충돌

sf_zone 은 "강한 임펄스(+5%·거래량 3x)" 가 핵심 조건. max 7% 는 정확히
그 sweet spot 을 잘라냄.

| | Before | After |
|---|-------:|------:|
| sf_zone trades | 9 | 2 |
| sf_zone 활성 종목 | 4/8 | 1/8 |

→ 새 파라미터는 **f_zone 과 sf_zone 을 동일 검출 로직에서 공유**하므로
하나에 손대면 다른 하나도 영향. 별도 검출 로직으로 분리하지 않고서는
sf_zone 의 winning 시그널을 보존할 수 없음.

### Lesson 3 — Hypothesis 검증 절차 보강 필요

이번 실험에서 누락된 단계:
1. **차단 trades 의 winning 비율 사전 추정** — 코드 적용 전 dry-run 으로
   "+7% 초과 임펄스 봉이 향후 N봉에서 +X% 도달 비율" 통계
2. **종목 클러스터별 효과 분리 측정** — 변동성 high/mid/low 그룹
3. **전략 정의(sf_zone 의 강한 임펄스)와 가설의 직접 충돌 여부 사전 검토**

## 4. 다음 후보 (가설을 좁히거나 다른 접근)

| 후보 | 설명 | 우선순위 |
|------|------|---------|
| **D1. ATR 기반 동적 SL** | 종목별 변동성에 비례한 SL 임계 (현재 −1.5% 고정 → −2×ATR 등). 일봉/분봉 무관 일반화 가능 | 🔴 |
| D2. 진입 봉 갭다운 거부 | "다음 봉 open 이 시그널 봉 close 대비 +X% 갭상승 시 진입 보류" — Trade #2 같은 케이스 차단 (LG #3 같은 일중 변동은 못 막음) | 🟡 |
| D3. sf_zone 만 max 면제 | f_zone 만 max 7% 적용, sf_zone 은 면제. 별도 검출 함수 분리 필요 | 🟡 |
| D4. 임펄스 봉 + 익봉 강도 추가 검증 | 임펄스 다음 봉이 음봉/긴 윗꼬리이면 진입 거부 (정점 시그널) | 🟢 |

## 5. 롤백 검증

```bash
cd /Users/beye/workspace/BarroAiTrade
./venv/bin/python analysis/imports/2026-05-13/backtest_from_cache.py
# 결과: f_zone −99,409 / sf_zone +160,847 (베이스라인 복귀)
```

`git status` 둘 다 깨끗. 추가 파일 없음. 본 보고서만 신규.

## 6. 보존된 산출물

| 파일 | 목적 |
|------|------|
| `LESSON_FZONE_MAX_GAIN.md` | 본 학습 기록 (롤백 사유 포함) |
| `FZONE_LG_TRACE.md` | LG 3 trades 추적 (가설 발원) |
| `trace_fzone_lg.py` | 추적 스크립트 (재사용 가능) |
| `REPORT_600BARS.md` | 8 종목 baseline 백테스트 |
| `backtest_from_cache.py` | 백테스트 스크립트 (다음 가설 재사용) |
