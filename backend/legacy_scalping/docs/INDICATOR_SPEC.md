# 지표 수식 상세 문서 (INDICATOR_SPEC)

## 1. 파란점선 (Blue Dotted Line)

### 의미
주식단테의 '역매공파' 중 **파(破)** 단계의 핵심 기준선.
개인 투자자의 수급만으로는 돌파하기 어려운 **비정상적 가격대**를 설정.
이 선을 주가가 돌파하면 **세력의 개입**을 수학적으로 시사한다.

### 수식

```
파란점선(Lp) = Highest(High, n) - ATR(n) × α
```

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| n (lookback) | 224 | 약 1년 거래일수. 장기 추세 반영 |
| α (multiplier) | 2.0 | 변동성 승수. 높을수록 필터 엄격 |

### 구현 (Python)
```python
highest_high = df['high'].rolling(window=224).max()
atr = calc_atr(df['high'], df['low'], df['close'], 224)
blue_line = highest_high - (atr * 2.0)
```

### 상태 판정
| 상태 | 조건 | 의미 |
|------|------|------|
| below | close < blue_line × 0.98 | 아직 멀리 있음 |
| near | 0.98 ≤ close/blue_line ≤ 1.02 | 돌파 임박 (감시 대상) |
| above | close > blue_line × 1.02 | 돌파 후 안착 |
| breakout | 전일 below/near → 당일 above | 당일 돌파 (매수 신호) |

### 색상 변환 (TradingView)
- 저항 상태 (below): 회색 점선
- 돌파/안착 (above): 파란색 실선

---

## 2. 수박지표 (Watermelon Signal)

### 의미
**세력이 돈을 쓴 흔적**을 감지하는 지표.
거래량 폭증 + 캔들 확장 + 바닥권 위치의 3중 조건이 동시에 충족될 때 발동.
수박지표가 뜬 캔들의 **중심값 = 세력 평균단가 추정치**.

### 수식

```
수박지표(Sw) = (V_t > V̄_n × β) AND ((H - L) > ATR(m) × γ) AND (C < MA224 × buffer)
```

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| n (vol_avg_period) | 20 | 평균 거래량 계산 기간 |
| β (vol_spike_ratio) | 2.5 | 거래량 폭증 배수 (평균 대비) |
| m (atr_period) | 14 | 캔들 변동폭 비교용 ATR 기간 |
| γ (price_move_ratio) | 1.5 | 변동성 확장 계수 |
| buffer (ma224_buffer) | 1.1 | 바닥권 판단 (MA224의 110% 이하) |

### 세력 평단가
```
세력 평단가 = (수박지표 발생 캔들의 High + Low) / 2
```

### 오돌이 기법
수박지표 발생 후 주가가 세력 평단가까지 하락하면 → 매수 기회 (세력이 자기 단가를 방어)

---

## 3. 당일매매 적용 파라미터 조정

원본 문서의 전략은 **스윙/중장기** 기준이므로, 당일매매용으로 다음과 같이 조정:

| 항목 | 원본 (스윙) | 당일매매 | 이유 |
|------|:-----------:|:--------:|------|
| 파란점선 Lookback | 224일 | 224일 유지 | 일봉 기준 계산 후 당일 적용 |
| 수박지표 거래량 배수 | 2.5배 | 3.0배 | 당일 노이즈 필터 강화 |
| 바닥권 기준 | MA224 × 1.1 | MA224 × 1.05 | 더 엄격한 바닥 확인 |
| 손절 | -7% | -2% | 당일 리스크 한도 |
| 익절 | +35% | +3% / +5% | 당일 실현 가능 수준 |
| 보유기간 | 수일~수주 | 당일 청산 | 14:50 강제 청산 |
| 비중 | 분할 3회 | 종목당 10% 일괄 | 빠른 진입/퇴출 |

---

## 4. 보조 지표

### 224일 이동평균선 (MA224)
- 약 1년 평균 거래가
- 주가가 아래에 있으면 **역배열** (하락 추세)
- 수박지표의 바닥권 판단 기준

### 112일 이동평균선 (MA112)
- 약 반년 평균 거래가
- 손절 기준으로 활용 가능

### 코스닥 20일선 (시장 필터)
- 코스닥 지수가 20일선 아래 = 하락장
- 하락장에서는 매매 비중 50% 축소 또는 매매 중단

---

## 5. Pine Script 통합 코드 (TradingView 참조용)

```pinescript
//@version=5
indicator("단테 파란점선 + 수박지표 + 당일매매", overlay=true)

// 파라미터
len = input.int(224, "Lookback")
mult = input.float(2.0, "Multiplier")
vol_mult = input.float(3.0, "Volume Spike Ratio")

// 파란점선
basis = ta.highest(high, len)
atr_val = ta.atr(len)
blue_line = basis - (atr_val * mult)
line_color = close > blue_line ? color.blue : color.new(color.gray, 50)
plot(blue_line, "파란점선", color=line_color, style=plot.style_dots, linewidth=2)

// 수박지표
vol_avg = ta.sma(volume, 20)
vol_spike = volume > vol_avg * vol_mult
price_move = (high - low) > ta.atr(14) * 1.5
ma224 = ta.sma(close, 224)
is_bottom = close < ma224 * 1.1
watermelon = vol_spike and price_move and is_bottom
plotshape(watermelon, "수박🍉", shape.circle, location.belowbar, color.green, size=size.small)

// 돌파 신호
cross_up = ta.crossover(close, blue_line) and volume > vol_avg * 3.0
plotshape(cross_up, "파(破)", shape.triangleup, location.belowbar, color.blue, size=size.normal)

// 배경
bgcolor(close > ma224 ? color.new(color.blue, 95) : color.new(color.red, 95))

// MA
plot(ma224, "MA224", color=color.orange, linewidth=1)
plot(ta.sma(close, 112), "MA112", color=color.purple, linewidth=1)
```
