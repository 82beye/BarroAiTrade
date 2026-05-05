"""
일일 종목 스캐너
장 시작 전(08:30) 전종목을 스캔하여 당일 매매 감시 리스트를 생성
"""

import json
import logging
from datetime import datetime, date
from typing import List, Optional
from dataclasses import asdict

import numpy as np

from scanner.indicators import (
    IndicatorConfig,
    IndicatorResult,
    analyze_stock,
)
from scanner.ohlcv_cache import OHLCVCache
from scanner.market_condition import MarketConditionAnalyzer, MarketCondition

logger = logging.getLogger(__name__)


class _NumpyEncoder(json.JSONEncoder):
    """numpy 타입을 Python 네이티브 타입으로 변환하는 JSON 인코더"""

    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


class DailyScreener:
    """당일 매매 종목 스캐너"""
    
    def __init__(self, kiwoom_api, config: dict):
        """
        Args:
            kiwoom_api: KiwoomRestAPI 인스턴스
            config: settings.yaml 파싱 결과
        """
        self.api = kiwoom_api
        self.scanner_config = config.get('scanner', {})
        self.indicator_config = IndicatorConfig(
            blue_lookback=config['indicators']['blue_dotted_line']['lookback_period'],
            blue_atr_period=config['indicators']['blue_dotted_line']['atr_period'],
            blue_multiplier=config['indicators']['blue_dotted_line']['multiplier'],
            wm_vol_avg_period=config['indicators']['watermelon']['volume_avg_period'],
            wm_vol_spike_ratio=config['indicators']['watermelon']['volume_spike_ratio'],
            wm_atr_period=config['indicators']['watermelon']['atr_period'],
            wm_price_move_ratio=config['indicators']['watermelon']['price_move_ratio'],
            wm_ma224_buffer=config['indicators']['watermelon']['ma224_buffer'],
        )
        self.max_watchlist = self.scanner_config.get('max_watchlist', 20)

        # OHLCV 캐시
        cache_dir = self.scanner_config.get('cache_dir', './data/ohlcv_cache')
        self.cache = OHLCVCache(cache_dir)

        # 시장 상태 분석기
        self.market_analyzer = MarketConditionAnalyzer(config)
        self.last_market_condition: Optional[MarketCondition] = None
    
    async def run_scan(self) -> List[IndicatorResult]:
        """
        전종목 스캔 실행
        
        Returns:
            score 내림차순 정렬된 감시 리스트
        """
        logger.info("=" * 60)
        logger.info(f"일일 스캔 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)
        
        # 1. 전종목 코드 수집
        all_codes = await self._get_all_stock_codes()
        logger.info(f"전체 종목 수: {len(all_codes)}")
        
        # 2. 기본 필터링 (가격, 거래량, 종목유형)
        filtered_codes = await self._apply_basic_filters(all_codes)
        self.filtered_codes = filtered_codes  # 역매공파 검색기 유니버스용 노출
        logger.info(f"기본 필터 통과: {len(filtered_codes)}")
        
        # 3. 시장 상태 분석 (ATR 기반)
        self.last_market_condition = await self.market_analyzer.analyze(self.api)
        level = self.last_market_condition.overall_level.value
        logger.info(f"시장 상태: {level} | 매수허용: {self.last_market_condition.entry_allowed}")
        
        # 4. 지표 계산 및 종목 분석
        results = []
        fail_count = 0
        cache_used = self.cache.is_recent(max_days=3)
        if not cache_used:
            logger.warning("OHLCV 캐시 없음/만료 → API 직접 조회 (속도 저하 예상)")
        for i, stock in enumerate(filtered_codes):
            code, name = stock['code'], stock['name']
            if i % 100 == 0:
                logger.info(f"분석 진행: {i}/{len(filtered_codes)} (실패: {fail_count})")

            try:
                result = await self._analyze_single_stock(code, name)
                if result and result.blue_line_status in ("near", "above"):
                    results.append(result)
            except Exception as e:
                fail_count += 1
                logger.debug(f"종목 분석 실패 [{code}]: {e}")
                continue

        if fail_count > 0:
            fail_pct = fail_count / len(filtered_codes) * 100
            logger.warning(f"종목 분석 실패: {fail_count}/{len(filtered_codes)} ({fail_pct:.1f}%)")
        logger.info(f"파란점선 근접/돌파 종목: {len(results)}")
        
        # 5. 점수 기준 정렬 및 상위 N개 선택
        results.sort(key=lambda x: x.score, reverse=True)
        watchlist = results[:self.max_watchlist]
        
        # 6. 결과 저장
        self._save_watchlist(watchlist)
        
        logger.info(f"최종 감시 리스트: {len(watchlist)}종목")
        for r in watchlist:
            logger.info(
                f"  [{r.code}] {r.name} | "
                f"종가:{r.close:,.0f} | 파란점선:{r.blue_line:,.0f} | "
                f"상태:{r.blue_line_status} | "
                f"수박:{r.watermelon_signal} | "
                f"점수:{r.score}"
            )
        
        return watchlist
    
    async def _get_all_stock_codes(self) -> List[dict]:
        """
        전종목 코드/이름/메타정보 수집
        키움 REST API: POST /api/dostk/stkinfo (ka10099)
        """
        codes = []
        markets = self.scanner_config.get('markets', ['KOSPI', 'KOSDAQ'])

        for market in markets:
            market_code = "0" if market == "KOSPI" else "10"
            try:
                stock_list = await self.api.get_stock_list_with_meta(market_code)
                codes.extend(stock_list)
            except Exception as e:
                logger.error(f"{market} 종목 코드 수집 실패: {e}")

        return codes
    
    async def _apply_basic_filters(self, codes: List[dict]) -> List[dict]:
        """
        기본 필터 적용
        - ETF/ETN/SPAC 이름 패턴 제외
        - ka10099 메타필드: 관리종목/거래정지/투자경고/비적정감사 제외
        """
        exclude = self.scanner_config.get('exclude', {})

        filtered = []
        for stock in codes:
            name = stock.get('name', '')
            state = stock.get('state', '')
            order_warning = stock.get('orderWarning', '')
            audit_info = stock.get('auditInfo', '')

            # ETF/ETN/SPAC 이름 패턴으로 필터링
            if exclude.get('exclude_etf') and self._is_etf(name):
                continue
            if exclude.get('exclude_spac') and self._is_spac(name):
                continue

            # 관리종목/거래정지 제외 (ka10099 state 필드)
            if exclude.get('exclude_managed') and '관리' in state:
                continue
            if exclude.get('exclude_suspended') and '거래정지' in state:
                continue

            # 투자경고/위험 종목 제외 (orderWarning: "0"=정상)
            if order_warning not in ('0', ''):
                continue

            # 비적정 감사의견 제외
            if audit_info and audit_info not in ('정상', '적정', ''):
                continue

            filtered.append(stock)

        return filtered
    
    async def _analyze_single_stock(self, code: str, name: str) -> Optional[IndicatorResult]:
        """단일 종목 지표 분석"""
        # 캐시가 최근 3일 이내이면 캐시에서 로드, 아니면 API 조회
        df = None
        if self.cache.is_recent(max_days=3):
            df = self.cache.load(code)

        if df is None:
            df = await self.api.get_daily_ohlcv(code, count=300)
        
        if df is None or len(df) < self.indicator_config.blue_lookback:
            return None
        
        # 기본 필터 (가격/거래량)
        exclude = self.scanner_config.get('exclude', {})
        last_close = df['close'].iloc[-1]
        avg_volume = df['volume'].tail(20).mean()
        
        if last_close < exclude.get('min_price', 1000):
            return None
        max_price = exclude.get('max_price', 0)
        if max_price > 0 and last_close > max_price:
            return None
        if avg_volume < exclude.get('min_volume', 100000):
            return None
        
        return analyze_stock(code, name, df, self.indicator_config)
    
    def _save_watchlist(self, watchlist: List[IndicatorResult]):
        """감시 리스트를 JSON 파일로 저장"""
        today = date.today().isoformat()
        filepath = f"./logs/watchlist_{today}.json"
        
        data = {
            "date": today,
            "generated_at": datetime.now().isoformat(),
            "count": len(watchlist),
            "stocks": [asdict(r) for r in watchlist],
        }
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2, cls=_NumpyEncoder)
            logger.info(f"감시 리스트 저장: {filepath}")
        except Exception as e:
            logger.error(f"감시 리스트 저장 실패: {e}")
    
    @staticmethod
    def _is_spac(name: str) -> bool:
        """SPAC 종목명 패턴 확인"""
        name_upper = name.upper()
        if 'SPAC' in name_upper:
            return True
        # 한글 스팩 패턴: "XX제N호스팩", "XX스팩N호", "XXN호스팩"
        if '스팩' in name:
            return True
        return False

    @staticmethod
    def _is_etf(name: str) -> bool:
        """ETF/ETN 종목명 패턴 확인"""
        etf_keywords = [
            'ETF', 'ETN', 'KODEX', 'TIGER', 'KBSTAR', 'ARIRANG',
            'SOL', 'ACE', 'HANARO', 'KOSEF', 'TREX', 'TIMEFOLIO',
            'PLUS', 'FOCUS', 'BNK',
            # 2025~2026 신규 ETF 브랜드
            'RISE', 'WON', '1Q', 'KIWOOM', 'WOORI',
            # 채권/머니마켓/선물 ETF 키워드
            '액티브', '레버리지', '인버스', '선물',
            '머니마켓', '국공채', '회사채', '금리', '단기',
            'CD금리', 'SOFR', 'KOFR', 'TDF',
        ]
        name_upper = name.upper()
        return any(kw in name_upper for kw in etf_keywords)


def load_watchlist(date_str: Optional[str] = None) -> List[dict]:
    """
    저장된 감시 리스트 로드
    
    Args:
        date_str: 날짜 문자열 (None이면 오늘)
    """
    if date_str is None:
        date_str = date.today().isoformat()
    
    filepath = f"./logs/watchlist_{date_str}.json"
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('stocks', [])
    except FileNotFoundError:
        logger.error(f"감시 리스트 없음: {filepath}")
        return []
