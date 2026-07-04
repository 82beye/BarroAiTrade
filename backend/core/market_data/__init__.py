"""티마 벤치마크 대시보드용 읽기 전용 시세 데이터 레이어.

주문/매매 경로와 완전히 분리된 조회 전용 모듈 모음.
- cache_quotes: OHLCV 일봉 캐시(data/ohlcv_cache) 기반 지연 시세 폴백
- stock_names: 종목코드 → 종목명 마스터 로더
"""
