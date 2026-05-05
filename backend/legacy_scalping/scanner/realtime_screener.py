"""
역매공파 실시간 검색기 (2-Pass Screening)

Pass 1: 캐시 전용 — 전종목 대상으로 API 호출 없이 필터링 (~50-100 후보)
Pass 2: 실시간 확인 — 후보 종목에 get_current_price() 호출 → 최종 결과

조건 수식: A∧B∧((C∧D)∨(E∧F))∧G∧H∧I∧J∧K∧L∧M∧N∧O
"""

import logging
from datetime import datetime, time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from scanner.bb_ichimoku import (
    calc_bollinger_band,
    calc_ichimoku_spans,
    check_golden_cross,
)
from scanner.ohlcv_cache import OHLCVCache

logger = logging.getLogger(__name__)


@dataclass
class YeokMaeGongPaResult:
    """역매공파 검색 결과"""
    code: str
    name: str
    current_price: float
    bb20_upper: float
    bb40_upper: float
    bb20_middle: float
    bb40_middle: float
    ichimoku_span1: float
    ichimoku_span2: float
    ma60: float
    ma112: float
    ma224: float
    ma448: float
    avg_trade_value_5d: float       # 5일 평균 거래대금 (원)
    max_close_vs_open_pct_5d: float # 5봉 이내 최대 시가대비종가 %
    score: float
    conditions_met: List[str] = field(default_factory=list)
    degraded: bool = False          # True = 데이터 부족으로 일부 조건 미검증


class RealtimeScreener:
    """역매공파 실시간 검색기"""

    # 전체 역매공파 조건 평가에 필요한 데이터 길이 (MA448)
    MIN_DATA_LENGTH_FULL = 448
    # 최소 데이터 길이 (BB40 + 비교용 3봉)
    MIN_DATA_LENGTH_HARD = 43

    def __init__(self, kiwoom_api, config: dict):
        self.api = kiwoom_api
        self.config = config

        rs_config = config.get('realtime_screener', {})
        self.enabled = rs_config.get('enabled', True)
        self.scan_interval = rs_config.get('scan_interval_minutes', 1) * 60  # seconds
        self.rescan_on_close = rs_config.get('rescan_on_position_close', True)
        self.max_candidates = rs_config.get('max_candidates', 15)
        self.min_avg_trade_value = rs_config.get('min_avg_trade_value', 3_000_000_000)
        self.min_close_vs_open_pct = rs_config.get('min_close_vs_open_pct', 7.0)

        cache_dir = config.get('scanner', {}).get('cache_dir', './data/ohlcv_cache')
        self.cache = OHLCVCache(cache_dir)

        # 검색 대상 유니버스: [{code, name}, ...]
        self._universe: List[dict] = []

        # 마지막 스캔 시각
        self._last_scan_time: Optional[datetime] = None

        # Pass 1 결과 (code → df 캐시)
        self._pass1_candidates: Dict[str, dict] = {}

    def set_stock_universe(self, stocks: List[dict]):
        """
        검색 대상 유니버스 설정

        Args:
            stocks: DailyScreener._apply_basic_filters() 결과
                    [{"code": "005930", "name": "삼성전자", ...}, ...]
        """
        self._universe = stocks
        logger.info(f"역매공파 유니버스 설정: {len(stocks)}종목")

    def should_scan(self, force: bool = False) -> bool:
        """스캔 시점 판단 (scan_interval=0이면 주기적 자동 재스캔 비활성화)"""
        if not self.enabled:
            return False
        if force:
            return True
        if self.scan_interval <= 0:
            return False
        if self._last_scan_time is None:
            return True
        elapsed = (datetime.now() - self._last_scan_time).total_seconds()
        return elapsed >= self.scan_interval

    async def run_scan(self, force: bool = False) -> Dict[str, YeokMaeGongPaResult]:
        """
        2-Pass 역매공파 스크리닝 실행

        Returns:
            {code: YeokMaeGongPaResult} dict
        """
        if not self.should_scan(force):
            return {}

        if not self._universe:
            logger.warning("역매공파: 유니버스 미설정")
            return {}

        logger.info(f"역매공파 스캔 시작 (유니버스: {len(self._universe)}종목)")
        scan_start = datetime.now()

        # Pass 1: 캐시 전용 필터
        candidates = self._pass1_cache_screening()
        logger.info(f"역매공파 Pass1 완료: {len(candidates)}종목 후보")

        # Pass 2: 실시간 가격 확인
        results = await self._pass2_live_confirmation(candidates)
        logger.info(f"역매공파 Pass2 완료: {len(results)}종목 통과")

        self._last_scan_time = datetime.now()
        elapsed = (self._last_scan_time - scan_start).total_seconds()
        logger.info(f"역매공파 스캔 완료: {elapsed:.1f}초 소요")

        return results

    def _pass1_cache_screening(self) -> Dict[str, dict]:
        """
        Pass 1: 캐시 전용 필터링 (API 호출 없음)

        평가 조건: M(역배열) → A, B, G(전일), H(전일), I, K, L, N, O
        조건 M이 ~90% 필터링하므로 먼저 체크

        데이터 부족 시 adaptive mode:
          - 448일+: 전체 15개 조건 평가 (정상 모드)
          - 112~447일: M 조건 스킵 (역배열 검증 불가)
          - 78~111일: M, O 조건 스킵
          - 60~77일: M, O, K, L 조건 스킵
          - 43~59일: M, O, K, L, N 조건 스킵
          - <43일: 분석 불가 (BB40 최소 요구)
        """
        candidates = {}
        skipped_short = 0
        degraded_count = 0

        data_lengths = []
        for stock in self._universe:
            code = stock['code']
            df = self.cache.load(code)
            if df is not None:
                data_lengths.append(len(df))

        if data_lengths:
            avg_len = sum(data_lengths) / len(data_lengths)
            max_len = max(data_lengths)
            if avg_len < self.MIN_DATA_LENGTH_FULL:
                logger.warning(
                    f"역매공파: 캐시 데이터 부족 — 평균 {avg_len:.0f}일 "
                    f"(최대 {max_len}일, 필요 {self.MIN_DATA_LENGTH_FULL}일). "
                    f"일부 조건을 건너뛰는 adaptive mode로 동작합니다."
                )

        for stock in self._universe:
            code = stock['code']
            name = stock.get('name', '')

            df = self.cache.load(code)
            if df is None or len(df) < self.MIN_DATA_LENGTH_HARD:
                skipped_short += 1
                continue

            data_len = len(df)
            close = df['close']
            high = df['high']
            low = df['low']

            # 데이터 길이별 평가 가능 조건 결정
            can_eval_m = data_len >= 448   # MA448 필요
            can_eval_o = data_len >= 112   # MA112 필요
            can_eval_kl = data_len >= 78   # 일목균형표 (52+26 shift)
            can_eval_n = data_len >= 60    # MA60 필요
            is_degraded = not can_eval_m

            # --- 조건 M: 역배열 (112이평 < 224이평 < 448이평) ---
            ma112_val = np.nan
            ma224_val = np.nan
            ma448_val = np.nan
            ma112 = None

            if can_eval_m:
                ma112 = close.rolling(112).mean()
                ma224 = close.rolling(224).mean()
                ma448 = close.rolling(448).mean()
                ma112_val = ma112.iloc[-1]
                ma224_val = ma224.iloc[-1]
                ma448_val = ma448.iloc[-1]
                if pd.isna(ma112_val) or pd.isna(ma224_val) or pd.isna(ma448_val):
                    continue
                if not (ma112_val < ma224_val < ma448_val):
                    continue
            elif can_eval_o:
                # MA112까지는 계산 가능
                ma112 = close.rolling(112).mean()
                ma112_val = ma112.iloc[-1]

            # --- 조건 N: 종가 > 60이평 ---
            ma60_val = np.nan
            if can_eval_n:
                ma60 = close.rolling(60).mean()
                ma60_val = ma60.iloc[-1]
                if pd.isna(ma60_val) or close.iloc[-1] <= ma60_val:
                    continue

            # --- 조건 I: 5일 평균거래대금 >= 30억 (금일 제외) ---
            if len(df) < 6:
                continue
            trade_value = close * df['volume']
            avg_tv_5d = trade_value.iloc[-6:-1].mean()
            if pd.isna(avg_tv_5d) or avg_tv_5d < self.min_avg_trade_value:
                continue

            # --- BB(20,2), BB(40,2) 계산 ---
            bb20_upper, bb20_mid, _ = calc_bollinger_band(close, 20, 2.0)
            bb40_upper, bb40_mid, _ = calc_bollinger_band(close, 40, 2.0)

            # --- 조건 A: [일]1봉전 BB(20,2) 종가 >= 중심선 ---
            if pd.isna(bb20_mid.iloc[-2]) or close.iloc[-2] < bb20_mid.iloc[-2]:
                continue

            # --- 조건 B: [일]1봉전 BB(40,2) 종가 >= 중심선 ---
            if pd.isna(bb40_mid.iloc[-2]) or close.iloc[-2] < bb40_mid.iloc[-2]:
                continue

            # --- 조건 G(전일 기준): BB(20,2) 상한선 1봉 연속상승 ---
            if (
                pd.isna(bb20_upper.iloc[-2])
                or pd.isna(bb20_upper.iloc[-3])
                or bb20_upper.iloc[-2] <= bb20_upper.iloc[-3]
            ):
                continue

            # --- 조건 H(전일 기준): BB(40,2) 상한선 1봉 연속상승 ---
            if (
                pd.isna(bb40_upper.iloc[-2])
                or pd.isna(bb40_upper.iloc[-3])
                or bb40_upper.iloc[-2] <= bb40_upper.iloc[-3]
            ):
                continue

            # --- 조건 K, L: 일목균형표 주가 > 선행스팬1, 선행스팬2 ---
            span1_val = np.nan
            span2_val = np.nan
            if can_eval_kl:
                span1, span2 = calc_ichimoku_spans(high, low, close)
                span1_val = span1.iloc[-1]
                span2_val = span2.iloc[-1]
                if pd.isna(span1_val) or close.iloc[-1] <= span1_val:
                    continue
                if pd.isna(span2_val) or close.iloc[-1] <= span2_val:
                    continue

            # --- 조건 O: 1이평(종가) 골든크로스 112이평 (4봉 이내) ---
            if can_eval_o and ma112 is not None:
                ma1 = close
                if not check_golden_cross(ma1, ma112, lookback=4):
                    continue

            if is_degraded:
                degraded_count += 1

            # Pass 1 통과 → 후보에 추가
            candidates[code] = {
                'name': name,
                'df': df,
                'bb20_upper': bb20_upper,
                'bb20_mid': bb20_mid,
                'bb40_upper': bb40_upper,
                'bb40_mid': bb40_mid,
                'span1': span1_val,
                'span2': span2_val,
                'ma60': ma60_val,
                'ma112': ma112_val,
                'ma224': ma224_val,
                'ma448': ma448_val,
                'avg_tv_5d': avg_tv_5d,
                'degraded': is_degraded,
            }

        if skipped_short > 0:
            logger.info(f"역매공파 Pass1: {skipped_short}종목 데이터 부족으로 제외")
        if degraded_count > 0:
            logger.warning(
                f"역매공파 Pass1: {degraded_count}종목이 adaptive mode로 통과 "
                f"(일부 조건 미검증 — 캐시 업데이트 필요: --update-cache)"
            )

        return candidates

    async def _pass2_live_confirmation(
        self,
        candidates: Dict[str, dict],
    ) -> Dict[str, YeokMaeGongPaResult]:
        """
        Pass 2: 실시간 가격으로 오늘 캔들 반영 후 최종 조건 평가

        평가 조건: C, D, E, F, J + G, H 재검증 (0봉전 기준)
        """
        results = {}

        for code, info in candidates.items():
            try:
                price_data = await self.api.get_current_price(code)
            except Exception as e:
                logger.debug(f"역매공파 Pass2 가격조회 실패 [{code}]: {e}")
                continue

            cur_price = price_data['price']
            cur_open = price_data['open']
            cur_high = price_data['high']
            cur_low = price_data['low']
            cur_volume = price_data['volume']

            if cur_price <= 0 or cur_open <= 0:
                continue

            # 오늘 캔들을 df에 append하여 BB 재계산
            df = info['df']
            today_row = pd.DataFrame([{
                'date': pd.Timestamp(datetime.now().date()),
                'open': cur_open,
                'high': cur_high,
                'low': cur_low,
                'close': cur_price,
                'volume': cur_volume,
            }])
            df_live = pd.concat([df, today_row], ignore_index=True)

            close_live = df_live['close']

            # BB 재계산 (오늘 포함)
            bb20_upper, bb20_mid, _ = calc_bollinger_band(close_live, 20, 2.0)
            bb40_upper, bb40_mid, _ = calc_bollinger_band(close_live, 40, 2.0)

            bb20_upper_today = bb20_upper.iloc[-1]
            bb40_upper_today = bb40_upper.iloc[-1]
            bb20_mid_today = bb20_mid.iloc[-1]
            bb40_mid_today = bb40_mid.iloc[-1]

            if pd.isna(bb20_upper_today) or pd.isna(bb40_upper_today):
                continue

            conditions_met = []

            # --- 조건 C: [일]0봉전 BB(20,2) 종가 >= 상한선 ---
            cond_c = cur_price >= bb20_upper_today
            # --- 조건 D: [일]0봉전 BB(40,2) 종가 >= 상한선 ---
            cond_d = cur_price >= bb40_upper_today

            # --- 조건 E: [일]0봉전 BB(20,2) 종가 상한선 상향돌파 ---
            # 전일 종가 < 전일 BB20 상한선 AND 금일 종가 >= 금일 BB20 상한선
            prev_close = df['close'].iloc[-1]
            bb20_upper_prev = info['bb20_upper'].iloc[-1]
            bb40_upper_prev = info['bb40_upper'].iloc[-1]
            cond_e = prev_close < bb20_upper_prev and cur_price >= bb20_upper_today
            # --- 조건 F: [일]0봉전 BB(40,2) 종가 상한선 상향돌파 ---
            cond_f = prev_close < bb40_upper_prev and cur_price >= bb40_upper_today

            # 조건 수식: (C∧D) ∨ (E∧F)
            if not ((cond_c and cond_d) or (cond_e and cond_f)):
                continue

            if cond_c:
                conditions_met.append('C')
            if cond_d:
                conditions_met.append('D')
            if cond_e:
                conditions_met.append('E')
            if cond_f:
                conditions_met.append('F')

            # --- 조건 G 재검증 (0봉전): BB(20,2) 상한선 오늘 > 전일 ---
            if bb20_upper_today <= bb20_upper_prev:
                continue
            conditions_met.append('G')

            # --- 조건 H 재검증 (0봉전): BB(40,2) 상한선 오늘 > 전일 ---
            if bb40_upper_today <= bb40_upper_prev:
                continue
            conditions_met.append('H')

            # --- 조건 J: 5봉 이내 시가대비종가 7% 이상 ---
            # 최근 5봉 (오늘 포함)
            max_cv_pct = 0.0
            for i in range(max(0, len(df_live) - 5), len(df_live)):
                o = df_live['open'].iloc[i]
                c = df_live['close'].iloc[i]
                if o > 0:
                    pct = (c - o) / o * 100
                    max_cv_pct = max(max_cv_pct, pct)

            if max_cv_pct < self.min_close_vs_open_pct:
                continue
            conditions_met.append('J')

            # 모든 조건 통과 → 점수 계산
            score = self._calc_ymgp_score(
                cur_price, bb20_upper_today, bb40_upper_today,
                info['avg_tv_5d'], max_cv_pct,
            )

            results[code] = YeokMaeGongPaResult(
                code=code,
                name=info['name'],
                current_price=cur_price,
                bb20_upper=bb20_upper_today,
                bb40_upper=bb40_upper_today,
                bb20_middle=bb20_mid_today,
                bb40_middle=bb40_mid_today,
                ichimoku_span1=info['span1'],
                ichimoku_span2=info['span2'],
                ma60=info['ma60'],
                ma112=info['ma112'],
                ma224=info['ma224'],
                ma448=info['ma448'],
                avg_trade_value_5d=info['avg_tv_5d'],
                max_close_vs_open_pct_5d=max_cv_pct,
                score=score,
                conditions_met=conditions_met,
                degraded=info.get('degraded', False),
            )

        # 점수순 정렬 후 상위 N개
        if len(results) > self.max_candidates:
            sorted_codes = sorted(
                results.keys(), key=lambda c: results[c].score, reverse=True
            )
            results = {c: results[c] for c in sorted_codes[:self.max_candidates]}

        return results

    @staticmethod
    def _calc_ymgp_score(
        price: float,
        bb20_upper: float,
        bb40_upper: float,
        avg_trade_value: float,
        max_cv_pct: float,
    ) -> float:
        """
        역매공파 종합 점수 (0~100)

        - BB 돌파 강도: 40점 (종가가 상한선을 얼마나 넘었나)
        - 거래대금: 30점 (30억~100억 스케일)
        - 시가대비종가 상승폭: 30점 (7~20% 스케일)
        """
        score = 0.0

        # BB 돌파 강도 (40점)
        if bb20_upper > 0:
            bb20_excess = (price - bb20_upper) / bb20_upper * 100
            score += min(max(bb20_excess, 0) * 4, 20)
        if bb40_upper > 0:
            bb40_excess = (price - bb40_upper) / bb40_upper * 100
            score += min(max(bb40_excess, 0) * 4, 20)

        # 거래대금 (30점) — 30억=0점, 100억+=30점
        tv_billions = avg_trade_value / 1_000_000_000
        tv_score = min((tv_billions - 3) / 7 * 30, 30)
        score += max(tv_score, 0)

        # 시가대비종가 (30점) — 7%=0점, 20%+=30점
        cv_score = min((max_cv_pct - 7) / 13 * 30, 30)
        score += max(cv_score, 0)

        return round(score, 2)
