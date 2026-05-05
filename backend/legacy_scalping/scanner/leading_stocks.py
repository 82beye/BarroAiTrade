"""
당일 주도주 실시간 순위 분석기

OHLCV 캐시 + 실시간 현재가를 결합하여
당일 상승률, 거래대금, 거래량 급증률 기반 주도주 순위를 산출

최적화:
  - 캐시 기반 2단계 사전 필터 (거래대금 + 상승 잠재력 스코어)
  - 이전 결과 캐시 (탈락 종목 재스캔 주기 동안 스킵)
  - 조기 중단 (충분한 후보 확보 시 나머지 스킵)

사용:
    python main.py --leading-stocks          # 실시간 주도주 순위 조회
    python main.py --leading-stocks --top 30 # 상위 30종목
"""

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

import numpy as np
import pandas as pd

from scanner.ohlcv_cache import OHLCVCache

logger = logging.getLogger(__name__)


@dataclass
class LeadingStockResult:
    """주도주 분석 결과"""
    rank: int
    code: str
    name: str
    current_price: int
    prev_close: int
    change_pct: float           # 전일 대비 등락률 (%)
    open_to_cur_pct: float      # 시가 대비 현재가 (%)
    trade_value: float           # 당일 거래대금 (원)
    trade_value_ratio: float     # 거래대금 vs 20일 평균 배수
    volume_ratio: float          # 거래량 vs 20일 평균 배수
    score: float                 # 주도주 종합 점수
    category: str = ""           # 주도 유형 (급등주/거래폭증/대량매집 등)
    open_price: int = 0          # 시가 (API 조회 시 저장)
    high_price: int = 0          # 고가
    low_price: int = 0           # 저가
    volume: int = 0              # 거래량


class LeadingStocksAnalyzer:
    """당일 주도주 실시간 분석기"""

    def __init__(self, kiwoom_api, config: dict):
        self.api = kiwoom_api
        self.config = config

        ls_config = config.get('leading_stocks', {})
        self.top_n = ls_config.get('top_n', 20)
        self.min_trade_value = ls_config.get('min_trade_value', 1_000_000_000)  # 10억
        self.min_change_pct = ls_config.get('min_change_pct', 2.0)
        self.volume_avg_period = ls_config.get('volume_avg_period', 20)

        # API 호출 상한 (1회 스캔당)
        self.max_api_calls = ls_config.get('max_api_calls', 200)

        # 점수 가중치
        weights = ls_config.get('score_weights', {})
        self.w_change_pct = weights.get('change_pct', 30)
        self.w_trade_value = weights.get('trade_value', 30)
        self.w_volume_ratio = weights.get('volume_ratio', 20)
        self.w_open_strength = weights.get('open_strength', 20)

        cache_dir = config.get('scanner', {}).get('cache_dir', './data/ohlcv_cache')
        self.cache = OHLCVCache(cache_dir)

        # 이전 결과 캐시 (재스캔 시 우선 조회)
        self._prev_top_codes: Set[str] = set()
        self._prev_reject_codes: Set[str] = set()
        self._prev_reject_time: float = 0
        self._reject_ttl_seconds = 180  # 탈락 종목 3분간 재조회 안 함

    async def analyze(
        self,
        universe: List[dict],
        top_n: Optional[int] = None,
    ) -> List[LeadingStockResult]:
        """
        주도주 분석 실행

        Args:
            universe: [{"code": "005930", "name": "삼성전자", ...}, ...]
            top_n: 상위 N개 반환 (None이면 설정값 사용)

        Returns:
            점수 내림차순 LeadingStockResult 리스트
        """
        if top_n is None:
            top_n = self.top_n

        start_time = _time.time()
        logger.info(f"주도주 분석 시작 (유니버스: {len(universe)}종목)")

        # Pass 1: 캐시 기반 사전 필터 (거래대금 + 상승 잠재력)
        candidates = self._prefilter(universe)
        logger.info(f"주도주 사전 필터 통과: {len(candidates)}종목")

        # Pass 2: 상승 잠재력 스코어 기반 정렬 + 상한 적용
        candidates = self._rank_and_limit(candidates)
        logger.info(f"주도주 API 조회 대상: {len(candidates)}종목")

        # Pass 3: 실시간 가격 조회 + 점수 산출
        results = await self._score_candidates(candidates, top_n)

        elapsed = _time.time() - start_time
        logger.info(
            f"주도주 점수 산출 완료: {len(results)}종목 "
            f"({elapsed:.1f}초)")

        # 점수 내림차순 정렬 + 순위 부여
        results.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(results[:top_n], 1):
            r.rank = i

        # 이전 결과 캐시 업데이트
        self._update_prev_cache(results, top_n)

        return results[:top_n]

    async def analyze_fast(
        self,
        top_n: Optional[int] = None,
        qry_tp: str = "2",
    ) -> List[LeadingStockResult]:
        """
        ka00198 기반 초고속 주도주 분석 (API 1~2회로 완료)

        기존 analyze()가 200회 개별 API 호출 (168초)이 소요되는 반면,
        ka00198은 단일 호출로 거래량/상승률 상위 종목을 한 번에 반환.

        Args:
            top_n: 상위 N개 반환
            qry_tp: "1"=1분, "2"=10분, "3"=1시간, "4"=당일누적, "5"=30초

        Returns:
            점수 내림차순 LeadingStockResult 리스트
        """
        if top_n is None:
            top_n = self.top_n

        start_time = _time.time()
        logger.info(f"주도주 초고속 분석 시작 (ka00198, qry_tp={qry_tp})")

        results = []

        # KOSPI + KOSDAQ 동시 조회
        for market in ("0", "10"):
            try:
                ranked = await self.api.get_realtime_stock_rank(
                    market=market, qry_tp=qry_tp)
            except Exception as e:
                logger.warning(f"ka00198 조회 실패 (market={market}): {e}")
                continue

            for item in ranked:
                code = item['code']
                cur_price = item['price']
                prev_close = item.get('prev_close', 0)
                cur_volume = item.get('volume', 0)
                cur_open = item.get('open', 0)
                trade_value = item.get('trade_value', 0)

                # prev_close 보정: API에 없으면 캐시에서 로드
                if prev_close <= 0:
                    df = self.cache.load(code)
                    if df is not None and len(df) >= 1:
                        prev_close = int(df['close'].iloc[-1])
                    else:
                        continue

                if prev_close <= 0:
                    continue

                change_pct = (cur_price - prev_close) / prev_close * 100

                # 최소 상승률 필터
                if change_pct < self.min_change_pct:
                    continue

                # 거래대금 추정 (API에 없으면 price×volume)
                if trade_value <= 0:
                    trade_value = cur_price * cur_volume

                # 최소 거래대금 필터
                if trade_value < self.min_trade_value:
                    continue

                # 20일 평균 데이터 (캐시 기반)
                avg_tv_20d = 0
                avg_vol_20d = 0
                df = self.cache.load(code)
                if df is not None and len(df) >= self.volume_avg_period:
                    close = df['close']
                    volume = df['volume']
                    tv_series = close * volume
                    avg_tv_20d = float(
                        tv_series.iloc[-self.volume_avg_period:].mean())
                    avg_vol_20d = float(
                        volume.iloc[-self.volume_avg_period:].mean())

                trade_value_ratio = (
                    trade_value / avg_tv_20d if avg_tv_20d > 0 else 0)
                volume_ratio = (
                    cur_volume / avg_vol_20d if avg_vol_20d > 0 else 0)

                open_to_cur_pct = (
                    (cur_price - cur_open) / cur_open * 100
                    if cur_open > 0 else 0)

                score = self._calc_score(
                    change_pct, trade_value, trade_value_ratio,
                    volume_ratio, open_to_cur_pct)

                category = self._classify(
                    change_pct, volume_ratio, trade_value_ratio)

                results.append(LeadingStockResult(
                    rank=0,
                    code=code,
                    name=item.get('name', ''),
                    current_price=cur_price,
                    prev_close=prev_close,
                    change_pct=round(change_pct, 2),
                    open_to_cur_pct=round(open_to_cur_pct, 2),
                    trade_value=trade_value,
                    trade_value_ratio=round(trade_value_ratio, 2),
                    volume_ratio=round(volume_ratio, 2),
                    score=round(score, 2),
                    category=category,
                    open_price=cur_open,
                    high_price=item.get('high', cur_price),
                    low_price=item.get('low', cur_price),
                    volume=cur_volume,
                ))

        # 중복 제거 (KOSPI/KOSDAQ 양쪽 등장 가능)
        seen = set()
        unique_results = []
        for r in results:
            if r.code not in seen:
                seen.add(r.code)
                unique_results.append(r)
        results = unique_results

        # 점수 내림차순 정렬 + 순위 부여
        results.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(results[:top_n], 1):
            r.rank = i

        # 이전 결과 캐시 업데이트
        self._update_prev_cache(results, top_n)

        elapsed = _time.time() - start_time
        logger.info(
            f"주도주 초고속 분석 완료: {len(results)}종목 → "
            f"상위 {min(len(results), top_n)}종목 ({elapsed:.1f}초)")

        return results[:top_n]

    def _prefilter(self, universe: List[dict]) -> List[dict]:
        """
        캐시 기반 사전 필터링

        - 20일 평균 거래대금 >= min_trade_value (기존 50% → 100%로 강화)
        - 전일 종가/거래량/거래대금 데이터 확보
        - 캐시 기반 상승 잠재력 사전 스코어 계산
        """
        candidates = []

        for stock in universe:
            code = stock['code']
            df = self.cache.load(code)
            if df is None or len(df) < self.volume_avg_period:
                continue

            close = df['close']
            volume = df['volume']

            # 20일 평균 거래대금 (기존 50%에서 100%로 강화)
            trade_value_series = close * volume
            avg_tv = trade_value_series.iloc[-self.volume_avg_period:].mean()
            if pd.isna(avg_tv) or avg_tv < self.min_trade_value:
                continue

            # 20일 평균 거래량
            avg_vol = volume.iloc[-self.volume_avg_period:].mean()

            # 상승 잠재력 사전 스코어 (캐시 기반, API 미호출)
            potential = self._calc_potential_score(df)

            candidates.append({
                **stock,
                'prev_close': int(close.iloc[-1]),
                'avg_trade_value_20d': avg_tv,
                'avg_volume_20d': avg_vol,
                'potential_score': potential,
            })

        return candidates

    def _calc_potential_score(self, df: pd.DataFrame) -> float:
        """
        캐시 기반 상승 잠재력 스코어 (0~100)

        API 호출 없이 OHLCV 캐시만으로 빠르게 산출.
        점수가 높은 종목을 우선 API 조회하여 스캔 속도 향상.

        항목:
          - 최근 5일 거래량 증가 추세 (30점)
          - 최근 5일 양봉 비율 (20점)
          - 최근 5일 평균 등락률 (30점)
          - 전일 거래대금 급증 (20점)
        """
        n = len(df)
        if n < 6:
            return 0.0

        close = df['close'].values
        open_vals = df['open'].values
        volume = df['volume'].values
        score = 0.0

        # 1. 거래량 증가 추세 (30점)
        vol_5d = volume[-5:].astype(float)
        vol_prev5d = volume[-10:-5].astype(float) if n >= 10 else vol_5d
        avg_recent = float(np.mean(vol_5d))
        avg_prev = float(np.mean(vol_prev5d))
        if avg_prev > 0:
            vol_growth = avg_recent / avg_prev
            score += min(max((vol_growth - 1) * 30, 0), 30)

        # 2. 양봉 비율 (20점)
        bullish = sum(1 for i in range(-5, 0) if close[i] > open_vals[i])
        score += bullish / 5 * 20

        # 3. 평균 등락률 (30점)
        if n >= 6:
            daily_returns = (close[-5:] - close[-6:-1]) / close[-6:-1] * 100
            avg_return = float(np.mean(daily_returns))
            score += min(max(avg_return * 5, 0), 30)

        # 4. 전일 거래대금 급증 (20점)
        if n >= 2:
            tv_last = close[-1] * volume[-1]
            tv_prev = close[-2] * volume[-2]
            if tv_prev > 0:
                tv_ratio = tv_last / tv_prev
                score += min(max((tv_ratio - 1) * 10, 0), 20)

        return round(score, 1)

    def _rank_and_limit(self, candidates: List[dict]) -> List[dict]:
        """
        상승 잠재력 스코어 기준 정렬 후 API 호출 상한 적용

        이전 스캔에서 상위였던 종목을 우선 배치하여
        연속 모니터링 안정성 확보.
        """
        # 이전 상위 종목 우선 + 잠재력 점수 기준 정렬
        def sort_key(c):
            is_prev_top = c['code'] in self._prev_top_codes
            return (is_prev_top, c['potential_score'])

        candidates.sort(key=sort_key, reverse=True)

        # 탈락 캐시가 유효하면 탈락 종목 후순위 배치
        now = _time.time()
        if (now - self._prev_reject_time) < self._reject_ttl_seconds:
            # 탈락 종목을 뒤로 밀기 (완전 제거는 안 함 — 시장 변동 가능)
            priority = [
                c for c in candidates
                if c['code'] not in self._prev_reject_codes]
            deferred = [
                c for c in candidates
                if c['code'] in self._prev_reject_codes]
            candidates = priority + deferred

        return candidates[:self.max_api_calls]

    async def _score_candidates(
        self,
        candidates: List[dict],
        top_n: int,
    ) -> List[LeadingStockResult]:
        """
        실시간 가격 조회 + 주도주 점수 산출

        조기 중단: 충분한 고품질 후보가 확보되면 나머지 스킵
        """
        results = []
        api_calls = 0
        consecutive_errors = 0
        # 조기 중단 임계: top_n의 3배 결과가 확보되면 중단
        early_stop_count = top_n * 3

        for cand in candidates:
            code = cand['code']

            # API 한도 초과 시 즉시 중단
            if self.api.is_quota_exhausted('ka10001'):
                logger.warning(
                    f"주도주 스캔 중단: ka10001 한도 초과 "
                    f"(API {api_calls}회, 결과 {len(results)}종목)")
                break

            try:
                price_data = await self.api.get_current_price(code)
                api_calls += 1
            except Exception as e:
                logger.debug(f"주도주 가격 조회 실패 [{code}]: {e}")
                api_calls += 1
                consecutive_errors += 1
                # 연속 5회 실패 시 중단
                if consecutive_errors >= 5:
                    logger.warning(
                        f"주도주 스캔 중단: 연속 {consecutive_errors}회 조회 실패 "
                        f"(API {api_calls}회)")
                    break
                continue

            cur_price = price_data.get('price', 0)
            cur_open = price_data.get('open', 0)
            cur_volume = price_data.get('volume', 0)

            # API 에러 응답 감지 (return_code가 포함된 경우)
            if cur_price <= 0 or cur_open <= 0:
                consecutive_errors += 1
                if consecutive_errors >= 5:
                    logger.warning(
                        f"주도주 스캔 중단: 연속 {consecutive_errors}회 가격 0 "
                        f"(API 한도 초과 의심, {api_calls}회)")
                    break
                continue

            consecutive_errors = 0  # 성공 시 리셋

            prev_close = cand['prev_close']
            if prev_close <= 0:
                continue

            # 지표 계산
            change_pct = (cur_price - prev_close) / prev_close * 100
            open_to_cur_pct = (cur_price - cur_open) / cur_open * 100
            trade_value = cur_price * cur_volume
            avg_tv_20d = cand['avg_trade_value_20d']
            avg_vol_20d = cand['avg_volume_20d']

            trade_value_ratio = trade_value / avg_tv_20d if avg_tv_20d > 0 else 0
            volume_ratio = cur_volume / avg_vol_20d if avg_vol_20d > 0 else 0

            # 최소 상승률 필터
            if change_pct < self.min_change_pct:
                continue

            # 최소 거래대금 필터
            if trade_value < self.min_trade_value:
                continue

            # 종합 점수 계산
            score = self._calc_score(
                change_pct, trade_value, trade_value_ratio,
                volume_ratio, open_to_cur_pct,
            )

            # 주도 유형 분류
            category = self._classify(
                change_pct, volume_ratio, trade_value_ratio,
            )

            results.append(LeadingStockResult(
                rank=0,
                code=code,
                name=cand.get('name', ''),
                current_price=cur_price,
                prev_close=prev_close,
                change_pct=round(change_pct, 2),
                open_to_cur_pct=round(open_to_cur_pct, 2),
                trade_value=trade_value,
                trade_value_ratio=round(trade_value_ratio, 2),
                volume_ratio=round(volume_ratio, 2),
                score=round(score, 2),
                category=category,
                open_price=cur_open,
                high_price=price_data.get('high', cur_price),
                low_price=price_data.get('low', cur_price),
                volume=cur_volume,
            ))

            # 조기 중단: 충분한 결과 확보
            if len(results) >= early_stop_count:
                logger.info(
                    f"주도주 조기 중단: {len(results)}종목 확보 "
                    f"(API {api_calls}회/{len(candidates)}종목)")
                break

        return results

    def _update_prev_cache(
        self,
        results: List[LeadingStockResult],
        top_n: int,
    ):
        """이전 결과 캐시 업데이트"""
        result_codes = {r.code for r in results}
        self._prev_top_codes = {r.code for r in results[:top_n]}
        # 탈락 = API 조회했지만 상위 top_n에 못 든 종목
        self._prev_reject_codes = result_codes - self._prev_top_codes
        self._prev_reject_time = _time.time()

    def _calc_score(
        self,
        change_pct: float,
        trade_value: float,
        trade_value_ratio: float,
        volume_ratio: float,
        open_to_cur_pct: float,
    ) -> float:
        """
        주도주 종합 점수 (0~100)

        - 상승률 (30점): 2%=0점, 15%+=30점
        - 거래대금 (30점): 10억=0점, 100억+=30점
        - 거래량 급증 (20점): 1x=0점, 10x+=20점
        - 시가대비강도 (20점): 0%=0점, 10%+=20점
        """
        # 상승률 점수
        s_change = min(max((change_pct - 2) / 13 * self.w_change_pct, 0), self.w_change_pct)

        # 거래대금 점수
        tv_billions = trade_value / 1_000_000_000
        s_tv = min(max((tv_billions - 1) / 9 * self.w_trade_value, 0), self.w_trade_value)

        # 거래량 급증 점수
        s_vol = min(max((volume_ratio - 1) / 9 * self.w_volume_ratio, 0), self.w_volume_ratio)

        # 시가대비 강도 점수
        s_open = min(max(open_to_cur_pct / 10 * self.w_open_strength, 0), self.w_open_strength)

        return s_change + s_tv + s_vol + s_open

    @staticmethod
    def _classify(
        change_pct: float,
        volume_ratio: float,
        trade_value_ratio: float,
    ) -> str:
        """주도 유형 분류"""
        if change_pct >= 15:
            return "급등주"
        if volume_ratio >= 5 and change_pct >= 5:
            return "거래폭증"
        if trade_value_ratio >= 3 and change_pct >= 3:
            return "대량매집"
        if change_pct >= 8:
            return "강세주"
        return "상승주"
