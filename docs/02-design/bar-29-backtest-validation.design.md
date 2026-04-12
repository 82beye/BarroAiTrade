---
tags: [design, feature/bar-29, status/in_progress]
---

# BAR-29 백테스팅 전략 검증 Design

> **Project**: BarroAiTrade
> **Feature**: BAR-29
> **Author**: Stock Analyst Agent (89c9e8da)
> **Date**: 2026-04-12
> **Status**: Design Phase
> **Gate**: Simulation Mode Launch (모의투자 개시)

---

## 1. 설계 개요

### 1.1 목표
BAR-24 백테스팅 엔진의 전략 성과를 객관적으로 검증하고, 실거래 적용 가능성을 평가하기 위한 분석 설계.

### 1.2 핵심 원칙
- **정량적 근거**: 통계적으로 유의미한 지표 중심
- **다각형 평가**: 수익성, 위험성, 실행성의 균형 검증
- **과거 편견 제거**: Out-of-sample 테스트 및 시기별 분석
- **실거래 반영**: 거래비용, 슬리페이지 포함

---

## 2. 검증 프레임워크

### 2.1 4대 검증 영역

```
1. 수익성 검증 (Return Analysis)
   ├─ 총 수익률 (Total Return)
   ├─ 연간 수익률 (CAGR)
   ├─ 월간/분기별 수익률
   └─ 윤후효과(Seasonality) 분석

2. 위험성 검증 (Risk Analysis)
   ├─ 최대낙폭 (Maximum Drawdown)
   ├─ 변동성 (Volatility / Standard Deviation)
   ├─ VaR (Value at Risk)
   └─ 회복시간 (Recovery Time)

3. 효율성 검증 (Efficiency Analysis)
   ├─ 승률 (Win Rate)
   ├─ 손익비 (Profit Factor)
   ├─ 샤프 지수 (Sharpe Ratio)
   └─ 칼마 비율 (Calmar Ratio)

4. 실행성 검증 (Execution Analysis)
   ├─ 거래비용 영향도
   ├─ 슬리페이지 추정
   ├─ 거래 지속시간
   └─ 거래 빈도
```

### 2.2 검증 데이터 구조

```
Input Data (BAR-24 출력)
├─ 전략별 거래 로그
│  ├─ 진입 시간/가격
│  ├─ 청산 시간/가격
│  ├─ 거래량
│  └─ 신호 타입
├─ 시세 데이터
│  ├─ OHLCV (일/시간/분 단위)
│  └─ 지수/벤치마크
└─ 시장 데이터
   ├─ 거래량
   ├─ 호가 스프레드
   └─ 거래량 필터

Processing
├─ 데이터 정제 (이상치 제거)
├─ 비용 계산 (수수료, 세금)
├─ 슬리페이지 추정
└─ 조정된 거래 재계산

Output Metrics
├─ 15+ 성과 지표
├─ 위험도 등급
├─ 시각화 차트 (5+)
└─ 최종 평가 등급
```

---

## 3. 성과 지표 명세

### 3.1 수익성 지표 (Return Metrics)

| 지표 | 계산식 | 목표 | 평가 |
|------|--------|------|------|
| **Total Return (TR)** | (최종자산 - 초기자산) / 초기자산 | > 50% | 누적 수익률 |
| **CAGR** | (최종자산 / 초기자산)^(1/년수) - 1 | > 15% | 연평균 수익률 |
| **Monthly Avg Return** | 월간 수익률 평균 | > 1.5% | 월평균 수익 |
| **Win Rate** | 수익거래 수 / 전체거래 수 | > 45% | 승리율 |
| **Profit Factor** | 총 수익 / 총 손실 | > 1.5 | 손익비 |
| **Avg Win / Avg Loss** | 평균 수익거래 / 평균 손실거래 | > 1.2 | 평균 손익 |

### 3.2 위험성 지표 (Risk Metrics)

| 지표 | 계산식 | 목표 | 평가 |
|------|--------|------|------|
| **Volatility** | 일일 수익률 표준편차 | < 15% | 변동성 |
| **Max Drawdown (MDD)** | 최고값 대비 최저값 낙폭 | < 20% | 최대 손실 |
| **Drawdown Duration** | MDD 기간 (거래일 수) | < 30days | 회복 시간 |
| **VaR (95%)** | 95% 신뢰도 일일 손실 | < 2% | 극한 리스크 |
| **Beta** | 시장(KOSPI) 대비 변동성 | < 1.2 | 시장 민감도 |
| **Correlation to Market** | 전략수익 vs 시장수익 | < 0.7 | 시장 독립성 |

### 3.3 효율성 지표 (Efficiency Metrics)

| 지표 | 계산식 | 목표 | 평가 |
|------|--------|------|------|
| **Sharpe Ratio** | (수익률 - 무위험율) / 변동성 | > 1.0 | 위험 대비 수익 |
| **Calmar Ratio** | CAGR / Max Drawdown | > 0.8 | 회복력 평가 |
| **Sortino Ratio** | 초과수익 / 하락 표준편차 | > 1.2 | 하락리스크 대비 수익 |
| **Recovery Factor** | 총 수익 / Max Drawdown | > 3.0 | 손실 회복 능력 |
| **Consecutive Losses** | 최대 연패 거래 | < 5회 | 연속 손실 |
| **Win Streak** | 최대 연승 거래 | > 3회 | 연속 수익 |

### 3.4 실행성 지표 (Execution Metrics)

| 지표 | 계산식 | 목표 | 평가 |
|------|--------|------|------|
| **Transaction Cost Impact** | 거래비용 / 총수익 | < 10% | 비용 영향도 |
| **Slippage Estimate** | 예상 슬리페이지 / 거래가격 | < 0.5% | 실행 비용 |
| **Avg Trade Duration** | 거래 지속시간 평균 | 1-5days | 거래 빈도 |
| **Trade Frequency** | 월간 평균 거래 수 | > 5회 | 거래 활동도 |
| **Liquidity Requirement** | 최대 포지션 / 평균거래량 | < 50% | 유동성 필요 |
| **Execution Likelihood** | 예상 호가 내 체결률 | > 95% | 체결 가능성 |

---

## 4. 검증 알고리즘

### 4.1 수익성 분석 알고리즘

```python
def analyze_returns(trades, benchmark, risk_free_rate):
    # 1. 기본 수익률 계산
    equity_curve = calculate_equity_curve(trades)
    total_return = (equity_curve[-1] - equity_curve[0]) / equity_curve[0]
    cagr = calculate_cagr(equity_curve, years)
    
    # 2. 월간/분기별 수익률
    monthly_returns = segment_returns(equity_curve, 'month')
    quarterly_returns = segment_returns(equity_curve, 'quarter')
    
    # 3. 벤치마크 비교
    benchmark_return = (benchmark[-1] - benchmark[0]) / benchmark[0]
    outperformance = total_return - benchmark_return
    
    # 4. 윤후 효과 분석 (월별 평균 수익률)
    seasonal_pattern = analyze_seasonality(monthly_returns)
    
    return {
        'total_return': total_return,
        'cagr': cagr,
        'monthly_avg': np.mean(monthly_returns),
        'monthly_std': np.std(monthly_returns),
        'quarterly_performance': quarterly_returns,
        'vs_benchmark': outperformance,
        'seasonal_pattern': seasonal_pattern
    }
```

### 4.2 위험성 분석 알고리즘

```python
def analyze_risks(trades, equity_curve, market_returns):
    # 1. 변동성 계산
    daily_returns = np.diff(equity_curve) / equity_curve[:-1]
    volatility = np.std(daily_returns) * np.sqrt(252)  # 연환산
    
    # 2. 최대낙폭 (MDD) 계산
    running_max = np.maximum.accumulate(equity_curve)
    drawdown = (equity_curve - running_max) / running_max
    max_drawdown = np.min(drawdown)
    mdd_duration = calculate_recovery_time(drawdown)
    
    # 3. VaR 계산 (95% 신뢰도)
    sorted_returns = np.sort(daily_returns)
    var_95 = sorted_returns[int(len(sorted_returns) * 0.05)]
    
    # 4. Beta, Correlation 계산
    beta = np.cov(daily_returns, market_returns)[0, 1] / np.var(market_returns)
    correlation = np.corrcoef(daily_returns, market_returns)[0, 1]
    
    return {
        'volatility': volatility,
        'max_drawdown': max_drawdown,
        'mdd_recovery_days': mdd_duration,
        'var_95': var_95,
        'beta': beta,
        'correlation': correlation
    }
```

### 4.3 효율성 분석 알고리즘

```python
def analyze_efficiency(returns, volatility, max_drawdown):
    # 1. Sharpe Ratio (무위험율 = 연 2.5%)
    risk_free_rate = 0.025
    sharpe = (returns - risk_free_rate) / volatility
    
    # 2. Calmar Ratio (CAGR / MDD)
    calmar = returns / abs(max_drawdown)
    
    # 3. Sortino Ratio (하락리스크 전용)
    downside_std = np.std(returns[returns < 0])
    sortino = (returns - risk_free_rate) / downside_std
    
    # 4. Recovery Factor
    total_profit = sum(trades where profit > 0)
    recovery = total_profit / abs(max_drawdown)
    
    return {
        'sharpe_ratio': sharpe,
        'calmar_ratio': calmar,
        'sortino_ratio': sortino,
        'recovery_factor': recovery
    }
```

### 4.4 실행성 검증 알고리즘

```python
def analyze_execution(trades, market_data):
    # 1. 거래비용 계산 (수수료 0.025% + 세금 0.15%)
    total_cost = sum(trade.qty * trade.price * 0.002 for trade in trades)
    cost_ratio = total_cost / total_profit
    
    # 2. 슬리페이지 추정
    # 호가 스프레드 평균 + 거래량 충격 추정
    slippage_per_trade = []
    for trade in trades:
        spread = market_data[trade.time].bid_ask_spread
        volume_impact = estimate_volume_impact(trade.qty, market_data[trade.time].volume)
        slippage = spread + volume_impact
        slippage_per_trade.append(slippage)
    
    avg_slippage = np.mean(slippage_per_trade)
    
    # 3. 거래 빈도
    monthly_trades = len(trades) / (period_days / 30)
    
    # 4. 유동성 요구도
    max_position = max(trade.qty for trade in trades)
    avg_market_volume = np.mean(market_data.volume)
    liquidity_ratio = max_position / avg_market_volume
    
    return {
        'total_cost': total_cost,
        'cost_ratio': cost_ratio,
        'avg_slippage': avg_slippage,
        'monthly_trade_count': monthly_trades,
        'liquidity_ratio': liquidity_ratio
    }
```

---

## 5. 합격 기준 및 등급 체계

### 5.1 합격 기준

```
✅ Ready for Simulation (초록색)
├─ CAGR > 15% + Sharpe > 1.0 + MDD < 20%
├─ Win Rate > 45% + Profit Factor > 1.5
├─ 실행 가능성 > 95%
└─ 모든 검증 통과

⚠️ Conditional (노란색)
├─ 최근 3년만 우수 (구간별 분석 필요)
├─ 특정 시장 조건에서만 유효
├─ 파라미터 최적화 필요
└─ 추가 검증 후 승인

❌ Not Ready (빨간색)
├─ 음수 수익률
├─ Sharpe < 0.5 또는 MDD > 30%
├─ 실행 불가능한 거래 조건
└─ 재설계 필요
```

### 5.2 최종 등급 결정 로직

```python
def determine_grade(metrics):
    score = 0
    
    # 수익성 (40점)
    if metrics.cagr > 20: score += 40
    elif metrics.cagr > 15: score += 30
    elif metrics.cagr > 10: score += 20
    
    # 위험성 (30점)
    if metrics.mdd < 15: score += 30
    elif metrics.mdd < 20: score += 25
    elif metrics.mdd < 25: score += 15
    
    # 효율성 (20점)
    if metrics.sharpe > 1.5: score += 20
    elif metrics.sharpe > 1.0: score += 15
    elif metrics.sharpe > 0.5: score += 10
    
    # 실행성 (10점)
    if metrics.execution_likelihood > 95: score += 10
    elif metrics.execution_likelihood > 90: score += 8
    
    if score >= 80:
        return "Ready for Simulation"
    elif score >= 60:
        return "Conditional"
    else:
        return "Not Ready"
```

---

## 6. 산출물 명세

### 6.1 검증 리포트 (`Backtest-Validation-Report.md`)

```markdown
# 백테스팅 검증 리포트

## Executive Summary
- 3문장 평가 및 최종 등급

## 1. 검증 대상 전략
- 전략별 개요
- 신호 유형 및 파라미터

## 2. 검증 환경
- 데이터 기간 (2018-2025)
- 시작 자본
- 거래비용 가정

## 3. 성과 분석
### 3.1 수익성
- Total Return, CAGR 추이
- 월간/분기별 성과
- 벤치마크 비교

### 3.2 위험성
- Volatility, MDD, VaR
- 회복 시간 분석
- 베타, 상관성

## 4. 효율성 평가
- Sharpe, Calmar, Sortino
- Recovery Factor
- 승패율 분석

## 5. 실행성 검증
- 거래비용 영향도
- 슬리페이지 추정
- 유동성 요구도

## 6. 시간별 성과 분석
- 구간별 성과 비교
- 시장 환경별 성과
- 과적합 위험 평가

## 7. 리스크 평가
- 극한상황 스트레스 테스트
- 회복력 평가
- 모니터링 필요 항목

## 8. 권장사항 및 최종 등급
- 개선 필요 사항
- 모니터링 포인트
- 최종 등급: [Ready/Conditional/Not Ready]
```

### 6.2 성과 데이터 시트 (`Backtest-Performance-Metrics.csv`)

```
전략명,데이터기간,총수익률(%),CAGR(%),승률(%),샤프지수,최대낙폭(%),MDD회복(days),실행등급
```

### 6.3 시각화 차트 (최소 5개)

```
├─ equity_curve.png: 수익선 추이
├─ drawdown_chart.png: 낙폭 분석
├─ monthly_returns.png: 월간 수익률 분포
├─ risk_metrics_dashboard.png: 위험도 대시보드
├─ performance_by_period.png: 기간별 성과
└─ benchmark_comparison.png: 벤치마크 비교
```

---

## 7. 검수 체크리스트

- [ ] 데이터 검증: 결손, 이상치 확인
- [ ] 성과 지표: 15+ KPI 산출
- [ ] 리포트: 8개 섹션 이상
- [ ] 차트: 5개 이상 시각화
- [ ] 실행성: 거래비용/슬리페이지 포함
- [ ] 등급: 객관적 기준에 따른 평가
- [ ] GAP 분석: Design vs 실제 분석 비교

---

## 8. 의존성 및 주의사항

### 의존성
- ✅ BAR-24: 백테스팅 엔진 (거래 로그 출력)
- ✅ BAR-28: 관심종목 리스트 (검증 대상 종목)
- ✅ 시장 데이터: KOSPI, KOSDAQ 벤치마크

### 주의사항
- ⚠️ 과거 성과 ≠ 미래 보장
- ⚠️ 과적합(Overfitting) 위험
- ⚠️ 시장 환경 변화에 따른 성과 변동
- ⚠️ 거래비용/슬리페이지 추정값의 정확도

---

## 9. 승인 및 서명

| 역할 | 이름 | 승인 | 날짜 |
|------|-----|------|------|
| Stock Analyst | Agent (89c9e8da) | ⏳ | 2026-04-12 |
| Head of Research | - | ⏳ | - |
| CTO/Project Lead | - | ⏳ | - |

---

*최종 업데이트: 2026-04-12 | Status: Design Phase*
