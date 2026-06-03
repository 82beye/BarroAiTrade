"""공통 지표 계산 helper.

BAR-OPS-09 Phase 7: 4 strategy (f_zone, blue_line, gold_zone, swing_38) 와
IntradaySimulator 에 복제됐던 `_atr_pct` 본문을 단일 함수로 통합.

- 4 strategy 의 `_atr_pct` staticmethod 는 호환 wrapper 로 유지 (float 반환).
- IntradaySimulator 의 module-level `_atr_pct` 도 wrapper 로 유지 (Decimal 반환).
- 신규 strategy 는 직접 이 모듈을 import 해서 사용.

2026-06-03 (BAR-OPS-10): 슈퍼트렌드 + 멀티 타임프레임 RSI 확인 필터용 헬퍼 추가.
  RSI(Wilder)·RSI 시그널선·골든/데드크로스·상위 타임프레임(HTF) 리샘플·룩어헤드-free
  정렬을 단일 소스로 제공 → supertrend 전략 레이어와 auto_trader 가 동일 코드 재사용.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from backend.models.market import OHLCV


def atr_pct(candles: List[OHLCV], n: int = 14) -> float:
    """최근 n봉의 True Range 평균 / 마지막 close 비율 (예: 0.025 = 2.5%).

    종목별 변동성 측정. 분봉/일봉 무관 동일 공식.
    저변동·고가주 가짜 시그널 차단(변동성 필터) 의 핵심 지표.

    Args:
        candles: OHLCV 리스트 (오래된 → 최신 순).
        n: True Range 평균 봉 수 (기본 14).

    Returns:
        ATR% 값 (0.0 ~ 1.0 범위 일반적). 데이터 부족 또는 last_close <= 0 시 0.0.
    """
    if len(candles) < 2:
        return 0.0
    n = min(n, len(candles) - 1)
    trs: list[float] = []
    for i in range(1, n + 1):
        c = candles[-i]
        prev = candles[-i - 1]
        tr = max(
            c.high - c.low,
            abs(c.high - prev.close),
            abs(c.low - prev.close),
        )
        trs.append(tr)
    atr = sum(trs) / len(trs) if trs else 0.0
    last_close = candles[-1].close
    if last_close <= 0:
        return 0.0
    return atr / last_close


# ─── RSI (Relative Strength Index) ───────────────────────────────────────────
def compute_rsi(candles: List[OHLCV], period: int = 14) -> List[float]:
    """Wilder RSI(0~100) — candles 와 동일 길이. 첫 period 봉 = 50.0(중립).

    backend.core.scanner.indicators.TechnicalIndicators.calculate_rsi 와 **수치 동일**
    (list 기반 API). 두 구현이 갈라지지 않도록 test_rsi_indicators 의 parity 테스트로 고정.

    Args:
        candles: OHLCV 리스트 (오래된 → 최신 순).
        period: RSI 기간 (기본 14).

    Returns:
        RSI 값 list[float] (candles 와 동일 길이). 데이터 부족(< period+1) 시 전부 50.0.
    """
    closes = [float(c.close) for c in candles]
    n = len(closes)
    if n < period + 1:
        return [50.0] * n
    deltas = [closes[i + 1] - closes[i] for i in range(n - 1)]
    seed = deltas[:period]
    up = sum(d for d in seed if d >= 0) / period
    down = -sum(d for d in seed if d < 0) / period
    rsi = [0.0] * n
    rsi[period] = 100.0 if down == 0 else 100.0 - 100.0 / (1.0 + up / down)
    for i in range(period + 1, n):
        delta = deltas[i - 1]
        if delta > 0:
            up = (up * (period - 1) + delta) / period
            down = (down * (period - 1)) / period
        else:
            up = (up * (period - 1)) / period
            down = (down * (period - 1) - delta) / period
        rsi[i] = 100.0 if down == 0 else 100.0 - 100.0 / (1.0 + up / down)
    for k in range(period):
        rsi[k] = 50.0
    return rsi


def rsi_signal_line(rsi: Sequence[float], signal_period: int = 9) -> List[float]:
    """RSI 시그널선 = SMA(rsi, signal_period). 앞 (signal_period-1) 구간은 nan.

    네이버/HTS RSI 패널의 '시그널' 선과 동일(단순이동평균). 골든/데드크로스 판정 기준선.
    """
    n = len(rsi)
    out: List[float] = [float("nan")] * n
    p = signal_period
    if p <= 0 or n < p:
        return out
    run = sum(rsi[:p])
    out[p - 1] = run / p
    for i in range(p, n):
        run += rsi[i] - rsi[i - p]
        out[i] = run / p
    return out


def rsi_cross_state(
    rsi: Sequence[float],
    signal: Optional[Sequence[float]] = None,
    *,
    mode: str = "signal_cross",
    centerline: float = 50.0,
    min_level: float = 0.0,
    max_level: float = 100.0,
) -> Tuple[List[bool], List[bool]]:
    """(golden, dead) bool 시계열 — rsi 와 동일 길이.

    mode:
      - "signal_cross": RSI 가 시그널선을 상향(golden)/하향(dead) 돌파한 봉. signal 없으면
        기본 SMA9 로 내부 계산. (네이버 골든/데드크로스 정의)
      - "centerline":   RSI 가 centerline(기본 50)을 상향/하향 돌파한 봉.
      - "level":        상태 게이트(이벤트 아님). golden[i]=min_level≤rsi≤max_level,
                        dead[i]=rsi<min_level.

    nan(시그널선 초기 구간 등)이 끼면 해당 봉은 False.
    """
    n = len(rsi)
    golden = [False] * n
    dead = [False] * n
    if n == 0:
        return golden, dead

    def _ok(*vals: float) -> bool:
        return all(v == v for v in vals)  # nan 아님 (nan != nan)

    if mode == "level":
        for i in range(n):
            r = rsi[i]
            if not _ok(r):
                continue
            golden[i] = (r >= min_level) and (r <= max_level)
            dead[i] = r < min_level
        return golden, dead

    if mode == "centerline":
        for i in range(1, n):
            a, b = rsi[i - 1], rsi[i]
            if not _ok(a, b):
                continue
            golden[i] = a <= centerline and b > centerline
            dead[i] = a >= centerline and b < centerline
        return golden, dead

    # signal_cross (기본)
    sig = list(signal) if signal is not None else rsi_signal_line(rsi)
    for i in range(1, n):
        ra, rb = rsi[i - 1], rsi[i]
        sa, sb = sig[i - 1], sig[i]
        if not _ok(ra, rb, sa, sb):
            continue
        golden[i] = ra <= sa and rb > sb
        dead[i] = ra >= sa and rb < sb
    return golden, dead


# ─── 상위 타임프레임(HTF) 리샘플 + 룩어헤드-free 정렬 ────────────────────────
_SESSION_ANCHOR_MIN = 9 * 60   # 09:00 KST 정규장 시작 — HTF 버킷 기준점


def _offset_min(ts) -> int:
    """09:00 기준 분(分) 오프셋. (장 시작 09:00 → 0, 09:10 → 10)"""
    return (ts.hour * 60 + ts.minute) - _SESSION_ANCHOR_MIN


def _bucket_index(ts, tf_mult: int, base_minutes: int) -> int:
    """timestamp 가 속한 HTF 버킷 인덱스 (09:00 앵커, 날짜 무관 분 단위)."""
    return _offset_min(ts) // (tf_mult * base_minutes)


def resample_htf(candles: List[OHLCV], tf_mult: int, *, base_minutes: int = 5) -> List[OHLCV]:
    """5분봉(base_minutes) → tf_mult 배 상위 타임프레임 봉으로 **벽시계 버킷** 집계.

    tf_mult: 1=passthrough(5분), 2=10분, 3=15분, 6=30분.
    버킷 = (날짜, 09:00 기준 bucket_index). 장마감 단일가(15:30~) 격자 불연속
    (…151500 다음 152000 없이 153000 점프)에서도 인덱스가 아닌 timestamp 로 묶으므로
    안전. OHLC=open(첫)/high(max)/low(min)/close(끝), volume=합, timestamp=버킷 마지막
    5분봉 ts. 날짜별 키 → 15:35 종가와 익일 09:00 이 한 버킷에 안 섞인다.
    """
    n = len(candles)
    if n == 0 or tf_mult <= 1:
        return list(candles)
    out: List[OHLCV] = []
    cur_key = None
    grp: List[OHLCV] = []

    def _flush() -> None:
        if not grp:
            return
        out.append(OHLCV(
            symbol=grp[0].symbol,
            timestamp=grp[-1].timestamp,
            open=grp[0].open,
            high=max(c.high for c in grp),
            low=min(c.low for c in grp),
            close=grp[-1].close,
            volume=sum(c.volume for c in grp),
            market_type=grp[0].market_type,
        ))

    for c in candles:
        key = (c.timestamp.date(), _bucket_index(c.timestamp, tf_mult, base_minutes))
        if key != cur_key:
            _flush()
            grp = [c]
            cur_key = key
        else:
            grp.append(c)
    _flush()
    return out


def htf_rsi_at(
    candles: List[OHLCV], i: int, tf_mult: int, *, base_minutes: int = 5,
) -> List[OHLCV]:
    """결정봉 i 시점에 **완성된** HTF 봉들만 반환 (룩어헤드 제거).

    candles[:i+1] 만 사용(미래 봉 미참조). 마지막 HTF 버킷이 아직 형성 중
    (다음 5분 슬롯이 같은 버킷에 속함)이면 그 봉을 drop → 닫힌 HTF 봉만 남긴다.
    형성 중 봉을 두면 close 가 매 5분 갱신돼 크로스가 깜빡(=룩어헤드)이므로 반드시 제거.
    tf_mult<=1 이면 passthrough(각 5분봉이 곧 완성봉).
    """
    if i < 0:
        return []
    base = candles[: i + 1]
    if tf_mult <= 1:
        return list(base)
    htf = resample_htf(base, tf_mult, base_minutes=base_minutes)
    if not htf:
        return htf
    last = candles[i]
    cur_b = _bucket_index(last.timestamp, tf_mult, base_minutes)
    next_b = (_offset_min(last.timestamp) + base_minutes) // (tf_mult * base_minutes)
    if next_b <= cur_b:        # 다음 5분 슬롯이 같은 버킷 → 마지막 HTF 봉 형성 중 → drop
        htf = htf[:-1]
    return htf


def _htf_cross_series(
    candles: List[OHLCV], i: int, tf_mult: int, *, period: int,
    signal_period: int, mode: str, min_level: float, max_level: float,
    base_minutes: int,
) -> Optional[Tuple[List[bool], List[bool]]]:
    """i 시점 완성 HTF 봉 → (golden, dead). 데이터 부족 시 None."""
    htf = htf_rsi_at(candles, i, tf_mult, base_minutes=base_minutes)
    need = period + 1 + (signal_period if mode == "signal_cross" else 0)
    if len(htf) < need:
        return None
    rsi = compute_rsi(htf, period)
    sig = rsi_signal_line(rsi, signal_period) if mode == "signal_cross" else None
    return rsi_cross_state(rsi, sig, mode=mode, min_level=min_level, max_level=max_level)


def htf_rsi_confirms_long(
    candles: List[OHLCV], *, i: int, tf_mult: int, period: int,
    signal_period: int, mode: str, lookback: int,
    min_level: float, max_level: float, base_minutes: int = 5,
) -> bool:
    """결정봉 i 에서 상위 타임프레임 RSI 가 롱 진입을 '확인'하면 True.

    HTF RSI 산출 불가(데이터 부족)면 False(보수적 — 확인 없으면 진입 안 함).
    signal_cross/centerline: 최근 lookback HTF봉 내 golden 이벤트. level: 현재 상태.
    """
    res = _htf_cross_series(
        candles, i, tf_mult, period=period, signal_period=signal_period,
        mode=mode, min_level=min_level, max_level=max_level, base_minutes=base_minutes,
    )
    if res is None:
        return False
    golden, _dead = res
    if not golden:
        return False
    if mode == "level":
        return golden[-1]
    lb = max(1, lookback)
    return any(golden[-lb:])


def htf_rsi_confirms_exit(
    candles: List[OHLCV], *, i: int, tf_mult: int, period: int,
    signal_period: int, mode: str, lookback: int,
    min_level: float, max_level: float, base_minutes: int = 5,
) -> bool:
    """결정봉 i 에서 상위 타임프레임 RSI 가 청산(데드크로스/레짐붕괴)을 알리면 True.

    산출 불가면 False(슈퍼트렌드 SELL 이 안전망 — RSI 만으로 강제청산 안 함).
    signal_cross/centerline: 최근 lookback HTF봉 내 dead 이벤트. level: 현재 상태.
    """
    res = _htf_cross_series(
        candles, i, tf_mult, period=period, signal_period=signal_period,
        mode=mode, min_level=min_level, max_level=max_level, base_minutes=base_minutes,
    )
    if res is None:
        return False
    _golden, dead = res
    if not dead:
        return False
    if mode == "level":
        return dead[-1]
    lb = max(1, lookback)
    return any(dead[-lb:])


__all__ = [
    "atr_pct",
    "compute_rsi",
    "rsi_signal_line",
    "rsi_cross_state",
    "resample_htf",
    "htf_rsi_at",
    "htf_rsi_confirms_long",
    "htf_rsi_confirms_exit",
]
