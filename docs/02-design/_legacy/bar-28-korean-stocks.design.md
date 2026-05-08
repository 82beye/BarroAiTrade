---
tags: [design, feature/bar-28, status/in_progress]
---

# BAR-28 한국 주식 시장 분석 + 관심종목 리스트 Design

> **Project**: BarroAiTrade
> **Feature**: BAR-28
> **Author**: Stock Analyst Agent (89c9e8da)
> **Date**: 2026-04-12
> **Status**: Design Phase
> **Gate**: Simulation Mode Launch (모의투자 개시)

---

## 1. 설계 개요

### 1.1 목표
BAR-28 Plan에서 정의된 시장분석 및 종목 선정을 구현하기 위한 기술 설계. 3단계 필터링 프로세스를 통해 30-50개 최적 거래 종목을 선별.

### 1.2 핵심 원칙
- **정량적 검증**: 모든 선정 기준은 수치 기반
- **시스템 최적화**: BarroAiTrade 거래 특성(파란점선/수박신호)에 맞춘 필터링
- **실거래성**: 유동성, 거래량 기반 실행 가능성 검증
- **포트폴리오**: 산업 다각화 및 리스크 분산

---

## 2. 시스템 아키텍처

### 2.1 3단계 필터링 파이프라인

```
Step 1: 시장 상태 분석 (Market Screening)
   ├─ KOSPI/KOSDAQ 지수 추이 수집
   ├─ 상승/하락주 비율 계산
   ├─ 섹터별 강도 분석
   └─ 시장 리스크 평가 (VIX, 변동성)

Step 2: 종목 예비 스크리닝 (Pre-filtering)
   ├─ 유동성 필터: 일평균거래량 > 1M주
   ├─ 변동성 필터: 월간변동률 5~30%
   ├─ 가격대 필터: 예탁금 상한 내
   └─ 데이터 품질: 최소 60일 거래 기록

Step 3: 기술신호 최적화 (Signal Ranking)
   ├─ 파란점선 신호 호응도 점수
   ├─ 수박신호 신호 호응도 점수
   ├─ 복합신호 점수 (가중 평균)
   └─ 최종 순위 결정

Step 4: 포트폴리오 구성 (Portfolio Assembly)
   ├─ 산업군 다각화 (최대 8개 섹터)
   ├─ 시가총액 균형 (대형/중형/소형)
   ├─ 리스크 조정
   └─ 최종 30-50종목 선정
```

### 2.2 데이터 흐름도

```
KiwoomAPI
  ├─ 실시간 주가 데이터
  ├─ 거래량 데이터
  └─ 지수 데이터
       ↓
[시장분석 모듈]
  ├─ 데이터 정규화
  ├─ 기술지표 계산
  └─ 시장 상태 판정
       ↓
[스크리닝 모듈]
  ├─ 1차 필터링 (유동성, 변동성)
  ├─ 2차 필터링 (기술신호 호응도)
  └─ 점수 부여
       ↓
[포트폴리오 구성 모듈]
  ├─ 산업군 분류
  ├─ 리스크 조정
  └─ 최종 선정
       ↓
[산출물 생성]
  ├─ KR-Market-Analysis-2026Q2.md
  ├─ KR-Watchlist-30-50.csv
  └─ Individual-Stock-Analysis.md
```

---

## 3. 상세 설계 명세

### 3.1 필터링 기준 명세

#### 시장 분석 (Market Analysis)

| 항목 | 기준 | 평가 방법 | 비고 |
|------|------|---------|------|
| KOSPI 추세 | 상승/하락 판정 | 20일 이동평균 대비 현재가 | 거시 지표 |
| 섹터 강도 | 상대 강도 순위 | 섹터별 수익률 비교 | 산업군 선택 |
| 시장 변동성 | VIX 또는 KOSPI 변동률 | 30일 표준편차 | 리스크 수준 |
| 유동성 | 시장 거래량 | 일평균거래량 × 주가 | 시장 건강도 |

#### 종목 스크리닝 (Stock Screening)

| 필터 | 기준값 | 적용 순서 | 제외율 |
|------|--------|---------|-------|
| 일평균거래량 | > 1,000,000주 | 1순위 | ~30% |
| 월간변동률 | 5~30% | 2순위 | ~20% |
| 예탁금 적합성 | < 5,000만원/종목 | 3순위 | ~10% |
| 데이터 품질 | 최소 60일 | 4순위 | ~5% |

#### 기술신호 호응도 점수 (Signal Score)

```python
# 파란점선 신호 호응도 계산
blue_line_score = (
    recent_signal_count / total_days * 100  # 신호 발생 빈도
    + consecutive_wins / total_signals * 50  # 연속 승리율
    - max_consecutive_loss / total_signals * 20  # 최대 연패 페널티
)

# 수박신호 호응도 계산
watermelon_score = (
    signal_volume_confirm / total_volume * 100  # 거래량 확인율
    + signal_to_price_correlation * 50  # 가격 연동성
    + early_entry_success_rate * 30  # 조기 진입 성공률
)

# 복합신호 점수 (가중 평균)
hybrid_score = (blue_line_score * 0.5) + (watermelon_score * 0.5)
```

#### 포트폴리오 구성 가중치 (Portfolio Weighting)

```
산업군 다각화 (5-8개 섹터)
├─ 자동차 (5-8%)
├─ 전자/반도체 (10-15%)
├─ 금융 (8-12%)
├─ 화학/에너지 (8-10%)
├─ 통신/미디어 (5-8%)
├─ 유통/서비스 (5-8%)
├─ 건설/부동산 (5-8%)
└─ 기타 (10-15%)

시가총액 혼합
├─ 대형주 (시총 1조+): 40-50%
├─ 중형주 (시총 2000억~1조): 30-40%
└─ 소형주 (시총 ~2000억): 10-20%

리스크 프로필
├─ High Risk (변동성 20-30%): 20-30%
├─ Mid Risk (변동성 10-20%): 50-60%
└─ Low Risk (변동성 <10%): 10-20%
```

### 3.2 산출물 명세

#### 1️⃣ 시장 분석 리포트 (`KR-Market-Analysis-2026Q2.md`)

```markdown
# 한국 주식 시장 분석 리포트 (2026 Q2)

## Executive Summary
- 현재 시장 상태 평가 (3줄)
- 주요 기회 요인 (2-3개)
- 주요 위험 요인 (2-3개)

## 1. 거시 지표 분석
- KOSPI 추세 및 기술적 수준
- KOSDAQ 추이 및 성장주 시장 평가
- 섹터별 강약도 분석

## 2. 유동성 및 변동성 분석
- 일평균 거래량 추이
- 시장 변동성 (VIX 수준)
- 상승/하락주 비율

## 3. 기술적 신호 환경
- 파란점선 신호 발생 빈도
- 수박신호 신호 발생 빈도
- 신호 정확성 평가

## 4. 리스크 팩터 분석
- 금리 리스크
- 환율 리스크
- 시장 구조적 리스크

## 5. 투자 권장사항
- 현재 타이밍 평가
- 섹터 선택 전략
- 위험 관리 방향
```

#### 2️⃣ 관심종목 리스트 (`KR-Watchlist-30-50.csv`)

```csv
코드,종목명,섹터,시가총액,평균거래량,파란점선점수,수박신호점수,복합점수,선정이유,리스크등급
```

최소 30개, 최대 50개 종목 포함

#### 3️⃣ 종목별 상세 분석 (`Individual-Stock-Analysis.md`)

```markdown
# 종목 상세 분석

## 1. 종목명 (코드)

### 기본정보
- 시가총액: xxx억원
- 일평균거래량: xxx주
- 현재가: xxx원
- 52주 고/저: xxx / xxx

### 거래 특성
- 일평균거래액: xxx억원 (범위: Y)
- 일일 변동성: xx% (과거 30일 평균)
- 월간 변동률: xx% (과거 3개월)

### 기술신호 호응도
- 파란점선 신호: xx회 (신뢰도: xx%)
- 수박신호: xx회 (정확도: xx%)
- 최근 신호 상태: [상향/중립/하향]

### 리스크 평가
- 변동성 등급: [Low/Mid/High]
- 유동성 등급: [우수/양호/주의]
- 리스크 점수: x.xx

### 추천 진입전략
- 기본 진입가: xxx원 (파란점선 기준)
- 손절 수준: xx% (-xx원)
- 익절 1차: xx% (+xx원)
- 익절 2차: xx% (+xx원)

### 선정 사유
[구체적 이유, 2-3문장]
```

### 3.3 데이터 검증 방법

```
Stage 1: 입력 데이터 검증
├─ 데이터 결손 확인 (NaN, 0)
├─ 이상치 탐지 (3σ rule)
├─ 시간 순서 검증

Stage 2: 필터 검증
├─ 각 필터별 제외율 모니터링
├─ 필터 단계별 종목 수 추적
├─ 필터 충돌 확인 (예: 너무 많이 제외되는 경우)

Stage 3: 점수 검증
├─ 점수 분포 확인 (최소, 최대, 평균)
├─ 기술신호 점수 vs 실제 신호 빈도 비교
├─ 점수 재현성 (다시 계산 시 동일 결과 확인)

Stage 4: 포트폴리오 검증
├─ 산업군 분포 확인
├─ 시가총액 분포 확인
├─ 리스크 프로필 확인
└─ 최종 종목 수 확인 (30-50)
```

---

## 4. 구현 세부사항

### 4.1 코드 구조

```
backend/
├─ services/
│  └─ stock_screener.py
│     ├─ MarketAnalyzer
│     │  ├─ analyze_market_trend()
│     │  ├─ calculate_sector_strength()
│     │  └─ evaluate_market_health()
│     ├─ StockScreener
│     │  ├─ apply_liquidity_filter()
│     │  ├─ apply_volatility_filter()
│     │  ├─ calculate_signal_score()
│     │  └─ rank_candidates()
│     └─ PortfolioBuilder
│        ├─ diversify_by_sector()
│        ├─ balance_market_cap()
│        ├─ adjust_risk_profile()
│        └─ build_final_watchlist()
│
docs/
├─ 01-plan/analysis/
│  ├─ KR-Market-Analysis-2026Q2.md
│  ├─ KR-Watchlist-30-50.csv
│  └─ Individual-Stock-Analysis.md
```

### 4.2 알고리즘 의사코드

```python
def build_watchlist():
    # Step 1: 시장 분석
    market_state = analyze_market()  # KOSPI, 섹터, 변동성
    
    # Step 2: 1차 스크리닝 (유동성, 변동성)
    candidates = apply_filters(all_stocks, market_state)
    # 필터링 후: ~50-100개
    
    # Step 3: 기술신호 점수 계산
    for stock in candidates:
        blue_line_score = analyze_blue_line_signal(stock)
        watermelon_score = analyze_watermelon_signal(stock)
        hybrid_score = weighted_average(blue_line_score, watermelon_score)
        stock.set_signal_score(hybrid_score)
    
    # Step 4: 포트폴리오 구성
    ranked = sort_by_signal_score(candidates)
    final_watchlist = build_portfolio(
        ranked,
        sector_targets={...},
        market_cap_targets={...},
        risk_targets={...}
    )
    # 최종: 30-50개
    
    return final_watchlist
```

---

## 5. 성과 지표 (KPI)

| KPI | 목표값 | 검증 방법 |
|-----|--------|---------|
| 필터링 효율성 | 1단계 30%, 2단계 20%, 3단계 15% | 각 단계 제외율 |
| 기술신호 적합도 | 호응도 > 80% | 신호 히스토리 분석 |
| 포트폴리오 다각화 | 5-8개 섹터, 시총 혼합 | 분포도 분석 |
| 최종 리스트 크기 | 30-50개 | 종목 수 확인 |
| 실행 가능성 | 예탁금 < 5,000만/종목 | 예탁금 검증 |

---

## 6. 리스크 및 대응

| 리스크 | 영향 | 대응 방안 |
|--------|------|---------|
| 시장 급변 | 선정 기준 부실 | 주간 재분석 |
| 신호 품질 저하 | 호응도 하락 | 파라미터 재조정 |
| 유동성 악화 | 실행 불가 | 추가 유동성 필터 |
| 데이터 결손 | 분석 지연 | 보조 데이터 확보 |

---

## 7. 검수 체크리스트

- [ ] 시장 분석 리포트: 5섹션 이상, 1500자 이상
- [ ] 종목 리스트: 30-50개, 선정 이유 명확
- [ ] 상세 분석: 모든 종목 200자 이상
- [ ] 데이터 검증: 필터링 과정 기록
- [ ] 포트폴리오: 산업군 5-8개, 시총 균형
- [ ] GAP 분석: Design vs Implementation 비교

---

## 8. 승인 및 서명

| 역할 | 이름 | 승인 | 날짜 |
|------|------|------|------|
| Stock Analyst | Agent (89c9e8da) | ⏳ | 2026-04-12 |
| Head of Research | - | ⏳ | - |
| CTO/Project Lead | - | ⏳ | - |

---

*최종 업데이트: 2026-04-12 | Status: Design Phase*
