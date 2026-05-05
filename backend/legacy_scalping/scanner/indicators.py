"""
주식단테 지표 계산 엔진
- 파란점선 (Blue Dotted Line): 장기 변동성 기반 추세 저항/지지선
- 수박지표 (Watermelon Signal): 세력 매집 신호 감지
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional


@dataclass
class IndicatorConfig:
    """지표 파라미터 설정"""
    # 파란점선
    blue_lookback: int = 224
    blue_atr_period: int = 224
    blue_multiplier: float = 2.0
    # 수박지표
    wm_vol_avg_period: int = 20
    wm_vol_spike_ratio: float = 2.5
    wm_atr_period: int = 14
    wm_price_move_ratio: float = 1.5
    wm_ma224_buffer: float = 1.1
    # 이동평균선
    ma224_period: int = 224
    ma112_period: int = 112


@dataclass
class IndicatorResult:
    """종목별 지표 계산 결과"""
    code: str
    name: str
    close: float
    blue_line: float
    blue_line_status: str       # "below" | "near" | "above" | "breakout"
    ma224: float
    ma112: float
    watermelon_signal: bool
    watermelon_price: Optional[float]   # 수박지표 발생 시 세력 평단가
    volume_ratio: float         # 현재 거래량 / 20일 평균
    score: float                # 종합 점수 (높을수록 우선)


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Average True Range 계산"""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()


def calc_blue_dotted_line(df: pd.DataFrame, config: IndicatorConfig) -> pd.Series:
    """
    파란점선 계산
    
    수식: blue_line = highest(high, n) - ATR(n) * α
    
    - n: lookback period (기본 224일)
    - α: multiplier (기본 2.0)
    
    의미: 장기 고점에서 평균 변동폭의 α배를 뺀 값.
          이 선 위로 주가가 올라오면 비정상적 힘(세력)의 개입을 의미.
    """
    highest_high = df['high'].rolling(window=config.blue_lookback, min_periods=1).max()
    atr = calc_atr(df['high'], df['low'], df['close'], config.blue_atr_period)
    blue_line = highest_high - (atr * config.blue_multiplier)
    return blue_line


def calc_watermelon_signal(df: pd.DataFrame, config: IndicatorConfig) -> pd.DataFrame:
    """
    수박지표 계산
    
    발생 조건 (3가지 모두 충족):
    1. 거래량 폭증: V(t) > avg_V(n) * β
    2. 캔들 변동폭 확장: (high - low) > ATR(m) * γ  
    3. 바닥권 확인: close < MA(224) * buffer
    
    수박지표가 뜬 캔들의 중심값 = 세력 평단가 추정치
    """
    # 평균 거래량
    vol_avg = df['volume'].rolling(window=config.wm_vol_avg_period, min_periods=1).mean()
    vol_spike = df['volume'] > (vol_avg * config.wm_vol_spike_ratio)
    
    # 캔들 변동폭
    atr = calc_atr(df['high'], df['low'], df['close'], config.wm_atr_period)
    price_move = (df['high'] - df['low']) > (atr * config.wm_price_move_ratio)
    
    # 바닥권 (224일선 근처 또는 아래)
    ma224 = df['close'].rolling(window=config.ma224_period, min_periods=1).mean()
    is_bottom = df['close'] < (ma224 * config.wm_ma224_buffer)
    
    # 3조건 결합
    signal = vol_spike & price_move & is_bottom
    
    # 세력 평단가 (수박지표 발생 캔들의 중심값)
    watermelon_price = np.where(signal, (df['high'] + df['low']) / 2, np.nan)
    
    result = df.copy()
    result['watermelon_signal'] = signal
    result['watermelon_price'] = watermelon_price
    result['volume_ratio'] = df['volume'] / vol_avg
    
    return result


def calc_blue_line_status(close: float, blue_line: float) -> str:
    """
    파란점선 대비 주가 상태 판정
    
    - "below":    blue_line * 0.98 미만 (아직 멀리 있음)
    - "near":     blue_line * 0.98 ~ 1.02 범위 (돌파 임박)  
    - "above":    blue_line 위 안착 (돌파 후 안착)
    - "breakout": 당일 돌파 (매수 신호)
    """
    ratio = close / blue_line if blue_line > 0 else 0
    if ratio < 0.98:
        return "below"
    elif ratio < 1.02:
        return "near"
    else:
        return "above"


def analyze_stock(
    code: str,
    name: str,
    df: pd.DataFrame,
    config: IndicatorConfig = IndicatorConfig()
) -> Optional[IndicatorResult]:
    """
    단일 종목 지표 분석
    
    Args:
        code: 종목코드 (6자리)
        name: 종목명
        df: OHLCV DataFrame (columns: date, open, high, low, close, volume)
        config: 지표 파라미터
    
    Returns:
        IndicatorResult 또는 데이터 부족 시 None
    """
    if len(df) < config.blue_lookback:
        return None
    
    # 파란점선
    blue_line_series = calc_blue_dotted_line(df, config)
    blue_line = blue_line_series.iloc[-1]
    
    # 이동평균선
    ma224 = df['close'].rolling(window=config.ma224_period).mean().iloc[-1]
    ma112 = df['close'].rolling(window=config.ma112_period).mean().iloc[-1]
    
    # 수박지표 (최근 60거래일 내 발생 여부)
    wm_df = calc_watermelon_signal(df, config)
    recent_wm = wm_df.tail(60)
    has_watermelon = recent_wm['watermelon_signal'].any()
    
    # 수박지표 발생 시 가장 최근 세력 평단가
    wm_price = None
    if has_watermelon:
        wm_rows = recent_wm[recent_wm['watermelon_signal'] == True]
        if len(wm_rows) > 0:
            wm_price = wm_rows['watermelon_price'].iloc[-1]
    
    close = df['close'].iloc[-1]
    status = calc_blue_line_status(close, blue_line)
    vol_ratio = wm_df['volume_ratio'].iloc[-1]
    
    # 종합 점수 계산
    score = _calc_score(close, blue_line, status, has_watermelon, vol_ratio, ma224)
    
    return IndicatorResult(
        code=code,
        name=name,
        close=close,
        blue_line=blue_line,
        blue_line_status=status,
        ma224=ma224,
        ma112=ma112,
        watermelon_signal=has_watermelon,
        watermelon_price=wm_price,
        volume_ratio=vol_ratio,
        score=score,
    )


def _calc_score(
    close: float,
    blue_line: float,
    status: str,
    has_watermelon: bool,
    vol_ratio: float,
    ma224: float,
) -> float:
    """
    종합 점수 산출 (0~100)
    
    가중치:
    - 파란점선 근접도: 40점 (near > above > below)
    - 수박지표 발생: 30점
    - 거래량 비율: 20점
    - 바닥권 위치: 10점
    """
    score = 0.0
    
    # 파란점선 근접도 (40점)
    if status == "near":
        score += 40.0
    elif status == "above":
        score += 35.0
    elif status == "below":
        ratio = close / blue_line if blue_line > 0 else 0
        score += max(0, ratio * 40 - 5)
    
    # 수박지표 (30점)
    if has_watermelon:
        score += 30.0
    
    # 거래량 비율 (20점)
    vol_score = min(vol_ratio / 3.0, 1.0) * 20.0
    score += vol_score
    
    # 바닥권 위치 (10점) - MA224 대비 저평가일수록 높음
    if ma224 > 0:
        undervalue_ratio = 1 - (close / ma224)
        if undervalue_ratio > 0:
            score += min(undervalue_ratio * 50, 10.0)
    
    return round(score, 2)


# ---------------------------------------------------------------------------
# Pine Script 참조 (TradingView용 - 별도 사용)
# ---------------------------------------------------------------------------
PINE_SCRIPT_REFERENCE = """
//@version=5
indicator("Dante Blue Dotted + Watermelon", overlay=true)

// === 파란점선 ===
len = input.int(224, "Lookback Period")
mult = input.float(2.0, "Multiplier")
basis = ta.highest(high, len)
atr_val = ta.atr(len)
blue_line = basis - (atr_val * mult)
line_color = close > blue_line ? color.blue : color.new(color.gray, 50)
plot(blue_line, "Blue Dotted Line", color=line_color, style=plot.style_dots, linewidth=2)

// === 수박지표 ===
vol_avg = ta.sma(volume, 20)
vol_spike = volume > vol_avg * 2.5
price_move = (high - low) > ta.atr(14) * 1.5
ma224 = ta.sma(close, 224)
is_bottom = close < ma224 * 1.1
watermelon = vol_spike and price_move and is_bottom
plotshape(watermelon, "Watermelon", shape.circle, location.belowbar, color.green, size=size.small)

// === 파란점선 돌파 신호 ===
cross_up = ta.crossover(close, blue_line)
vol_confirm = volume > vol_avg * 3.0
entry_signal = cross_up and vol_confirm
plotshape(entry_signal, "Entry", shape.triangleup, location.belowbar, color.blue, size=size.normal)

// === 배경색 ===
bgcolor(close > ma224 ? color.new(color.blue, 95) : color.new(color.red, 95))
"""
